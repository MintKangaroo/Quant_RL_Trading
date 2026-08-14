"""낸 주문을 **끝까지 지켜보는 루프**. ``lifecycle.py`` 의 판단을 실제로
브로커 호출로 옮기는 자리다.

## ⚠️ 2026-08-15 — 이 모듈은 아직 아무도 안 부른다

``register``·``step``·``cumulative_from_sync`` 를 부르는 프로덕션 코드가
**0건**이다. ``executor/__init__.py`` 의 재수출뿐이라 죽은 코드 탐지
(``find_dead_code.py``)도 재수출을 참조로 세서 못 잡았다. 아래 "왜 이 파일이
필요했나" 절이 과거형("~했다")으로 적힌 문제들은 **여전히 현재형이다** —
이 파일이 생겼다고 저절로 배선되지 않는다.

지금은 무해하다: 백테스트·shadow 둘 다 ``PaperBroker`` 를 쓰는데 ``register()``
는 ``broker_order_no`` 가 없는 주문(paper)을 자동으로 걸러 아무것도 등록하지
않는다(``tests/executor/test_supervise.py::test_register_skips_paper_acks``).
하지만 ``LSBroker``(실전 appkey)가 붙는 순간부터는 **이 배선 없이는 재호가·
취소가 실제로 안 나간다.** 배선 계획은 `docs/milestones.md` M3 절 6번,
검증 절차는 `docs/live-order-checklist.md` 를 본다.

## 왜 이 파일이 필요했나

``lifecycle.py`` 는 "지금 무엇을 해야 하는가" 를 순수하게 판단한다 — 재호가
타이머, 최대 재시도, 부분체결 잔량, 슬리피지 상한, 세션 종료. 그런데 그
판단을 **부르는 코드가 어디에도 없었다.** ``pipeline.submit_orders`` 는 세션에
한 번 주문을 내보내고 끝나고, 체결 조회(``broker/fills.py``)는 검증 도구
하나만 부른다. 그래서 다음이 전부 코드로만 존재했다(**위 경고 참고 — 지금도
그렇다**):

- 미체결 N 분 후 재호가 → 아무도 재호가하지 않았다
- 최대 재시도 후 취소 → 아무도 취소하지 않았다
- 부분체결 잔량 재호가 → 잔량은 세션이 끝날 때까지 그대로 남았다

이 모듈은 그 판단을 브로커 호출로 옮기는 **배선 부품**이다 — 이 모듈
자체가 자동으로 실행되는 배선은 아니다. 그 차이가 이 경고의 핵심이다.
여전히 ``datetime.now()`` 를 부르지 않고(불변식 2), ``Broker`` 프로토콜만
알기 때문에 언젠가 배선되면 paper·shadow·실전이 같은 코드를 탄다(불변식 5).
**AI 는 없다** (불변식 6).

## 모르는 것은 건드리지 않는다

체결 조회가 :class:`~quant_rl_trading.broker.fills.FillState.UNKNOWN` 을 내면
그 주문은 이번 회차에서 **아무 조치도 하지 않는다.** 얼마나 채워졌는지 모르는
채로 정정을 내면 이미 체결된 수량까지 다시 사는 꼴이 되고, 그건 되돌릴 수
없다. "모른다" 를 "0 체결" 로 읽는 순간 장부와 주문이 같이 틀어진다
(``broker/fills.py`` 와 같은 원칙).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from quant_rl_trading.broker import BrokerError
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
    from quant_rl_trading.executor.orders import PlannedOrder

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
            if item.status in (OrderStatus.SUBMITTED, OrderStatus.PARTIALLY_FILLED)
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
    out: dict[str, float] = {}
    for outcome in result.outcomes:
        if outcome.cumulative_quantity is None:
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
        if order.status not in (OrderStatus.SUBMITTED, OrderStatus.PARTIALLY_FILLED):
            updated.append(order)
            continue

        cumulative = cumulative_filled.get(order.order_id)
        if cumulative is None:
            # 얼마나 채워졌는지 모른다 — 이번 회차는 손대지 않는다.
            updated.append(order)
            skipped.append((order.order_id, "체결 상태를 모른다"))
            continue

        current = order
        already = order.original_quantity - order.remaining_quantity
        delta = int(cumulative) - already
        if delta > 0:
            delta = min(delta, current.remaining_quantity)
            current = apply_fill(current, filled_quantity=delta, now=now)
        if current.status is OrderStatus.FILLED:
            updated.append(current)
            continue

        price = market_prices.get(current.entity_id)
        if price is None:
            # 재호가할 가격이 없다. 가격 없이 낼 수 있는 판단은 없다.
            updated.append(current)
            skipped.append((current.order_id, "재호가 기준 시세가 없다"))
            continue

        current, action = decide(
            current, now=now, market_price=price, params=params
        )
        if action.type is ActionType.WAIT:
            updated.append(current)
            continue

        error = _apply(broker, action)
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
    orders: Iterable[OpenOrder], broker: Broker, *, now: datetime
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
        error = _apply(broker, action)
        if error is not None:
            errors.append((cancelled.order_id, error))
        updated.append(cancelled)
        actions.append(action)

    return SupervisionResult(
        orders=tuple(updated), actions=tuple(actions), errors=tuple(errors)
    )


# -- 내부 -----------------------------------------------------------------------


def _apply(broker: Broker, action: Action) -> str | None:
    """판단 하나를 브로커 호출로 옮긴다. 실패하면 사유를 돌려준다.

    **실패해도 상태 전이는 되돌리지 않는다.** ``BrokerError`` 는 "나갔는지
    모른다" 는 뜻이고(``broker/__init__.py``), 되돌려서 다음 회차에 또 내면
    같은 정정이 두 번 나갈 수 있다. 되돌리지 않으면 재시도 횟수를 한 번 쓰고
    타이머가 리셋되므로, 최악의 경우 재호가 한 번을 잃을 뿐이다 — 중복 주문과
    비교할 수 없는 크기의 손해다.
    """
    order = action.order
    if not order.broker_order_no:
        return "broker_order_no 가 없다 — 정정·취소를 낼 수 없다"
    try:
        if action.type is ActionType.REPRICE:
            broker.modify(
                broker_order_no=order.broker_order_no,
                entity_id=order.entity_id,
                quantity=order.remaining_quantity,
                price=order.limit_price,
            )
        else:
            broker.cancel(
                broker_order_no=order.broker_order_no,
                entity_id=order.entity_id,
                quantity=order.remaining_quantity,
            )
    except BrokerError as error:
        return str(error)
    return None
