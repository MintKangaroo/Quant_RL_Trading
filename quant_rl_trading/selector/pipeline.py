"""여섯 단계를 창고에 붙여 한 번에 돌린다.

Session 이 부르는 진입점이다. 단계별 로직은 각 모듈에 있고 여기서는 **순서만**
지킨다 — 순서가 곧 설계이므로 한 곳에서 읽히게 둔다 (selector.md §5).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from quant_rl_trading.selector import candidates as candidates_module
from quant_rl_trading.selector import filters as filters_module
from quant_rl_trading.selector.candidates import Candidate, SelectionParams, SelectionTrace
from quant_rl_trading.selector.combine import combined_scores
from quant_rl_trading.selector.weights import analyst_weights

if TYPE_CHECKING:
    from quant_rl_trading.store import Store

SIGNALS = "signals"


@dataclass(frozen=True)
class Selection:
    as_of: datetime
    market: str
    candidates: tuple[Candidate, ...]
    weights: dict[str, float]
    trace: SelectionTrace


def run(
    store: Store,
    *,
    as_of: datetime,
    market: str,
    equity: float,
    sectors: Mapping[str, str] | None = None,
) -> Selection:
    """후보 선정 한 번.

    ``sectors`` 는 주입이다. **기본은 None 이고, 그러면 섹터 상한이 안 걸린다.**
    창고에 ``sectors`` 테이블이 있는데도 자동으로 읽지 않는 이유는 그 안의 값이
    업종이 아니라 KOSDAQ 소속부이기 때문이다 — 아래 4~6 단계의 주석에 이유를
    적어 뒀다. 그 사실을 흔적에 남긴다. 조용히 건너뛰면 나중에 "섹터 상한이 왜
    안 걸렸지" 를 아무도 묻지 않게 된다.
    """
    trace = SelectionTrace()
    params = SelectionParams.from_store(store, as_of=as_of)
    filter_params = filters_module.FilterParams.from_store(
        store, as_of=as_of, market=market
    )

    # 0. 입력 점검. **선정 단계가 아니라 전제 확인이다** — 그래서 순서 밖이다.
    #    가중치가 없는 것은 "오늘 살 게 없다" 가 아니라 **설비 고장**이고, 둘을
    #    같은 메시지로 보고하면 고장을 몇 주 동안 못 알아본다.
    weights = analyst_weights(store, as_of=as_of, market=market)
    if not weights:
        trace.note(
            "IC 측정 결과가 없다. 동일가중으로 때우지 않는다 — 관찰 모드 "
            "Analyst 에게 실제 가중치를 주는 것과 같다"
        )
        return Selection(as_of, market, (), {}, trace)

    # 1. 유니버스 필터
    universe = filters_module.tradable_universe(
        store, as_of=as_of, market=market, params=filter_params, equity=equity
    )
    for entity, reason in universe.dropped.items():
        trace.drop(entity, reason)
    trace.stage("universe", len(universe))

    unhealthy = filters_module.distressed(store, as_of=as_of, market=market)
    kept = [entity for entity in universe.kept if entity not in unhealthy]
    for entity in universe.kept:
        if entity in unhealthy:
            trace.drop(entity, "부실 공시(관리종목·불성실공시 등)")
    trace.stage("healthy", len(kept))
    if not kept:
        return Selection(as_of, market, (), weights, trace)

    # 2. 합성 점수
    signals = store.get(SIGNALS, as_of=as_of, entity=kept, lookback=5)
    scores = combined_scores(signals, weights)
    trace.stage("scored", len(scores))
    if scores.empty:
        if not signals.empty:
            # **침묵의 이유를 남긴다.** 신호는 왔는데 점수가 0건이면 원인은
            # 거의 언제나 confidence 다(전원 0 이면 분모가 0). 이유가 없으면
            # "오늘 살 게 없다" 와 구분되지 않아 진단에 반나절이 든다.
            trace.note(
                f"신호 {len(signals)}행이 있는데 합성 점수가 0건이다. "
                "가중치×confidence 가 전부 0 인지 확인할 것 (analysts/ic.py "
                "NO_EVIDENCE_CONFIDENCE)"
            )
        return Selection(as_of, market, (), weights, trace)

    # 3. News·SNS 거부 — 상관보다 **먼저**. 상관 계산은 비싸고, 거부될 종목까지
    #    계산할 이유가 없다.
    rejected = candidates_module.rejected_entities(
        store,
        as_of=as_of,
        universe=list(scores.index),
        cap=params.rejection_cap,
        trace=trace,
    )
    scores = scores[~scores.index.isin(rejected)]
    trace.stage("after_verdicts", len(scores))
    if scores.empty:
        return Selection(as_of, market, (), weights, trace)

    # 4~6. 상관 페널티 · 섹터 상한 · 상위 N
    correlations = candidates_module.correlation_matrix(
        store, as_of=as_of, entities=list(scores.index), market=market
    )
    # **창고의 sectors 를 여기에 붙이지 않는다.** 붙일 수는 있는데, 지금 그
    # 테이블에 들어 있는 값이 업종이 아니라 **KOSDAQ 소속부**다 (우량기업부·
    # 벤처기업부·기술성장기업부…). KRX 일별매매의 SECT_TP_NM 이 그것뿐이고,
    # KOSPI 는 942 종목 전부 빈 문자열이다 (2026-08-14 실측).
    #
    # 이걸로 상한을 걸면 KOSPI 대형주에는 아무 제약이 없고 KOSDAQ 만 시장
    # 등급으로 나뉜다. selector.md §5-5 가 막으려는 것은 **상관된 노출**이지
    # 시장 등급이 아니다. 그러면 상한이 걸리긴 하는데 걸려야 할 곳에 안 걸리고,
    # 화면에는 "섹터 상한 적용됨" 이 뜬다 — 없는 것보다 나쁘다. 분산이 되고
    # 있다는 착시가 생기기 때문이다.
    #
    # 수집은 살려 뒀다(`sectors` 테이블). 진짜 업종 분류(KRX 업종분류현황)를
    # 받는 날 이 자리에 sector_map 을 꽂으면 된다.
    if not sectors:
        trace.note(
            "섹터 상한을 적용하지 않았다. 창고의 sectors 는 업종이 아니라 "
            "KOSDAQ 소속부이고(KOSPI 는 전부 미상), 그걸로 거는 상한은 "
            "상관 분산이 아니다 — 진짜 업종 분류를 받기 전까지 끈다"
        )

    chosen = candidates_module.select(
        scores=scores,
        params=params,
        correlations=correlations if not correlations.empty else None,
        sectors=sectors,
        trace=trace,
    )
    return Selection(as_of, market, tuple(chosen), weights, trace)
