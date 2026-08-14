"""Analyst 점수 → ``signals``. 하루의 첫 단계.

라이브의 일일 실행기(`tools/run_daily.py`)와 백테스트 루프가 **같은 함수**를
부른다. 두 벌로 두면 백테스트가 실전과 다른 Analyst 조합을 돌리게 되고, 그
차이는 성적으로만 드러나 원인을 못 찾는다 (불변식 5).

**뉴스·SNS 판정은 여기 없다.** 저쪽은 LLM 을 부르고 과거 뉴스는 창고에 없다.
백테스트에서 돌릴 수 없는 것을 같은 함수에 묶으면, 묶은 쪽이 분기를 만든다.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from quant_rl_trading.analysts import ic
from quant_rl_trading.analysts.base import Analyst
from quant_rl_trading.analysts.chart import ChartAnalyst
from quant_rl_trading.analysts.event import EventAnalyst
from quant_rl_trading.analysts.flow_kr import FlowKrAnalyst
from quant_rl_trading.analysts.flow_us import FlowUsAnalyst
from quant_rl_trading.analysts.fundamental import FundamentalAnalyst
from quant_rl_trading.analysts.regime import RegimeAnalyst
from quant_rl_trading.analysts.risk import RiskAnalyst
from quant_rl_trading.collectors.market_hours import Market
from quant_rl_trading.replay.clock import ReplayClock
from quant_rl_trading.store import DuplicateIngestRun
from quant_rl_trading.store.memo import MemoStore

if TYPE_CHECKING:
    from quant_rl_trading.store import Store

SIGNALS = "signals"

#: 점수를 내는 Analyst. **시장별로 돌릴 수 있는 것이 다르다** — 미장은 재무·
#: 이벤트·수급 데이터가 없어서, 돌려봤자 나오는 것은 신호가 아니라 빈 프레임이다.
SCORERS: dict[Market, dict[str, type[Analyst]]] = {
    Market.KR: {
        "chart": ChartAnalyst,
        "event": EventAnalyst,
        "flow_kr": FlowKrAnalyst,
        "fundamental": FundamentalAnalyst,
        "regime": RegimeAnalyst,
        "risk": RiskAnalyst,
    },
    Market.US: {
        "chart": ChartAnalyst,
        "regime": RegimeAnalyst,
        "risk": RiskAnalyst,
        "flow_us": FlowUsAnalyst,
    },
}


@dataclass
class ScoringResult:
    written: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    confidence: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    #: 창고 조회를 몇 번 아꼈나. 0 이면 캐시가 안 걸린 것이다 — 조용히
    #: 느려지는 것을 알아채는 유일한 표시다.
    cache_hits: int = 0
    cache_misses: int = 0


def run_id_for(table: str, market: Market, moment: datetime, name: str) -> str:
    """결정론적 run id. 같은 세션을 두 번 넣으려 하면 창고가 거부한다."""
    return f"daily-{table}-{market}-{moment:%Y%m%d}-{name}"


def produce(
    store: Store,
    *,
    market: Market,
    as_of: datetime,
    dry_run: bool = False,
    revision: int = 0,
) -> ScoringResult:
    """그 세션의 Analyst 점수를 창고에 남긴다.

    **관찰 모드(IC 미달) Analyst 도 기록한다.** 가중치가 0이라 매매에는 안
    쓰이지만, 기록이 없으면 나중에 좋아졌는지 알 수 없다. 통과 여부는
    ``analyst_weights`` 가 들고 있으므로 여기서 거를 이유가 없다.

    ``confidence`` 는 스스로 매기지 않고 최근 60일 롤링 IC 로 계산해 넣는다
    (agents.md §1). 잴 표본이 없으면 감쇠하지 않는다
    (`ic.NO_EVIDENCE_CONFIDENCE`).

    ``revision`` 을 올리면 **같은 세션의 정정본**을 넣는다. UPDATE 가 아니라
    새 행이고(불변식 4), 조회는 자연키마다 최신 revision 을 고른다. 계산
    규칙이 바뀌어 과거 세션의 값이 틀린 것이 된 경우에 쓴다 — 지어낸 값을
    덮는 용도가 아니다.
    """
    result = ScoringResult()
    clock = ReplayClock(as_of)
    # **여섯이 같은 창을 여섯 번 읽지 않게 한다.** Analyst 마다 prices·universe
    # 를 각자 조회하는데, 같은 as_of·같은 창이라 질의도 같다. 캐시는 이 세션이
    # 쓰고 버린다 (store/memo.py — Store 자체에 붙이면 오래 뜬 프로세스가
    # 낡은 데이터를 계속 보게 된다).
    cached = MemoStore(store)

    for name, factory in SCORERS[market].items():
        run_id = run_id_for(SIGNALS, market, as_of, name)
        if revision:
            run_id = f"{run_id}-rev{revision}"
        if store.ingest_run_recorded(SIGNALS, run_id):
            continue

        analyst = factory(cached, clock, market=market)
        # confidence 를 먼저 구한다. 나중에 구하면 Analyst 를 두 번 돌리게 되고,
        # 그건 국장 6종에 대해 피처 계산을 통째로 두 번 하는 것이다.
        confidence = ic.rolling_confidence(
            cached, analyst=name, as_of=as_of, market=str(market)
        )
        try:
            signals = analyst.run(as_of, confidence=confidence)
        except Exception as error:
            # 하나가 죽어도 나머지는 남긴다. 조용히 넘어가지 않고 경고로 올린다.
            result.warnings.append(f"{name}: {type(error).__name__}: {error}")
            continue

        if not signals:
            # 빈 것을 완료로 기록하면 데이터가 생겨도 영영 건너뛴다.
            result.warnings.append(f"{name}: 신호 0건")
            continue

        result.counts[name] = len(signals)
        result.confidence[name] = confidence
        if dry_run:
            continue
        rows = [signal.row(observed_at=as_of, source="daily") for signal in signals]
        if revision:
            for row in rows:
                row["revision"] = revision
        with contextlib.suppress(DuplicateIngestRun):
            result.written += int(store.append(SIGNALS, rows, ingest_run_id=run_id))
    result.cache_hits = cached.hits
    result.cache_misses = cached.misses
    return result
