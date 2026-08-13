"""룰 베이스라인 — Allocator 자리에 RL 대신 들어간다. 순수 코드.

**M3 는 이것으로 돈을 벌 수 있어야 한다.** RL 은 M4 이고, 작동하는 시스템
위에 얹는 것이다 (CLAUDE.md 개발 원칙).

세 가지를 전부 구현하고 설정으로 고른다:

    equal              동일가중
    score              스코어 비례          ← 기본값
    score_inverse_vol  스코어 비례 + 변동성 역가중

## 왜 기본이 동일가중이 아닌가

**M4 에서 RL 의 진짜 경쟁자가 될 것이기 때문이다.** 동일가중은 이기기 쉬운
상대라, 그걸 기준으로 삼으면 RL 이 실제로 나은지 모르는 채로 승격시키게 된다.
선행 프로젝트가 "RL 이 룰보다 낫다" 를 증명하지 못한 채 9차까지 간 이유가
이런 종류의 느슨한 기준이다.

## 음수 점수는 사지 않는다

합성 점수가 음수인 종목은 "덜 좋은 매수 후보" 가 아니라 **팔 이유**다.
비중을 0 으로 둔다. 음수를 그대로 정규화하면 부호가 뒤집혀 가장 나쁜 종목에
가장 큰 비중이 갈 수 있다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from quant_rl_trading.store import Store


class Baseline(StrEnum):
    EQUAL = "equal"
    SCORE = "score"
    SCORE_INVERSE_VOL = "score_inverse_vol"


@dataclass(frozen=True)
class AllocatorParams:
    baseline: Baseline
    max_position_weight: float
    cash_buffer: float

    @classmethod
    def from_store(cls, store: Store, *, as_of: datetime) -> AllocatorParams:
        return cls(
            baseline=Baseline(str(store.config("allocator.baseline", as_of=as_of))),
            max_position_weight=float(
                store.config("allocator.max_position_weight", as_of=as_of)
            ),
            cash_buffer=float(store.config("allocator.cash_buffer", as_of=as_of)),
        )


def allocate(
    *,
    scores: Mapping[str, float],
    params: AllocatorParams,
    volatility: Mapping[str, float] | None = None,
) -> dict[str, float]:
    """목표 비중. 합은 ``1 − cash_buffer`` 이하다.

    ``volatility`` 는 일간 수익률 표준편차. ``score_inverse_vol`` 에서만 쓰고,
    **없는 종목은 배분에서 뺀다** — 변동성을 모르는 종목에 역가중을 주려면
    분모를 지어내야 하고, 지어낸 분모는 대개 그 종목에 유리하게 틀린다.
    """
    positive = {
        entity: float(score) for entity, score in scores.items() if float(score) > 0.0
    }
    if not positive:
        return {}

    if params.baseline is Baseline.EQUAL:
        raw = {entity: 1.0 for entity in positive}
    elif params.baseline is Baseline.SCORE:
        raw = dict(positive)
    else:
        if volatility is None:
            raise ValueError(
                "score_inverse_vol 인데 변동성이 없다. 모르는 분모를 지어내지 않는다"
            )
        raw = {}
        for entity, score in positive.items():
            sigma = float(volatility.get(entity, 0.0))
            if sigma <= 0.0:
                continue
            raw[entity] = score / sigma
        if not raw:
            return {}

    return _normalize(raw, params=params)


def _normalize(raw: Mapping[str, float], *, params: AllocatorParams) -> dict[str, float]:
    """정규화 + 종목 상한. **상한에서 깎인 몫은 나머지에 다시 나눈다.**

    깎고 끝내면 총 비중이 목표보다 작아지고, 그 차이는 아무도 의도하지 않은
    현금이 된다. 소수 종목만 남는 날에는 상한 때문에 절반이 현금이 되는 일도
    생긴다 — 그건 배분 결정이 아니라 계산 사고다.

    다만 **모두가 상한에 닿으면 더 나눌 곳이 없다.** 그때는 남는 몫을 현금으로
    둔다. 상한을 넘겨 배분하는 것보다 낫다.
    """
    investable = max(0.0, 1.0 - params.cash_buffer)
    total = sum(raw.values())
    if total <= 0:
        return {}

    weights = {entity: value / total * investable for entity, value in raw.items()}
    cap = params.max_position_weight

    for _ in range(len(weights) + 1):
        excess = sum(max(0.0, value - cap) for value in weights.values())
        if excess <= 1e-12:
            break
        weights = {entity: min(value, cap) for entity, value in weights.items()}
        room = {entity: cap - value for entity, value in weights.items() if value < cap}
        headroom = sum(room.values())
        if headroom <= 1e-12:
            # 전부 상한에 닿았다. 남는 몫은 현금이다.
            break
        share = min(excess, headroom)
        for entity, available in room.items():
            weights[entity] += share * (available / headroom)

    return {entity: round(value, 10) for entity, value in weights.items() if value > 0.0}
