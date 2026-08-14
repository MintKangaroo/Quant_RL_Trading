"""목표비중 → 주식 수. 순수 코드 (불변식 6).

**여기가 목표와 현실이 갈라지는 지점이다.** 소액 구간에서는 정수 라운딩만으로
목표 30% 가 실현 22% 가 된다. 그 차이를 Allocator 에 되먹이지 않으면 RL 은
자기가 하지 않은 행동으로 보상받는다 (불변식 7).

순서 (agents.md §7 의 5~6):

    5. 정수 라운딩 · 최소 주문금액 · 고가주 제외
    6. 3% 거래대금 상한 · 청산 3일 제약

## 왜 내림인가

반올림하면 목표를 넘는 주문이 나온다. 현금이 모자라면 그 주문은 거부되고,
거부는 재시도로 이어지고, 재시도는 슬리피지를 만든다. **모자란 쪽으로
틀리는 것이 넘치는 쪽으로 틀리는 것보다 싸다.**
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import datetime
from typing import TYPE_CHECKING

from quant_rl_trading.schemas.order import Side

if TYPE_CHECKING:
    from quant_rl_trading.store import Store


@dataclass(frozen=True)
class SizingParams:
    max_adv_ratio: float
    max_liquidation_days: int
    min_order_value: float
    max_price_ratio: float
    #: 매도 대금이 예수금이 되기까지의 거래일. 주문가능금액을 잴 때만 쓴다.
    settlement_days: int = 2

    @classmethod
    def from_store(cls, store: Store, *, as_of: datetime) -> SizingParams:
        return cls(
            max_adv_ratio=float(store.config("execution.max_adv_ratio", as_of=as_of)),
            max_liquidation_days=int(
                store.config("execution.max_liquidation_days", as_of=as_of)
            ),
            min_order_value=float(store.config("execution.min_order_value", as_of=as_of)),
            max_price_ratio=float(store.config("universe.max_price_ratio", as_of=as_of)),
            settlement_days=int(store.config("execution.settlement_days", as_of=as_of)),
        )


@dataclass(frozen=True)
class Target:
    entity_id: str
    weight: float
    price: float
    #: 20일 평균 거래대금. 없으면 주문하지 않는다 — 못 파는 종목을 사는 것이
    #: 가장 비싼 실수다.
    adv_value: float
    lot_size: int = 1


@dataclass(frozen=True)
class Sized:
    entity_id: str
    side: Side
    quantity: int
    price: float
    target_weight: float
    realized_weight: float
    reason: str = ""


@dataclass(frozen=True)
class Skipped:
    entity_id: str
    target_weight: float
    reason: str


def size_orders(
    *,
    targets: list[Target],
    holdings: dict[str, int],
    equity: float,
    params: SizingParams,
    cash: float | None = None,
) -> tuple[list[Sized], list[Skipped]]:
    """목표비중과 현재 보유에서 주문을 만든다.

    ``holdings`` 는 현재 주식 수. 목표 수량과의 **차이**만 주문한다 — 전량
    청산 후 재매수하면 왕복 비용이 두 배가 된다.

    ``cash`` 는 **주문가능금액**이다(`accounting.ledger.available_cash`).
    매수 금액의 합이 이 값을 넘지 않는다. ``None`` 이면 제약을 걸지 않는데,
    **그 길로 이 저장소는 레버리지 2.83배까지 갔다** — 호출부는 언제나 값을
    준다. None 을 남겨 둔 것은 순수 함수 테스트가 한 종목의 라운딩만 보려고
    할 때를 위해서지, 운용 경로를 위해서가 아니다.
    """
    orders: list[Sized] = []
    skipped: list[Skipped] = []

    for target in targets:
        if target.price <= 0:
            skipped.append(Skipped(target.entity_id, target.weight, "가격 없음"))
            continue
        if target.adv_value <= 0:
            # 유동성을 모르면 상한을 계산할 수 없다. 0 으로 치면 상한이 사라진다.
            skipped.append(Skipped(target.entity_id, target.weight, "거래대금 관측 없음"))
            continue
        if equity > 0 and target.price > equity * params.max_price_ratio:
            skipped.append(Skipped(target.entity_id, target.weight, "고가주 — 1주가 자본 상한 초과"))
            continue

        desired_value = equity * target.weight
        # 내림. 넘치는 쪽으로 틀리면 현금 부족 → 거부 → 재시도 → 슬리피지다.
        desired_quantity = _floor_lot(desired_value / target.price, target.lot_size)
        held = holdings.get(target.entity_id, 0)
        delta = desired_quantity - held
        if delta == 0:
            if desired_quantity == 0 and target.weight > 0:
                # **목표는 있는데 1주도 못 산 경우.** 소액 구간에서 흔하다.
                # 조용히 넘어가면 되먹임 기록에 "왜 0 인지" 가 남지 않는다.
                skipped.append(
                    Skipped(target.entity_id, target.weight, "목표 비중이 1주에 못 미친다")
                )
            continue

        side = Side.BUY if delta > 0 else Side.SELL
        quantity = abs(delta)

        # 6. 거래대금 상한. 하루에 ADV 의 3% 넘게 건드리지 않는다.
        cap_quantity = _floor_lot(
            params.max_adv_ratio * target.adv_value / target.price, target.lot_size
        )
        if cap_quantity <= 0:
            skipped.append(Skipped(target.entity_id, target.weight, "거래대금 상한이 1주 미만"))
            continue
        capped = min(quantity, cap_quantity)
        reason = ""
        if capped < quantity:
            reason = f"거래대금 {params.max_adv_ratio:.0%} 상한으로 {quantity}→{capped}"
        quantity = capped

        value = quantity * target.price
        if side is Side.BUY and value < params.min_order_value:
            # 매도는 통과시킨다. 소액이라 못 파는 규칙은 포지션을 영영 남긴다.
            skipped.append(
                Skipped(target.entity_id, target.weight, "최소 주문금액 미달")
            )
            continue

        realized = (held + (quantity if side is Side.BUY else -quantity)) * target.price
        orders.append(
            Sized(
                entity_id=target.entity_id,
                side=side,
                quantity=quantity,
                price=target.price,
                target_weight=target.weight,
                realized_weight=realized / equity if equity > 0 else 0.0,
                reason=reason,
            )
        )

    if cash is not None:
        orders, cash_skipped = _fit_to_cash(
            orders,
            cash=cash,
            equity=equity,
            holdings=holdings,
            lots={target.entity_id: target.lot_size for target in targets},
            params=params,
        )
        skipped.extend(cash_skipped)

    return orders, skipped


def _fit_to_cash(
    orders: list[Sized],
    *,
    cash: float,
    equity: float,
    holdings: dict[str, int],
    lots: dict[str, int],
    params: SizingParams,
) -> tuple[list[Sized], list[Skipped]]:
    """매수 합계를 주문가능금액 안으로 자른다. **매도는 건드리지 않는다.**

    매도를 자르면 빠져나올 길을 막는 것이고, 매도는 돈을 쓰지 않는다.

    ``오늘 판 돈은 오늘 못 쓴다`` — 그 판단은 ``cash`` 를 만든 쪽
    (`accounting.ledger.available_cash`)이 이미 했다. 여기서는 받은 예산만 쓴다.

    **자르는 순서는 목표 비중이 큰 것부터**다. 확신이 큰 자리를 먼저 채우는
    것이 비중을 고르게 깎는 것보다 전략의 뜻에 가깝고, 무엇보다 **결정론적**
    이어야 한다(불변식 5) — 같은 as_of 로 두 번 돌리면 같은 주문이 나와야
    한다. 비중이 같으면 ``entity_id`` 오름차순으로 가른다. 파이썬 정렬은
    안정적이지만 입력 순서에 기대지 않는다. 입력은 dict 순서에서 오고,
    그것까지 계약으로 삼으면 호출부가 바뀔 때 조용히 깨진다.
    """
    budget = max(0.0, cash)
    skipped: list[Skipped] = []
    #: 종목 → 잘린 뒤의 주문. 없으면 통째로 빠진 것이다.
    resolved: dict[str, Sized] = {}

    buys = sorted(
        [item for item in orders if item.side is Side.BUY],
        key=lambda item: (-item.target_weight, item.entity_id),
    )
    for item in buys:
        value = item.quantity * item.price
        if value <= budget:
            budget -= value
            resolved[item.entity_id] = item
            continue

        # 예산에 맞게 줄여 본다. 통째로 버리면 남은 현금이 놀고, 다음 세션에
        # 같은 주문이 다시 나와 같은 자리에서 또 잘린다.
        affordable = _floor_lot(budget / item.price, lots.get(item.entity_id, 1))
        trimmed_value = affordable * item.price
        if affordable <= 0 or trimmed_value < params.min_order_value:
            skipped.append(
                Skipped(item.entity_id, item.target_weight, "주문가능금액 부족")
            )
            continue

        budget -= trimmed_value
        note = f"주문가능금액으로 {item.quantity}→{affordable}"
        held = holdings.get(item.entity_id, 0)
        resolved[item.entity_id] = replace(
            item,
            quantity=affordable,
            # 되먹임은 **실제로 내는 주문** 기준이어야 한다. 줄인 수량을 두고
            # 옛 비중을 남기면 Allocator 가 하지 않은 행동으로 보상받는다
            # (불변식 7).
            realized_weight=(
                (held + affordable) * item.price / equity if equity > 0 else 0.0
            ),
            reason=f"{item.reason} · {note}" if item.reason else note,
        )

    # **입력 순서를 지킨다.** 매도·매수를 갈라 붙이면 주문 기록의 줄 순서가
    # 바뀌고, 그건 이 수정이 건드릴 이유가 없는 것이다.
    kept = [
        resolved.get(item.entity_id, item) if item.side is Side.BUY else item
        for item in orders
        if item.side is not Side.BUY or item.entity_id in resolved
    ]
    return kept, skipped


def liquidation_plan(
    *, entity_id: str, quantity: int, price: float, adv_value: float, params: SizingParams
) -> list[int]:
    """청산을 며칠에 나눠 낼지. **3일 안에 못 빠져나오는 포지션은 애초에 크다.**

    상한을 무시하고 한 번에 던지면 그 충격비용이 손실의 대부분이 된다.
    """
    if adv_value <= 0 or price <= 0:
        return [quantity]
    daily = max(1, int(params.max_adv_ratio * adv_value / price))
    days = min(params.max_liquidation_days, math.ceil(quantity / daily))
    if days <= 1:
        return [quantity]

    per_day = math.ceil(quantity / days)
    plan: list[int] = []
    remaining = quantity
    for _ in range(days):
        take = min(per_day, remaining)
        if take <= 0:
            break
        plan.append(take)
        remaining -= take
    if remaining > 0:
        # 상한을 지키면 3일 안에 못 끝나는 경우. 마지막 날에 몰지 않고
        # **남았다는 사실을 남긴다** — 호출부가 다음 날 이어서 낸다.
        plan[-1] += 0
    return plan


def replace_weight(target: Target, weight: float) -> Target:
    """목표 비중만 바꾼 사본. 강제 청산이 이걸 쓴다."""
    return Target(
        entity_id=target.entity_id,
        weight=weight,
        price=target.price,
        adv_value=target.adv_value,
        lot_size=target.lot_size,
    )


def _floor_lot(quantity: float, lot_size: int) -> int:
    if lot_size <= 1:
        return int(math.floor(quantity))
    return int(math.floor(quantity / lot_size) * lot_size)
