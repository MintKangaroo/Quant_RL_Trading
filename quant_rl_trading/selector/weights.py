"""Analyst 가중치 — **측정 결과에서만 온다.**

코드가 가중치를 정하지 않는다. `analyst_weights` 테이블에 적재된 IC 측정
결과를 as_of 로 읽는다. IC 0.03 을 통과하지 못한 Analyst 는 그 테이블에
가중치 0 으로 들어 있으므로, 합성에서 자동으로 빠진다 — **관찰 모드 Analyst 가
조용히 섞이는 경로는 없다** (selector.md §2).

시장별로 따로 읽는다. flow_kr 은 미장에 존재하지 않고, 국장 가중치를 미장에
그대로 쓰면 없는 Analyst 에 무게를 싣는 꼴이 된다.

## 측정된 가중치와 알파 가중치는 다르다

`measured_weights` 는 창고에 적힌 그대로다. `analyst_weights` 는 거기서
**제약 Analyst 를 뺀 것**이고, 알파 합성이 쓰는 것은 이쪽이다
(`constraints.CONSTRAINT_ANALYSTS`, 태스크 #32). IC 가 높다는 것과 알파라는
것은 다른 말이다 — `risk` 는 IC 를 통과하고도 알파가 아니다.

## 빈 dict 는 세 가지 사건이다

둘 다 빈 dict 를 돌려줄 수 있는데, 그 빈 dict 에 이르는 길은 셋이고 처방이
전부 다르다 — 측정이 없다 / 통과가 0종이다 / 통과가 전부 제약 Analyst다.
`weight_census` 가 그 셋을 갈라 센다. **호출부는 dict 의 크기가 아니라
census 로 판단해야 한다.**
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from quant_rl_trading.selector.constraints import alpha_weights

if TYPE_CHECKING:
    from quant_rl_trading.store import Store

ANALYST_WEIGHTS = "analyst_weights"


def analyst_weights(
    store: Store, *, as_of: datetime, market: str, lookback: int = 400
) -> dict[str, float]:
    """**알파 합성이 쓰는** 가중치. 측정값에서 제약 Analyst 를 뺀 것.

    빈 dict 를 돌려줄 수 있다 — 아직 아무도 측정되지 않은 창고다. 그때 합성
    점수는 비고, 후보도 비어야 한다. **측정 없이 동일가중으로 때우지 않는다.**
    그건 관찰 모드 Analyst 에게 실제 가중치를 주는 것과 같다.
    """
    return alpha_weights(
        measured_weights(store, as_of=as_of, market=market, lookback=lookback)
    )


def measured_weights(
    store: Store, *, as_of: datetime, market: str, lookback: int = 400
) -> dict[str, float]:
    """{analyst: weight} — **창고에 적힌 그대로**. 제약 Analyst 도 들어 있다.

    같은 Analyst 가 여러 번 측정됐으면 **가장 늦은 것**. IC 측정 결과를 있는
    그대로 보고 싶은 곳(진화의 유전자 목록, 배치 비교 도구)이 쓴다. 알파
    합성에 이걸 쓰면 `risk` 가 다시 점수로 섞인다 — `analyst_weights` 를 써라.
    """
    frame = store.get(ANALYST_WEIGHTS, as_of=as_of, lookback=lookback)
    if frame.empty:
        return {}
    frame = frame[frame["market"] == market]
    if frame.empty:
        return {}
    latest = frame.sort_values(["observed_at", "valid_from"]).groupby("entity_id").tail(1)
    return {
        str(row["entity_id"]): float(row["weight"])
        for row in latest.to_dict(orient="records")
        if float(row["weight"]) > 0.0
    }


#: 측정 자체가 없다. 창고에 이 시장의 `analyst_weights` 행이 한 줄도 없다 —
#: IC 측정을 아직 안 돌렸거나, 돌렸는데 저장이 실패했다.
NO_MEASUREMENT = "no_measurement"

#: 측정은 있는데 통과(weight > 0)가 0종이다. 기다려서 풀리지 않는다 —
#: 피처로 돌아가야 한다.
NONE_PASSED = "none_passed"

#: 통과는 있는데 **전부 제약 Analyst**다. 알파 합성에 남는 것이 0종이라
#: 결과는 통과 0종과 같지만, 원인이 다르다 — 여기는 "알파를 못 만들었다" 이지
#: "아무도 IC 를 못 넘었다" 가 아니다.
CONSTRAINT_ONLY = "constraint_only"


@dataclass(frozen=True)
class WeightCensus:
    """가중치 측정 현황. **셋을 갈라 보기 위한 것이다.**

    ``analyst_weights`` 가 알파 합성에 0종을 내놓는 길은 셋이고, 셋은 서로
    완전히 다른 사건이다. 그런데 결과가 전부 "빈 dict" 라 호출부에서는
    구분이 안 된다 — 2026-08 내내 US 세션이 매일 "IC 측정 결과가 없다" 를
    남겼는데, 실제로는 4종이 다 측정돼 있었고(chart +0.0145 · flow_us
    +0.0135 · regime -0.0087 · risk +0.0585) 통과한 risk 가 제약 Analyst 라
    알파에서 빠진 것이었다. 세 번째 경우였는데 첫 번째로 표시됐다.
    """

    #: 이 시장에서 측정 행이 있는 Analyst 이름. 통과 여부와 무관하다.
    measured: tuple[str, ...]
    #: 그중 가중치 > 0 인 것. `measured_weights` 가 돌려주는 키와 같다.
    passed: tuple[str, ...]
    #: 통과했지만 제약 Analyst 라 알파에서 빠진 것.
    constrained: tuple[str, ...]
    #: 알파 합성에 실제로 남는 것. `analyst_weights` 의 키와 같다.
    alpha: tuple[str, ...]
    #: 측정된 가중치 원본 {analyst: weight}. 통과하지 못한 것(0 이하)도 있다 —
    #: **여기서만 0 을 그대로 들고 있는다.** 밖으로 나갈 때는 `alpha_map` 을
    #: 거쳐 통과·알파만 남는다.
    values: dict[str, float] = field(default_factory=dict)

    @property
    def alpha_map(self) -> dict[str, float]:
        """알파 합성이 쓸 {analyst: weight}. `analyst_weights` 와 같은 것."""
        return {name: float(self.values[name]) for name in self.alpha}

    @property
    def fault(self) -> str:
        """알파가 0종인 이유. 0종이 아니면 빈 문자열.

        **빈 문자열이 정상이다.** 사유 코드가 있다는 것은 그 시장이 오늘
        아무것도 못 사는 것이 아니라 **설비가 고장 나 있다**는 뜻이다.
        """
        if self.alpha:
            return ""
        if not self.measured:
            return NO_MEASUREMENT
        if not self.passed:
            return NONE_PASSED
        return CONSTRAINT_ONLY

    def describe(self) -> str:
        """사람이 읽을 한 줄. 셋 중 어느 경우인지와 숫자를 함께 적는다."""
        fault = self.fault
        if not fault:
            return (
                f"알파 Analyst {len(self.alpha)}종 "
                f"({', '.join(self.alpha)}) · 측정 {len(self.measured)}종"
            )
        if fault == NO_MEASUREMENT:
            return (
                "**IC 측정 자체가 없다.** 이 시장의 analyst_weights 가 0행이다 — "
                "측정을 안 돌렸거나 저장이 실패했다. 동일가중으로 때우지 않는다"
            )
        if fault == NONE_PASSED:
            return (
                f"**측정 {len(self.measured)}종은 있는데 통과가 0종이다** "
                f"({', '.join(self.measured)}). IC 를 넘은 Analyst 가 하나도 "
                "없다는 뜻이고, 기다려서 풀리지 않는다 — 피처로 돌아가야 한다"
            )
        return (
            f"**통과 {len(self.passed)}종이 전부 제약 Analyst다** "
            f"({', '.join(self.constrained)}). 알파 합성에 남는 것이 0종이라 "
            "점수를 만들 수 없다 — 측정 자체는 돌고 있으므로 IC 를 다시 "
            "돌린다고 풀리지 않는다"
        )


def weight_census(
    store: Store, *, as_of: datetime, market: str, lookback: int = 400
) -> WeightCensus:
    """측정 현황을 센다. **`measured_weights` 로는 셀 수 없는 것을 센다.**

    `measured_weights` 는 통과하지 못한 Analyst 의 키를 아예 지운다(가중치 0
    을 그대로 돌려주면 합성에서 관찰 모드가 실제 무게를 받는다). 그래서 그
    함수만 보면 **"측정이 없다" 와 "측정은 있는데 다 떨어졌다" 가 같은 빈
    dict** 이다. 여기서는 지우기 전 원본을 세므로 둘이 갈린다.
    """
    frame = store.get(ANALYST_WEIGHTS, as_of=as_of, lookback=lookback)
    if frame.empty:
        return WeightCensus((), (), (), (), {})
    frame = frame[frame["market"] == market]
    if frame.empty:
        return WeightCensus((), (), (), (), {})
    latest = frame.sort_values(["observed_at", "valid_from"]).groupby("entity_id").tail(1)
    rows = {
        str(row["entity_id"]): float(row["weight"])
        for row in latest.to_dict(orient="records")
    }
    passed = tuple(sorted(name for name, value in rows.items() if value > 0.0))
    alpha = tuple(sorted(alpha_weights({name: rows[name] for name in passed})))
    return WeightCensus(
        measured=tuple(sorted(rows)),
        passed=passed,
        constrained=tuple(name for name in passed if name not in alpha),
        alpha=alpha,
        values=rows,
    )
