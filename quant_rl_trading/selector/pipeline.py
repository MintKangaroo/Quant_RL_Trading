"""여섯 단계를 창고에 붙여 한 번에 돌린다.

Session 이 부르는 진입점이다. 단계별 로직은 각 모듈에 있고 여기서는 **순서만**
지킨다 — 순서가 곧 설계이므로 한 곳에서 읽히게 둔다 (selector.md §5).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

import pandas as pd

from quant_rl_trading.selector import candidates as candidates_module
from quant_rl_trading.selector import constraints as constraints_module
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
            #
            # **어느 쪽이었는지까지 적는다.** "확인할 것" 은 사람에게 숙제를
            # 넘기는 것이고, 그 답은 여기서 이미 손에 있다. 실제로 2026-08-12
            # 세션이 이 자리에서 멈췄는데 — 신호 13,746행이 전부 confidence 0
            # 이었다(롤링 IC 이력이 그날까지 관측된 적이 없다) — 문구가
            # 가리키지 않아 원인을 찾는 데 시간이 들었다.
            trace.note(_silent_score_reason(signals, weights))
        return Selection(as_of, market, (), weights, trace)

    # 2-b. 위험 하한 — **제약이지 점수가 아니다** (태스크 #32).
    #      `risk` 는 위 합성에서 이미 빠져 있다(constraints.CONSTRAINT_ANALYSTS).
    #      여기서 다시 등장하는 이유는 **자리를 옮긴 것이지 버린 것이 아니기**
    #      때문이다 — 알파로 쓰면 저변동이 고수익 신호로 둔갑하지만, 꼬리를
    #      자르는 데는 그대로 쓸모가 있다.
    #
    #      거부(3번)보다 먼저다. 거부는 LLM 판정이라 비싸고 상한이 걸려 있는데,
    #      여기서 잘릴 종목까지 판정 정원을 쓸 이유가 없다.
    scores = constraints_module.apply_risk_floor(
        scores,
        signals=signals,
        params=constraints_module.ConstraintParams.from_store(store, as_of=as_of),
        trace=trace,
    )
    trace.stage("risk_floor", len(scores))
    if scores.empty:
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


def _silent_score_reason(signals: pd.DataFrame, weights: Mapping[str, float]) -> str:
    """합성 점수가 0건인 이유를 **범인까지 지목해서** 돌려준다.

    ``combined_scores`` 는 ``weight × confidence`` 가 0 인 행을 버린다. 그래서
    0건이 되는 길은 셋뿐이고, 셋은 서로 완전히 다른 사건이다:

    - **confidence 가 전원 0** — 롤링 IC 이력이 아직 없다. 시간이 지나면
      저절로 풀린다. 2026-08-12 가 이 경우였다
    - **가중치가 전원 0** — IC 를 통과한 Analyst 가 하나도 없다. 저절로
      풀리지 않는다. 피처로 돌아가야 한다
    - **점수를 낸 Analyst 와 가중치를 받은 Analyst 가 안 겹친다** — 배선
      사고다. 가장 조용하고 가장 나쁘다

    셋을 "확인할 것" 으로 뭉뚱그리면 매번 같은 조사를 처음부터 다시 한다.
    """
    scored = set(signals["analyst"].astype(str).unique())
    weighted = {name for name, value in weights.items() if abs(float(value)) > 0.0}
    max_confidence = float(signals["confidence"].astype(float).max())
    head = f"신호 {len(signals)}행이 있는데 합성 점수가 0건이다"

    if not weighted:
        return (
            f"{head} — **가중치를 받은 Analyst 가 없다**. IC 를 통과한 것이 "
            "하나도 없다는 뜻이고, 기다려서 풀리지 않는다"
        )
    if not (scored & weighted):
        return (
            f"{head} — **점수를 낸 Analyst 와 가중치를 받은 Analyst 가 "
            f"안 겹친다.** 점수 {sorted(scored)} · 가중치 {sorted(weighted)}. "
            "배선 사고다"
        )
    if max_confidence <= 0.0:
        return (
            f"{head} — **confidence 가 전원 0 이다**(최대 {max_confidence:.3f}). "
            "롤링 IC 이력이 아직 없다는 뜻이다 (analysts/ic.py "
            "NO_EVIDENCE_CONFIDENCE). 이력이 쌓이면 풀린다"
        )
    return (
        f"{head} — 가중치도 confidence 도 0 이 아닌데 비었다"
        f"(최대 confidence {max_confidence:.3f}). combine.combined_scores 를 볼 것"
    )
