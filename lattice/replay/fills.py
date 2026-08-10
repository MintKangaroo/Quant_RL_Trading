"""체결 시뮬레이터 — 순수 코드. AI 없음, Clock 없음, store 접근 없음.

    충격비용 = k × 변동성 × √(주문량 / 일평균거래량)

슬리피지를 고정값으로 두면 소형주 수익률이 뻥튀기된다. 100주를 사는 것과
그날 거래량의 절반을 사는 것이 같은 비용일 리 없다.

임계치는 인자로 받는다. 호출자가 ``FillParams.from_store`` 로 as_of 시점의
설정을 읽어 넣는다 — 하드코딩 금지(불변식 10)이면서, 시뮬레이터 자체는
입력만으로 결정되는 순수 함수로 남는다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from lattice.schemas.order import Order, Side

if TYPE_CHECKING:
    from lattice.store import Store


class FillStatus(StrEnum):
    FILLED = "filled"
    PARTIAL = "partial"
    REJECTED = "rejected"


@dataclass(frozen=True)
class FillParams:
    """체결 규칙 임계치. 전부 store.config 에서 온다."""

    impact_k: float
    participation_cap: float
    liquidation_days: int
    min_order_value: float

    @classmethod
    def from_store(cls, store: Store, *, as_of: datetime) -> FillParams:
        return cls(
            impact_k=float(store.config("execution.impact_k", as_of=as_of)),
            participation_cap=float(store.config("execution.participation_cap", as_of=as_of)),
            liquidation_days=int(store.config("execution.liquidation_days", as_of=as_of)),
            min_order_value=float(store.config("execution.min_order_value", as_of=as_of)),
        )


@dataclass(frozen=True)
class MarketState:
    """체결 시점의 시장 상태. 시뮬레이터가 보는 전부."""

    entity_id: str
    close: float
    volume: float
    adv: float
    volatility: float
    lot_size: int = 1
    tick_size: float = 0.0
    is_halted: bool = False
    limit_up: float | None = None
    limit_down: float | None = None


@dataclass(frozen=True)
class Fill:
    entity_id: str
    side: Side
    requested_quantity: int
    filled_quantity: int
    avg_price: float
    impact_bps: float
    status: FillStatus
    reason: str

    def canonical(self) -> dict[str, object]:
        return {
            "entity_id": self.entity_id,
            "side": str(self.side),
            "requested_quantity": self.requested_quantity,
            "filled_quantity": self.filled_quantity,
            # 부동소수 표현 차이가 바이트 비교를 깨지 않게 고정 자릿수로 묶는다.
            "avg_price": round(self.avg_price, 6),
            "impact_bps": round(self.impact_bps, 6),
            "status": str(self.status),
            "reason": self.reason,
        }


def _rejected(order: Order, reason: str) -> Fill:
    return Fill(
        entity_id=order.entity_id,
        side=order.side,
        requested_quantity=order.quantity,
        filled_quantity=0,
        avg_price=0.0,
        impact_bps=0.0,
        status=FillStatus.REJECTED,
        reason=reason,
    )


def _round_to_tick(price: float, tick: float) -> float:
    if tick <= 0:
        return price
    return round(round(price / tick) * tick, 10)


def impact_bps(quantity: int, state: MarketState, params: FillParams) -> float:
    """충격비용(bp). 유동성 정보가 없으면 0 이 아니라 예외다.

    ADV 를 모르는 종목을 슬리피지 0 으로 체결시키면, 백테스트는 가장 못
    사는 종목에서 가장 좋은 성적을 낸다.
    """
    if state.adv <= 0:
        raise ValueError(f"{state.entity_id}: ADV 가 없다. 충격비용을 계산할 수 없다")
    ratio = quantity / state.adv
    return params.impact_k * state.volatility * math.sqrt(ratio) * 10_000


def max_position_for_liquidation(state: MarketState, params: FillParams) -> int:
    """청산 제약 — 정해진 일수 안에 참여율 상한으로 빠져나올 수 있는 최대 수량.

    Executor(M3)가 포지션 상한을 걸 때 쓴다. 못 빠져나오는 크기는 애초에
    들어가지 않는다.
    """
    return int(state.adv * params.participation_cap * params.liquidation_days)


def simulate_fill(order: Order, state: MarketState, params: FillParams) -> Fill:
    """주문 하나의 체결 결과. 같은 입력이면 언제나 같은 출력."""
    if state.entity_id != order.entity_id:
        raise ValueError(f"주문과 시장 상태의 종목이 다르다: {order.entity_id} / {state.entity_id}")

    if state.is_halted:
        return _rejected(order, "halted")

    if order.side is Side.BUY and state.limit_up is not None and state.close >= state.limit_up:
        return _rejected(order, "limit_up")
    if order.side is Side.SELL and state.limit_down is not None and state.close <= state.limit_down:
        return _rejected(order, "limit_down")

    lot = max(state.lot_size, 1)
    requested = (order.quantity // lot) * lot
    if requested <= 0:
        return _rejected(order, "below_lot_size")

    capacity = int(state.volume * params.participation_cap)
    if capacity <= 0:
        return _rejected(order, "no_liquidity")

    filled = (min(requested, capacity) // lot) * lot
    if filled <= 0:
        return _rejected(order, "below_lot_size")

    bps = impact_bps(filled, state, params)
    direction = 1.0 if order.side is Side.BUY else -1.0
    price = state.close * (1.0 + direction * bps / 10_000)

    if state.limit_up is not None:
        price = min(price, state.limit_up)
    if state.limit_down is not None:
        price = max(price, state.limit_down)
    price = _round_to_tick(price, state.tick_size)

    if order.limit_price is not None:
        if order.side is Side.BUY and price > order.limit_price:
            return _rejected(order, "limit_not_met")
        if order.side is Side.SELL and price < order.limit_price:
            return _rejected(order, "limit_not_met")

    if filled * price < params.min_order_value:
        return _rejected(order, "below_min_order_value")

    return Fill(
        entity_id=order.entity_id,
        side=order.side,
        requested_quantity=order.quantity,
        filled_quantity=filled,
        avg_price=price,
        impact_bps=bps,
        status=FillStatus.FILLED if filled == requested else FillStatus.PARTIAL,
        reason="ok" if filled == requested else "participation_cap",
    )
