"""Analyst 점수 → ``signals``. 하루의 첫 단계.

라이브의 일일 실행기(`tools/run_daily.py`)와 백테스트 루프가 **같은 함수**를
부른다. 두 벌로 두면 백테스트가 실전과 다른 Analyst 조합을 돌리게 되고, 그
차이는 성적으로만 드러나 원인을 못 찾는다 (불변식 5).

**뉴스·SNS 판정은 여기 없다.** 저쪽은 LLM 을 부르고 과거 뉴스는 창고에 없다.
백테스트에서 돌릴 수 없는 것을 같은 함수에 묶으면, 묶은 쪽이 분기를 만든다.
"""

from __future__ import annotations

import contextlib
import logging
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
from quant_rl_trading.analysts.ranker import RankerAnalyst
from quant_rl_trading.analysts.regime import RegimeAnalyst
from quant_rl_trading.analysts.risk import RiskAnalyst
from quant_rl_trading.analysts.volume import VolumeAnalyst
from quant_rl_trading.collectors.market_hours import Market
from quant_rl_trading.replay.clock import ReplayClock
from quant_rl_trading.store import DuplicateIngestRun
from quant_rl_trading.store.memo import MemoStore

if TYPE_CHECKING:
    from quant_rl_trading.store import Store

logger = logging.getLogger(__name__)

SIGNALS = "signals"
FAILURES = "analyst_failures"

#: 점수를 내는 Analyst. **시장별로 돌릴 수 있는 것이 다르다** — 데이터가 없는
#: Analyst 를 돌리면 나오는 것은 신호가 아니라 빈 프레임이다.
#:
#: **2026-09-01: 미장에 fundamental·event 를 켰다.** 이 명단은 원래 "미장은 재무·
#: 이벤트 데이터가 없다" 는 전제로 만들어졌는데, EDGAR 백필과 문서 수집이 들어온
#: 뒤로 그 전제가 옛말이 됐다. 창고 실측:
#:
#:   fundamentals  US 129,686행 / 4,939종목   (EDGAR)
#:   documents     US  61,383행 / 5,216종목   (event 가 읽는 표)
#:
#: 그리고 어제 미장 IC 가 둘 다 합격선을 넘겼다 — fundamental +0.0500 · event
#: +0.0367 (합격선 0.03). **재려면 데이터가 있어야 하므로, 잴 수 있었다는 것이
#: 곧 데이터가 있다는 증거다.** 데이터가 들어와도 이 명단을 안 고치면 신호가
#: 안 나고, 신호가 없으면 후보에 못 오른다 — 명단이 조용한 관문이었다.
SCORERS: dict[Market, dict[str, type[Analyst]]] = {
    Market.KR: {
        "chart": ChartAnalyst,
        "event": EventAnalyst,
        "flow_kr": FlowKrAnalyst,
        "fundamental": FundamentalAnalyst,
        "regime": RegimeAnalyst,
        "risk": RiskAnalyst,
        "volume": VolumeAnalyst,
        # **맨 마지막.** ranker 는 위 Analyst 들이 이 세션에 창고에 남긴 점수를 읽는다
        # (analysts/ranker.py). 앞에 두면 어제 점수를 읽거나 빈 프레임을 낸다.
        "ranker": RankerAnalyst,
    },
    Market.US: {
        "chart": ChartAnalyst,
        "event": EventAnalyst,
        "flow_us": FlowUsAnalyst,
        "fundamental": FundamentalAnalyst,
        "regime": RegimeAnalyst,
        "risk": RiskAnalyst,
        "volume": VolumeAnalyst,
        "ranker": RankerAnalyst,
    },
}


