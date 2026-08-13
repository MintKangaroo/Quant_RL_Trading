"""Analyst 점수 합성. **여기가 알파의 결합부다** (selector.md §1).

    combined(entity) = Σ(wᵢ · scoreᵢ · confᵢ) / Σ(wᵢ · confᵢ)

처음부터 비선형 결합을 시도하지 않는다. 신뢰도 가중합으로 시작해서 이것을
이기는 비선형이 나올 때만 교체한다. 복잡하게 시작하면 뭐가 문제인지 못 찾는다.

## 점수를 안 낸 Analyst 는 분모에서도 빠진다

flow_kr 은 수급이 관측된 종목만 의견을 낸다. 그 종목을 "중립 0" 으로 채워
분모에 남기면, **의견 없음이 반대 의견처럼 작동해** 다른 Analyst 의 강한
의견을 희석한다. 의견이 없는 것과 중립 의견은 다르다 — Analyst 내부에서
결측을 다루는 방식과 같은 원칙이다.

가중치 0(관찰 모드)인 Analyst 는 분자·분모 양쪽에서 자동으로 빠진다. 곱해서
0 이기 때문이다. IC 를 통과하지 못한 Analyst 가 조용히 섞이는 경로는 없다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import pandas as pd

#: 이 아래 가중치는 참여하지 않은 것으로 본다. 부동소수 잔여값이 분모에
#: 남아 0 나누기 근처를 만드는 것을 막는다.
EPSILON = 1e-12


@dataclass(frozen=True)
class Contribution:
    """한 종목에 대한 Analyst 하나의 몫. Decision Trace 가 이걸 읽는다."""

    analyst: str
    score: float
    confidence: float
    weight: float

    @property
    def share(self) -> float:
        return self.weight * self.confidence


def combined_scores(
    signals: pd.DataFrame, weights: Mapping[str, float]
) -> pd.Series:
    """종목별 합성 점수.

    ``signals`` 는 ``entity_id · analyst · score · confidence`` 를 갖는 프레임.
    같은 (종목, Analyst) 가 여러 행이면 **가장 늦은 관측**을 쓴다 — 정정본이
    원본을 덮는 것은 창고의 규칙이고, 여기서 다시 뒤집지 않는다.
    """
    if signals.empty:
        return pd.Series(dtype=float)

    frame = signals.copy()
    if "observed_at" in frame.columns:
        frame = (
            frame.sort_values("observed_at")
            .groupby(["entity_id", "analyst"], as_index=False)
            .tail(1)
        )

    frame["weight"] = frame["analyst"].map(lambda name: float(weights.get(name, 0.0)))
    frame["share"] = frame["weight"] * frame["confidence"].astype(float)
    frame = frame[frame["share"].abs() > EPSILON]
    if frame.empty:
        return pd.Series(dtype=float)

    frame["weighted"] = frame["share"] * frame["score"].astype(float)
    grouped = frame.groupby("entity_id")[["weighted", "share"]].sum()
    return (grouped["weighted"] / grouped["share"]).sort_values(ascending=False)


def contributions(
    signals: pd.DataFrame, weights: Mapping[str, float], entity_id: str
) -> tuple[Contribution, ...]:
    """한 종목의 분해. **왜 이 점수인지 남기지 못하면 결정을 설명할 수 없다.**"""
    if signals.empty:
        return ()
    rows = signals[signals["entity_id"] == entity_id]
    out = [
        Contribution(
            analyst=str(row["analyst"]),
            score=float(row["score"]),
            confidence=float(row["confidence"]),
            weight=float(weights.get(str(row["analyst"]), 0.0)),
        )
        for row in rows.to_dict(orient="records")
    ]
    return tuple(sorted(out, key=lambda item: abs(item.share), reverse=True))
