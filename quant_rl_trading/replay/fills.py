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

from quant_rl_trading.schemas.order import Order, Side

if TYPE_CHECKING:
    from quant_rl_trading.store import Store


class FillStatus(StrEnum):
    FILLED = "filled"
    PARTIAL = "partial"
    REJECTED = "rejected"


@dataclass(frozen=True)
class FillParams:
    """체결 규칙 임계치. 전부 store.config 에서 온다."""

    impact_k: float
    max_adv_ratio: float
    max_liquidation_days: int
    min_order_value: float

    @classmethod
    def from_store(cls, store: Store, *, as_of: datetime, fx_rate: float = 1.0) -> FillParams:
        """``fx_rate`` 는 시장 통화 1단위의 원화 가격 — `executor/sizing.SizingParams` 와 같은 규약.

        `execution.min_order_value` 는 **원화**다. 미장 주문은 달러로 비교하므로 환율로
        나눠야 한다. 안 나누면 $23,000 주문이 "100,000 미만" 으로 거절된다 — 2026-09-03
        미장 shadow 첫 주문 64건이 전부 그렇게 빠졌다.
        """
        if fx_rate <= 0:
            raise ValueError(f"환율은 양수여야 한다: {fx_rate!r}")
        return cls(
            impact_k=float(store.config("execution.impact_k", as_of=as_of)),
            max_adv_ratio=float(store.config("execution.max_adv_ratio", as_of=as_of)),
            max_liquidation_days=int(store.config("execution.max_liquidation_days", as_of=as_of)),
            min_order_value=float(store.config("execution.min_order_value", as_of=as_of)) / fx_rate,
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
    #: 그날의 저가·고가. **지정가 체결 여부는 종가가 아니라 이 값으로 판단한다.**
    #: 지정가 주문은 장 중 내내 살아 있으므로, 저가가 매수 지정가를 스쳤으면
    #: 종가가 그 위에서 끝났어도 채워진 것이다. 모르면(``None``) 종가로 대신
    #: 판단한다 — 종가만 있는 시계열(지수 등)에서 체결이 통째로 사라지는 것보다
    #: 낫다.
    low: float | None = None
    high: float | None = None

    @property
    def buy_touch(self) -> float:
        """매수 지정가가 닿았는지 볼 기준값."""
        return self.low if self.low is not None else self.close

    @property
    def sell_touch(self) -> float:
        return self.high if self.high is not None else self.close


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
    return int(state.adv * params.max_adv_ratio * params.max_liquidation_days)


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

    capacity = int(state.volume * params.max_adv_ratio)
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
        # **지정가 판정은 그날 범위로 한다.** 종가와만 비교하면, 저가가 매수
        # 지정가 밑을 스치고 올라가 끝난 날이 전부 미체결로 적힌다 — 실제로는
        # 그 가격에 채워졌다. 그렇게 지운 체결은 백테스트에서 두 번 거짓말을
        # 한다: 못 산 종목이 오른 만큼 성적이 낮게 나오고, 체결률이 실제보다
        # 낮게 보여 슬리피지 모형이 멀쩡한데도 유동성 문제로 읽힌다.
        limit = order.limit_price
        if order.side is Side.BUY:
            if state.buy_touch > limit:
                return _rejected(order, "limit_not_met")
            # 지정가보다 비싸게 사지는 않는다. 종가+충격이 지정가 아래면 그
            # 값이 그대로 남는다(더 유리한 쪽을 지어내지 않는다).
            price = min(price, limit)
        else:
            if state.sell_touch < limit:
                return _rejected(order, "limit_not_met")
            price = max(price, limit)

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
        reason="ok" if filled == requested else "max_adv_ratio",
    )
