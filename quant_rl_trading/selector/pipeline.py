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
from quant_rl_trading.selector import ksic
from quant_rl_trading.selector.candidates import Candidate, SelectionParams, SelectionTrace
from quant_rl_trading.selector.combine import combined_scores
from quant_rl_trading.selector.weights import weight_census

if TYPE_CHECKING:
    from quant_rl_trading.store import Store

SIGNALS = "signals"

#: `sectors` 테이블에서 고를 분류체계. 같은 테이블의 `krx_openapi` 는
#: 업종이 아니라 KOSDAQ 소속부라 상한 축으로 쓸 수 없다.
DART_SECTOR_SOURCE = "dart_company"


@dataclass(frozen=True)
class Selection:
    as_of: datetime
    market: str
    candidates: tuple[Candidate, ...]
    weights: dict[str, float]
    trace: SelectionTrace
    #: 알파 Analyst 가 0종인 사유 코드(`selector.weights` 의 상수). 빈
    #: 문자열이면 정상이다.
    #:
    #: **후보 0개와 다른 사건이다.** 후보가 0개인 것은 "오늘 살 게 없다" 일
    #: 수 있지만, 이 값이 차 있으면 그날 선정은 시작조차 못 한 것이다 —
    #: 세션이 종료코드로 내보내는 근거가 여기다 (tools/run_session.py).
    fault: str = ""


def run(
    store: Store,
    *,
    as_of: datetime,
    market: str,
    equity: float,
    sectors: Mapping[str, str] | None = None,
) -> Selection:
    """후보 선정 한 번.

    ``sectors`` 는 **None 이면 창고에서 읽는다.** 예전에는 주입만 받았고
    기본이 "상한 없음" 이었는데, 그건 그때 창고에 있던 값이 업종이 아니라
    KOSDAQ 소속부였기 때문이다 — 지금은 DART 표준산업분류가 들어와 있다
    (아래 4~6 단계 주석). 호출부(session/daily.py · allocator/cache.py)는
    둘 다 이 인자를 안 넘기므로, 여기서 읽지 않으면 어느 경로에서도 상한이
    안 걸린다.

    명시적으로 넘기면 그것을 쓴다. **빈 dict 를 넘기면 상한을 끄는 뜻**이고,
    그때는 그 사실이 흔적에 남는다. 조용히 건너뛰면 나중에 "섹터 상한이 왜
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
    #
    #    **어느 고장인지까지 적는다.** 예전에는 셋을 "IC 측정 결과가 없다" 로
    #    뭉뚱그렸는데, US 세션이 2026-08 내내 그 문구를 남기는 동안 실제로는
    #    4종이 전부 측정돼 있었다 — 통과한 risk 가 제약 Analyst 라 알파에서
    #    빠진 것이었다(태스크 #12). 처방이 "측정을 돌려라" 와 "알파를 만들어라"
    #    로 정반대인데 화면의 문구가 같았다.
    census = weight_census(store, as_of=as_of, market=market)
    if census.fault:
        trace.note(
            f"알파 Analyst 가 0종이다 — {census.describe()}. 동일가중으로 "
            "때우지 않는다 — 관찰 모드 Analyst 에게 실제 가중치를 주는 것과 같다"
        )
        return Selection(as_of, market, (), {}, trace, fault=census.fault)
    weights = census.alpha_map

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
    # **창고의 sectors 를 읽어 상한을 건다.** 2026-08-14 까지는 이 자리에서
    # 읽지 않았는데, 그때 그 테이블에 있던 값이 업종이 아니라 KOSDAQ
    # 소속부였기 때문이다 (우량기업부·벤처기업부…). KRX 일별매매의
    # SECT_TP_NM 이 그것뿐이었고 KOSPI 는 942종목 전부 빈 문자열이었다.
    # 그걸로 걸면 KOSPI 대형주에는 제약이 없고 KOSDAQ 만 시장 등급으로
    # 나뉘는데, selector.md §5-5 가 막으려는 것은 **상관된 노출**이지 시장
    # 등급이 아니다. 상한은 걸리는데 걸려야 할 곳에 안 걸리고 화면에는
    # "섹터 상한 적용됨" 이 뜬다 — 분산되고 있다는 착시라 없는 것보다 나쁘다.
    #
    # 2026-08-15 에 DART 표준산업분류(KSIC)가 들어왔다 — KR 2,761종목
    # 96.1% 적재(store/tables.py). 그래서 켠다. 다만 KSIC 세세분류는 535개로
    # 갈려 그대로는 상한이 영원히 안 걸리므로 `ksic.roll_up_map` 으로
    # 접는다(왜 그 자릿수인지는 그 모듈의 독스트링).
    #
    # ``source`` 를 고정하는 것이 핵심이다. 같은 테이블에 소속부와 KSIC 가
    # 함께 살고 있어서, 안 고르면 종목마다 다른 체계의 값이 섞인 dict 이
    # 만들어진다.
    if sectors is None:
        sectors = ksic.roll_up_map(
            candidates_module.sector_map(
                store,
                as_of=as_of,
                entities=list(scores.index),
                market=market,
                source=DART_SECTOR_SOURCE,
            )
        )
        if sectors:
            trace.note(
                f"섹터 상한 대상 {len(sectors)}/{len(scores)}종목 "
                f"({len(set(sectors.values()))}개 섹터, DART 표준산업분류를 "
                "KSIC 중분류군으로 접은 것). 분류를 모르는 종목엔 상한을 "
                "적용하지 않는다"
            )
    if not sectors:
        trace.note(
            "섹터 상한을 적용하지 않았다 — 후보 중 업종 분류가 있는 종목이 "
            "없다. **분산이 확인된 것이 아니라 확인하지 못한 것이다**"
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
