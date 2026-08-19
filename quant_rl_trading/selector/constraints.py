"""제약 Analyst — 점수를 매기지 않고 **자를 뿐이다** (태스크 #32).

## 왜 분리했나

`risk` 는 자기 독스트링에 "어느 종목이 오를까가 아니라 **어느 종목이
포트폴리오를 위험하게 만드나**" 를 잰다고 적고 있다. 그런 점수를 다른 알파와
평균 내면 **저위험이 곧 고수익 신호로 둔갑한다.** 실제로 그렇게 됐다
(2026-08-15 실측, 2026-01~08):

    전구간  펀드 +16.17%  코스피 +58.10%  알파 -41.93%p
    베타 0.131 · 상승일 포착률 14% · 주식비중 81.3%

현금을 쥐어서가 아니다. 81% 를 주식에 넣고도 지수와 안 움직인다.
`low_volatility .45 + low_beta .20` 이 **65% 의 축에서 급등 대형주를 벌주기**
때문이다. 삼성전자는 2,800종목 중 595위, SK하이닉스는 1,044위였다.

`risk` 를 빼면 삼전 676→169위, 하이닉스 1088→533위. **절반으로 줄이면
933/1356 으로 어중간하다** — 문제가 세기가 아니라 **자리**라는 뜻이다.
그래서 가중치를 낮추는 것이 아니라 옮긴다.

## 제약은 비례하지 않는다. 자른다

알파는 전 종목에 연속적으로 작용한다 — 조금 위험하면 조금 감점. 그래서 잘
고른 종목이 "위험하다" 는 이유로 계속 밀린다. 제약은 **꼬리에서만** 작용한다:
너무 위험한 것은 못 사고, 그 선을 넘지 않는 종목들 사이에서는 아무 말도
하지 않는다. 사고 싶은 것을 고르는 일과 못 살 것을 거르는 일은 다른 일이다.

`filters.tradable_universe` 의 거래대금 하한과 같은 종류의 장치다. 거기서
못 거른 것 — 변동성·베타 — 을 여기서 자른다.

## 백분위로 자르는 이유

`risk` 점수는 그날의 횡단면 순위에서 나온 값이라 **절대 수준에 뜻이 없다.**
0.3 이 어떤 날은 하위 5% 고 어떤 날은 하위 40% 다. 절대값으로 선을 그으면
변동성이 높은 국면에 후보가 통째로 사라지고, 낮은 국면엔 아무것도 안 걸린다.
백분위로 자르면 매일 같은 비율이 잘린다 — 자르는 양이 예측 가능해야
"오늘 살 게 없다" 와 "제약이 다 먹었다" 를 구분할 수 있다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from quant_rl_trading.selector.candidates import SelectionTrace
    from quant_rl_trading.store import Store

#: 위험을 재는 Analyst 이름. 이름을 문자열로 흩뿌리지 않는다.
RISK_ANALYST = "risk"

#: 알파 합성에 **들어가지 않는** Analyst. 여기 이름이 있으면 가중치가
#: 측정돼 있어도 `combined_scores` 의 분자·분모 양쪽에서 빠진다.
#:
#: 관찰 모드(가중치 0)와 다르다. 관찰 모드는 "아직 못 미더워서 안 쓴다" 이고
#: IC 가 오르면 저절로 들어온다. 여기 있는 것은 **IC 가 아무리 높아도 알파가
#: 아니다** — `low_volatility` 의 IC +0.0894 는 "저변동 종목이 5일 뒤 덜
#: 움직인다" 는 정의에 가까운 사실이지 예측이 아니다.
CONSTRAINT_ANALYSTS = frozenset({RISK_ANALYST})


@dataclass(frozen=True)
class ConstraintParams:
    """제약 임계치. 전부 `store.config` 에서 온다 (불변식 10)."""

    #: 위험 점수 하위 이 비율을 자른다. 0 이면 제약을 끈다.
    risk_floor_percentile: float

    @classmethod
    def from_store(cls, store: Store, *, as_of: datetime) -> ConstraintParams:
        return cls(
            risk_floor_percentile=float(
                store.config("selector.risk_floor_percentile", as_of=as_of)
            )
        )


def alpha_weights(weights: Mapping[str, float]) -> dict[str, float]:
    """알파 합성에 쓸 가중치. 제약 Analyst 를 뺀다.

    **0 으로 덮지 않고 키를 지운다.** 0 으로 두면 `combined_scores` 결과는
    같지만, 화면의 Decision Trace 가 "risk 가중치 0" 을 관찰 모드로 읽는다 —
    "아직 못 미더운 Analyst" 와 "애초에 알파가 아닌 Analyst" 는 다른 사실이고,
    둘을 같은 숫자로 표시하면 나중에 누가 risk 의 IC 를 보고 가중치를 돌려준다.
    """
    return {
        name: float(value)
        for name, value in weights.items()
        if name not in CONSTRAINT_ANALYSTS
    }


def constraint_scores(signals: pd.DataFrame, analyst: str) -> pd.Series:
    """한 제약 Analyst 의 종목별 점수. 같은 종목이 여럿이면 **가장 늦은 관측**.

    `combine.combined_scores` 와 같은 규칙이다 — 여기서 다른 규칙을 쓰면
    같은 신호를 두 단계가 다르게 읽는다.
    """
    if signals.empty or "analyst" not in signals.columns:
        return pd.Series(dtype=float)
    rows = signals[signals["analyst"].astype(str) == analyst]
    if rows.empty:
        return pd.Series(dtype=float)
    if "observed_at" in rows.columns:
        rows = rows.sort_values("observed_at").groupby("entity_id", as_index=False).tail(1)
    return pd.Series(
        rows["score"].astype(float).to_numpy(),
        index=rows["entity_id"].astype(str),
    )


def apply_risk_floor(
    scores: pd.Series,
    *,
    signals: pd.DataFrame,
    params: ConstraintParams,
    trace: SelectionTrace | None = None,
) -> pd.Series:
    """위험 하위 백분위를 잘라낸 합성 점수.

    ``scores`` 는 알파 합성 점수(제약 Analyst 가 이미 빠진 것), ``signals`` 는
    그 세션의 원본 신호다. 위험 점수는 원본에서 직접 읽는다 — 알파에서 뺐다고
    신호까지 안 보는 것이 아니다.

    **위험 점수가 없는 종목은 자르지 않는다.** `risk` 는 60세션 이상 가격이
    관측된 종목에만 의견을 낸다. 관측이 없는 것을 "위험하다" 로 읽으면 신규
    상장주와 수집 구멍이 같은 처분을 받는데, 그건 위험 판단이 아니라
    데이터 사고다. 흔적에는 남긴다.
    """
    if scores.empty:
        return scores
    floor = float(params.risk_floor_percentile)
    if floor <= 0.0:
        if trace is not None:
            trace.note("위험 하한을 적용하지 않았다 (selector.risk_floor_percentile = 0)")
        return scores

    risk = constraint_scores(signals, RISK_ANALYST).reindex(scores.index)
    observed = risk.dropna()
    if observed.empty:
        if trace is not None:
            trace.note(
                f"위험 하한을 적용하지 못했다 — {RISK_ANALYST} 신호가 "
                f"후보 {len(scores)}종목 중 0건이다. **제약이 통과된 것이지 "
                "안전이 확인된 것이 아니다**"
            )
        return scores

    threshold = float(observed.quantile(floor))
    cut = observed[observed < threshold]
    if trace is not None:
        missing = int(len(scores) - len(observed))
        trace.note(
            f"위험 하한 하위 {floor:.0%} 컷 — {len(cut)}종목 제외 "
            f"(임계 {threshold:+.4f}, 관측 {len(observed)}종목"
            + (f", 위험 점수 없음 {missing}종목은 통과)" if missing else ")")
        )
        for entity in cut.index:
            trace.drop(str(entity), f"위험 하위 {floor:.0%} (risk {cut[entity]:+.4f})")
    return scores[~scores.index.isin(cut.index)]