@dataclass
class ScoringResult:
    written: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    confidence: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    #: **예외로 죽은 Analyst 만.** ``warnings`` 와 갈라 둔 이유가 있다 —
    #: "신호 0건" 과 "MemoryError 로 죽음" 은 성격이 다른데 한 목록에 있으면
    #: 호출부가 문자열을 뜯어봐야 가를 수 있고, 그러면 아무도 안 가른다.
    #:
    #: 실측 2026-08-18~20: event·fundamental·regime 이 세 세션 연속 죽었는데
    #: `run_daily.sh` 는 rc=0 으로 끝났다. 6종 중 3종이 빠진 신호로 후보를
    #: 고르는 동안 크론도 브리핑도 아무 말을 안 했다.
    failures: list[str] = field(default_factory=list)
    #: 창고 조회를 몇 번 아꼈나. 0 이면 캐시가 안 걸린 것이다 — 조용히
    #: 느려지는 것을 알아채는 유일한 표시다.
    cache_hits: int = 0
    cache_misses: int = 0


def run_id_for(table: str, market: Market, moment: datetime, name: str) -> str:
    """결정론적 run id. 같은 세션을 두 번 넣으려 하면 창고가 거부한다."""
    return f"daily-{table}-{market}-{moment:%Y%m%d}-{name}"


def _record_failure(
    store: Store,
    *,
    market: Market,
    as_of: datetime,
    name: str,
    error: BaseException,
    revision: int,
) -> None:
    """Analyst 가 죽었다는 사실을 **창고에** 남긴다.

    ## 왜 로그로 부족한가

    2026-08-20 에 event·fundamental·regime 이 죽었다. 로그에는 남았지만
    창고에는 안 남았고, 같은 날 정정본을 넣자 `signals` 는 그날을 6종으로
    보여줬다 — **정정본이 사고의 흔적을 지웠다.** `verify_m3` 가 그 날을
    잡을 방법이 없어 상수에 손으로 적어야 했다.

    실패는 **일어난 순간** 데이터가 되어야 한다. 나중에 다른 표에서
    추론하면 늦고, 그 추론은 정정 한 번에 무너진다.

    ## 나중에 고쳐도 이 행은 안 지운다

    그날 세션이 반쪽 판단으로 돌았다는 것은 나중에 고쳐도 달라지지 않는
    사실이다. 되살아난 Analyst 는 새 `signals` 행을 쓸 뿐 이 행을 건드리지
    않는다.

    ## 여기서 죽지 않는다

    기록에 실패해도 세션은 계속 간다. 사고를 적다가 사고를 키우면 안 된다 —
    남은 Analyst 들은 여전히 돌아야 하고, 그게 이 함수를 부른 이유다.
    """
    run_id = f"analyst-failure-{market}-{as_of.date().isoformat()}-{name}"
    if revision:
        run_id = f"{run_id}-rev{revision}"
    row = {
        "entity_id": name,
        "valid_from": as_of,
        # 우리가 안 시각도 그 세션이다. 세션이 끝난 뒤에 알게 된 것이 아니라
        # 세션을 돌리다가 겪은 일이다.
        "observed_at": as_of,
        "source": "daily",
        "market": str(market),
        "stage": "score",
        "error_type": type(error).__name__,
        # 원문을 자르지 않는다. MemoryError 의 배열 크기처럼 **숫자 하나가
        # 원인을 특정**하는 경우가 있다 (2026-08-20 에 그랬다).
        "detail": str(error),
    }
    try:
        with contextlib.suppress(DuplicateIngestRun):
            store.append(FAILURES, [row], ingest_run_id=run_id)
    except Exception as write_error:  # noqa: BLE001
        logger.warning("Analyst 실패를 창고에 못 적었다: %s", write_error)


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
            message = f"{name}: {type(error).__name__}: {error}"
            result.warnings.append(message)
            # **실패는 따로도 센다.** 화면에 찍히는 것과 rc 로 나가는 것은
            # 다른 일이다 — 사람이 로그를 안 볼 때 크론이 대신 알아야 한다.
            result.failures.append(message)
            if not dry_run:
                _record_failure(
                    store, market=market, as_of=as_of, name=name,
                    error=error, revision=revision,
                )
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
