"""Pure supervision decisions wired to broker actions by tools/chase_orders.py.

Production uses ActionJournal.dispatch for durable intent-before-send and restart
recovery. A receipt is not a confirmed price change or cancellation. Unknown fill
or action outcomes block follow-up actions until authenticated reconciliation.
The optional direct adapter keeps the same receipt semantics for isolated callers.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
import math
from typing import TYPE_CHECKING

from quant_rl_trading.broker import BrokerError, RejectedOrder
from quant_rl_trading.executor.lifecycle import (
    Action,
    ActionType,
    LifecycleParams,
    OpenOrder,
    OrderStatus,
    apply_fill,
    close_session,
    decide,
    open_order_from_planned,
)

if TYPE_CHECKING:
    from quant_rl_trading.broker import Ack, Broker
    from quant_rl_trading.broker.fills import SyncResult
    from quant_rl_trading.executor.guards import GateResult
    from quant_rl_trading.executor.orders import PlannedOrder

UNRESOLVED = (OrderStatus.CANCEL_UNKNOWN, OrderStatus.MODIFY_UNKNOWN)
ACTIVE = (OrderStatus.SUBMITTED, OrderStatus.PARTIALLY_FILLED)

__all__ = [
    "SupervisionResult",
    "close",
    "cumulative_from_sync",
    "register",
    "step",
]


@dataclass(frozen=True)
class SupervisionResult:
    """한 회차의 결과. 호출자는 ``orders`` 를 그대로 다음 회차에 넘긴다."""

    #: 갱신된 주문 상태 전부(종결된 것 포함). 다음 회차의 입력이다.
    orders: tuple[OpenOrder, ...]
    #: 이번 회차에 실제로 내린 판단들. WAIT 는 담지 않는다 — 아무것도
    #: 안 했다는 뜻이라 감사 로그를 채울 이유가 없다.
    actions: tuple[Action, ...]
    #: 체결 상태를 몰라 **건드리지 않은** 주문들. 호출자가 봐야 한다.
    skipped: tuple[tuple[str, str], ...] = ()
    #: 브로커 호출이 실패한 주문들 (order_id, 사유).
    errors: tuple[tuple[str, str], ...] = ()

    @property
    def open(self) -> tuple[OpenOrder, ...]:
        """아직 살아 있는 주문 — 다음 회차에도 지켜봐야 한다."""
        return tuple(
            item
            for item in self.orders
            if item.status in (*ACTIVE, *UNRESOLVED)
        )


def register(
    planned: Iterable[PlannedOrder],
    acks: Iterable[Ack],
    *,
    now: datetime,
    reference_prices: Mapping[str, float],
) -> list[OpenOrder]:
    """전송 결과를 생애주기 관리 대상으로 바꾼다.

    ``pipeline.run`` 이 돌려준 ``planned``·``acks`` 를 그대로 넣으면 된다.
    관리 대상이 되는 것은 **접수됐고 지정가가 붙은** 주문뿐이다:

    - 거부(``accepted=False``)된 주문은 살아 있지 않다.
    - 시장가 주문은 즉시 체결·거부로 끝나 재호가할 여지가 없다
      (``lifecycle.open_order_from_planned``).
    - ``broker_order_no`` 가 없으면 정정·취소를 낼 방법이 없다. paper 는
      여기 걸려 자연스럽게 빠진다 — 보내지 않은 주문을 쫓아갈 이유가 없다.

    ``reference_prices`` 는 슬리피지 상한의 기준이 되는 **최초 계획 시점의
    가격**이다(``sizing.Target.price``). 재호가할 때마다 기준을 옮기면 상한이
    시세를 따라 늘어나 사실상 사라진다.
    """
    by_order_id = {ack.order_id: ack for ack in acks}
    out: list[OpenOrder] = []
    for item in planned:
        ack = by_order_id.get(item.order_id)
        if ack is None or not ack.accepted or not ack.broker_order_no:
            continue
        if item.order.limit_price is None:
            continue
        reference = reference_prices.get(item.order.entity_id)
        if reference is None:
            continue
        out.append(
            open_order_from_planned(
                item,
                reference_price=reference,
                now=now,
                broker_order_no=ack.broker_order_no,
            )
        )
    return out


def cumulative_from_sync(result: SyncResult) -> dict[str, float]:
    """체결 조회 결과에서 **아는 것만** 뽑는다.

    ``UNKNOWN`` 은 담지 않는다 — 담을 값이 없다. 빠진 주문은 ``step`` 이
    "모른다" 로 보고 이번 회차에서 건너뛴다. 0 으로 채우면 "안 채워졌다" 가
    되어 재호가·취소가 사실이 아닌 근거로 나간다.
    """
    from quant_rl_trading.broker.fills import FillState

    out: dict[str, float] = {}
    for outcome in result.outcomes:
        if outcome.state is FillState.UNKNOWN or outcome.cumulative_quantity is None:
            continue
        out[outcome.order_id] = float(outcome.cumulative_quantity)
    return out


def step(
    orders: Iterable[OpenOrder],
    broker: Broker,
    *,
    now: datetime,
    market_prices: Mapping[str, float],
    cumulative_filled: Mapping[str, float],
    params: LifecycleParams,
    pretrade_check: Callable[[OpenOrder], GateResult],
    dispatch: Callable[[Action, OpenOrder], tuple[OpenOrder, str | None]] | None = None,
) -> SupervisionResult:
    """한 회차 — 체결을 반영하고, 판단하고, 브로커를 부른다.

    순서가 중요하다. **체결 반영이 먼저다.** 잔량을 모르는 채로 재호가하면
    이미 채워진 수량을 다시 낸다.

    체결이 재호가 타이머를 늘리지 않는 것은 ``lifecycle.apply_fill`` 이
    ``last_action_at`` 을 건드리지 않기 때문이다 — 조금씩 계속 체결되는
    주문도 이 루프에서 제때 재호가·취소 판단을 받는다. 그 성질이 실제로
    도는지는 ``tests/executor/test_supervise.py`` 가 지킨다.
    """
    updated: list[OpenOrder] = []
    actions: list[Action] = []
    skipped: list[tuple[str, str]] = []
    errors: list[tuple[str, str]] = []

    for order in orders:
        if order.status not in (*ACTIVE, *UNRESOLVED):
            updated.append(order)
            continue

        cumulative = cumulative_filled.get(order.order_id)
        if cumulative is None:
            # 얼마나 채워졌는지 모른다 — 이번 회차는 손대지 않는다.
            updated.append(order)
            skipped.append((order.order_id, "체결 상태를 모른다"))
            continue

        current = order
        already = order.original_quantity - order.remaining_quantity - order.cancelled_quantity
        if (not math.isfinite(cumulative) or not float(cumulative).is_integer()
                or cumulative < already or cumulative > order.original_quantity - order.cancelled_quantity):
            updated.append(order)
            skipped.append((order.order_id, "체결 수량 불일치 — 대사 필요"))
            continue
        delta = int(cumulative) - already
        if delta > 0:
            current = apply_fill(current, filled_quantity=delta, now=now)
        if current.status in (OrderStatus.FILLED, OrderStatus.CANCELLED):
            updated.append(current)
            continue

        if current.status in UNRESOLVED:
            updated.append(current)
            skipped.append((current.order_id, "정정/취소 결과 미확정 — 브로커 대사 필요"))
            continue

        approval = pretrade_check(current)
        if not approval:
            proposed = replace(current, status=OrderStatus.CANCELLED, last_action_at=now)
            action = Action(ActionType.CANCEL, proposed, approval.reason)
            proposed, error = dispatch(action, current) if dispatch else _apply(broker, action, current)
            if error is not None:
                errors.append((current.order_id, error))
            updated.append(proposed)
            actions.append(action)
            continue

        price = market_prices.get(current.entity_id)
        if price is None:
            # 재호가할 가격이 없다. 가격 없이 낼 수 있는 판단은 없다.
            updated.append(current)
            skipped.append((current.order_id, "재호가 기준 시세가 없다"))
            continue

        before_action = current
        current, action = decide(
            current, now=now, market_price=price, params=params
        )
        if action.type is ActionType.WAIT:
            updated.append(current)
            continue

        current, error = dispatch(action, before_action) if dispatch else _apply(broker, action, before_action)
        if error is not None:
            errors.append((current.order_id, error))
        updated.append(current)
        actions.append(action)

    return SupervisionResult(
        orders=tuple(updated),
        actions=tuple(actions),
        skipped=tuple(skipped),
        errors=tuple(errors),
    )


def close(
    orders: Iterable[OpenOrder], broker: Broker, *, now: datetime,
    dispatch: Callable[[Action, OpenOrder], tuple[OpenOrder, str | None]] | None = None,
) -> SupervisionResult:
    """세션 종료 — 남은 미체결을 전부 취소한다. **이월하지 않는다.**

    백테스트의 ``pending()`` 이 어제 주문을 오늘 다시 시도하지 않기 때문이다
    (``backtest/execution.py``). 실전이 이월하면 백테스트가 거짓말이 된다
    (불변식 5).
    """
    actions: list[Action] = []
    errors: list[tuple[str, str]] = []
    updated: list[OpenOrder] = []

    remaining = list(orders)
    closed = dict(
        (order.order_id, (order, action)) for order, action in close_session(remaining, now=now)
    )
    for order in remaining:
        entry = closed.get(order.order_id)
        if entry is None:
            updated.append(order)
            continue
        cancelled, action = entry
        cancelled, error = dispatch(action, order) if dispatch else _apply(broker, action, order)
        if error is not None:
            errors.append((cancelled.order_id, error))
        updated.append(cancelled)
        actions.append(action)

    return SupervisionResult(
        orders=tuple(updated), actions=tuple(actions), errors=tuple(errors)
    )


# -- 내부 -----------------------------------------------------------------------


def _apply(broker: Broker, action: Action, before: OpenOrder) -> tuple[OpenOrder, str | None]:
    """판단 하나를 브로커 호출로 옮긴다. 실패하면 사유를 돌려준다.

    실패·미전송·거부는 확인되지 않은 조치다. 호출자는 unknown 상태로 남기고
    대사 전에는 같은 조치를 반복하지 않는다.
    """
    order = action.order
    pending = replace(order, limit_price=before.limit_price,
                      status=(OrderStatus.MODIFY_UNKNOWN if action.type is ActionType.REPRICE
                              else OrderStatus.CANCEL_UNKNOWN))
    if not order.broker_order_no:
        return pending, "broker_order_no 가 없다 — 정정·취소를 낼 수 없다"
    try:
        if action.type is ActionType.REPRICE:
            ack = broker.modify(
                broker_order_no=order.broker_order_no,
                entity_id=order.entity_id,
                quantity=order.remaining_quantity,
                price=order.limit_price,
            )
        else:
            ack = broker.cancel(
                broker_order_no=order.broker_order_no,
                entity_id=order.entity_id,
                quantity=order.remaining_quantity,
            )
    except RejectedOrder as error:
        return replace(before, retry_count=order.retry_count, last_action_at=order.last_action_at), str(error)
    except BrokerError as error:
        return pending, str(error)
    if not ack.accepted or not ack.sent:
        return pending, ack.rsp_msg or "브로커 조치 미확인 — 거부 또는 미전송"
    return pending, None
