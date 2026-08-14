"""미체결 주문의 **생애주기**. 순수 상태기계 — 네트워크도 창고도 안 만진다.

``orders.py`` 가 주문을 만들고, ``pipeline.py`` 가 그걸 창고에 적는다. 그
다음이 빠져 있었다 — 낸 주문이 안 채워지면 언제 다시 부를지, 몇 번까지
쫓아갈지, 언제 포기할지. 이 파일은 그 판단만 한다. 실제로 브로커에
``modify``·``cancel`` 을 부르는 것은 호출자(브로커 어댑터) 몫이다. 여기서는
"무엇을 해야 하는가" 만 결정한다 — 그래야 시간과 무관하게 테스트할 수 있다
(불변식 2: ``datetime.now()`` 금지, 판단은 전부 주입된 ``now`` 로 한다).

## 정정이 먼저다

취소 후 재주문하면 그 사이에 시세가 뛰어 취소만 되고 못 사는 구간이 생긴다.
그래서 미체결에 대한 기본 응답은 ``REPRICE`` (``Broker.modify`` 가 받는 자리)
이지, 취소가 아니다. 취소는 재시도가 바닥났거나(``CANCEL``), 세션이
끝났거나(``CANCEL``), 슬리피지 상한을 넘어야만(``ABANDON``) 나온다.

## 왜 포기(ABANDON)를 취소(CANCEL)와 구분하는가

M3-kickoff 3-3: "슬리피지 상한을 넘으면 주문하지 않음". 상한을 넘어 쫓아가는
재호가는 백테스트가 가정한 적이 없는 가격에 체결시키는 것과 같다 — 그 차이는
전부 성과에서 빠진다. 그런데 재시도 소진·세션 종료로 인한 취소와 슬리피지
초과로 인한 포기는 원인이 다르고, 호출자(Auditor·대시보드)가 "왜 못 샀나"를
구분해서 봐야 한다. 그래서 ``ActionType`` 을 둘로 나눈다.

## 미체결은 이월하지 않는다

``backtest/execution.py`` 의 ``pending()`` 은 오늘 세션의 주문만 골라 오늘
봉에 맞춘다 — 어제 못 채운 주문을 오늘 다시 시도하지 않는다. 실전이 이 규칙과
다르게 굴면 백테스트가 거짓말이 된다(불변식 5). 그래서 ``close_session`` 은
세션이 끝나면 재시도 횟수·타이머와 무관하게 남은 미체결을 전부 취소로 접는다.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

from quant_rl_trading.executor import orders as orders_module
from quant_rl_trading.executor.orders import PlannedOrder
from quant_rl_trading.schemas.order import Side

if TYPE_CHECKING:
    from quant_rl_trading.store import Store

__all__ = [
    "Action",
    "ActionType",
    "LifecycleParams",
    "OpenOrder",
    "OrderStatus",
    "apply_fill",
    "close_session",
    "decide",
    "open_order_from_planned",
]


class OrderStatus(Enum):
    """미체결 주문이 있을 수 있는 상태. 전이는 ``decide``·``apply_fill``·
    ``close_session`` 세 함수 안에만 있다 — 상태를 문자열로 흩뿌리지 않는다."""

    #: 접수됐고 아직 하나도 안 채워졌다.
    SUBMITTED = "submitted"
    #: 일부만 채워졌다. 잔량이 남아 있으므로 계속 생애주기 대상이다.
    PARTIALLY_FILLED = "partially_filled"
    #: 잔량까지 다 채워졌다. 종결.
    FILLED = "filled"
    #: 재시도 소진 또는 세션 종료로 취소됐다. 종결.
    CANCELLED = "cancelled"
    #: 슬리피지 상한을 넘어 포기했다. 종결. CANCELLED 와 원인이 다르므로 구분한다.
    ABANDONED = "abandoned"


#: 더 이상 생애주기 판단의 대상이 아닌 상태.
_TERMINAL = frozenset(
    {OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.ABANDONED}
)


class ActionType(Enum):
    """``decide`` 가 내는 판단. 호출자는 이 값으로 브로커를 부른다."""

    #: retry_after_sec 이 아직 안 지났다. 아무것도 안 한다.
    WAIT = "wait"
    #: ``Broker.modify`` 를 부른다 — 취소 후 재주문이 아니라 정정.
    REPRICE = "reprice"
    #: ``Broker.cancel`` 을 부른다 — 재시도 소진 또는 세션 종료.
    CANCEL = "cancel"
    #: ``Broker.cancel`` 을 부르되, 원인이 슬리피지 상한 초과다.
    ABANDON = "abandon"


@dataclass(frozen=True)
class LifecycleParams:
    """생애주기 임계치. 전부 config 의 ``execution.*`` 에서 온다 (하드코딩 금지)."""

    retry_after_sec: int
    max_retries: int
    max_slippage: float

    @classmethod
    def from_store(cls, store: Store, *, as_of: datetime) -> LifecycleParams:
        return cls(
            retry_after_sec=int(
                store.config("execution.retry_after_sec", as_of=as_of)
            ),
            max_retries=int(store.config("execution.max_retries", as_of=as_of)),
            max_slippage=float(store.config("execution.max_slippage", as_of=as_of)),
        )


@dataclass(frozen=True)
class OpenOrder:
    """생애주기 관리 중인 미체결 주문 한 건. 동결 — 전이는 새 인스턴스를 만든다."""

    order_id: str
    entity_id: str
    side: Side
    #: 최초 계획 시점의 기준가. 슬리피지 상한은 **이 값 대비**로 잰다 —
    #: 재호가할 때마다 기준을 옮기면 상한이 시세를 따라 계속 늘어나 사실상
    #: 무의미해진다.
    reference_price: float
    #: 현재 지정가. 재호가마다 갱신된다.
    limit_price: float
    original_quantity: int
    #: 아직 못 채운 수량. 재호가는 이 수량만 낸다 — 원 수량으로 다시 내면
    #: 부분체결분만큼 초과 매수가 된다.
    remaining_quantity: int
    retry_count: int
    #: 마지막으로 **우리가 브로커에 조치한** 시각(제출·재호가). 다음 재호가까지의
    #: 대기 시간을 여기서부터 잰다. 체결로는 갱신하지 않는다 — 참고 구현
    #: (ls_kr_rl_trader/broker/order.py cancel_stale_live_orders)의 TTL 은
    #: 원 제출시각(``ts``)에서만 재고, 부분체결이 그 시계를 늘려주지 않는다.
    #: 오히려 부분체결 + ``cancel_partial_remainder`` 조합은 TTL 을 기다리지
    #: 않고 즉시 취소 대상으로 넘긴다(order.py:446-448) — 체결이 "더 기다려도
    #: 된다"는 신호가 아니라는 뜻이다. 여기서도 같은 원칙을 따른다: 체결은
    #: 수동적 관측이지 우리 조치가 아니므로 재호가 타이머를 늘리지 않는다.
    last_action_at: datetime
    status: OrderStatus
    broker_order_no: str | None = None


@dataclass(frozen=True)
class Action:
    """``decide``/``close_session`` 이 호출자에게 돌려주는 지시.

    ``order`` 는 이미 전이가 반영된 새 상태다 — 호출자는 브로커를 부른 뒤
    이 값으로 자기 쪽 상태를 갱신하면 된다.
    """

    type: ActionType
    order: OpenOrder
    reason: str = ""


def open_order_from_planned(
    planned: PlannedOrder,
    *,
    reference_price: float,
    now: datetime,
    broker_order_no: str | None = None,
) -> OpenOrder:
    """``PlannedOrder`` 를 생애주기 관리 대상으로 등록한다.

    시장가 주문(``limit_price is None``)은 대상이 아니다 — 시장가는 청산과
    킬스위치 발동 때만 쓰고(orders.py), 그 경로는 즉시 체결·거부로 끝나
    재호가할 여지가 없다.
    """
    if planned.order.limit_price is None:
        raise ValueError(
            "시장가 주문은 생애주기 관리 대상이 아니다 — 즉시 체결·거부로 끝난다"
        )
    return OpenOrder(
        order_id=planned.order_id,
        entity_id=planned.order.entity_id,
        side=planned.order.side,
        reference_price=reference_price,
        limit_price=planned.order.limit_price,
        original_quantity=planned.order.quantity,
        remaining_quantity=planned.order.quantity,
        retry_count=0,
        last_action_at=now,
        status=OrderStatus.SUBMITTED,
        broker_order_no=broker_order_no,
    )


def apply_fill(order: OpenOrder, *, filled_quantity: int, now: datetime) -> OpenOrder:
    """체결을 반영한다. **부분체결이 정상 경로다** — 잔량이 남으면 계속
    ``PARTIALLY_FILLED`` 로 살아 있고, 다음 ``decide`` 호출에서 잔량만
    재호가 대상이 된다.

    ``now`` 는 종결 여부 판단(호출자가 이 시각에 체결을 관측했다는 사실)에만
    쓰이고, ``last_action_at`` 은 건드리지 않는다 — 체결은 우리가 브로커에
    조치한 게 아니라 관측한 것이다. 재호가 타이머를 체결로 리셋하면, 조금씩
    계속 체결되는 주문은 ``decide`` 가 영영 재호가·취소를 판단할 기회를 못
    받는다(``OpenOrder.last_action_at`` 문서 참고, 참고 구현의 TTL 원칙과 같다).
    """
    if order.status in _TERMINAL:
        raise ValueError(f"종결된 주문에는 체결을 반영할 수 없다: {order.status}")
    if filled_quantity <= 0:
        raise ValueError("체결 수량은 0보다 커야 한다")
    if filled_quantity > order.remaining_quantity:
        raise ValueError("체결 수량이 잔량을 초과했다")
    remaining = order.remaining_quantity - filled_quantity
    status = OrderStatus.FILLED if remaining == 0 else OrderStatus.PARTIALLY_FILLED
    return replace(order, remaining_quantity=remaining, status=status)


def decide(
    order: OpenOrder,
    *,
    now: datetime,
    market_price: float,
    params: LifecycleParams,
) -> tuple[OpenOrder, Action]:
    """미체결 한 건에 대해 지금 무엇을 할지 정한다. **입력만으로 결정된다** —
    같은 (order, now, market_price, params) 는 항상 같은 결과를 낸다.

    순서:
    1. retry_after_sec 이 아직 안 지났으면 대기.
    2. max_retries 를 다 썼으면 취소.
    3. 재호가 가격이 슬리피지 상한(원 기준가 대비)을 넘으면 포기.
    4. 아니면 정정 — 시세를 쫓되 상한을 넘지 않는 값으로.
    """
    if order.status not in (OrderStatus.SUBMITTED, OrderStatus.PARTIALLY_FILLED):
        raise ValueError(f"종결된 주문은 재호가 판단 대상이 아니다: {order.status}")

    elapsed = (now - order.last_action_at).total_seconds()
    if elapsed < params.retry_after_sec:
        return order, Action(type=ActionType.WAIT, order=order)

    if order.retry_count >= params.max_retries:
        cancelled = replace(order, status=OrderStatus.CANCELLED, last_action_at=now)
        return cancelled, Action(
            type=ActionType.CANCEL,
            order=cancelled,
            reason=f"최대 재시도({params.max_retries}회) 소진",
        )

    # 원 기준가 대비 상한 — orders.limit_price 와 같은 계산으로 맞춘다.
    cap = orders_module.limit_price(
        reference=order.reference_price, side=order.side, max_slippage=params.max_slippage
    )
    if order.side is Side.BUY:
        exceeded = market_price > cap
        new_price = min(market_price, cap)
    else:
        exceeded = market_price < cap
        new_price = max(market_price, cap)

    if exceeded:
        abandoned = replace(order, status=OrderStatus.ABANDONED, last_action_at=now)
        return abandoned, Action(
            type=ActionType.ABANDON,
            order=abandoned,
            reason="슬리피지 상한 초과 — 포기",
        )

    repriced = replace(
        order,
        limit_price=new_price,
        retry_count=order.retry_count + 1,
        last_action_at=now,
    )
    return repriced, Action(
        type=ActionType.REPRICE,
        order=repriced,
        reason=f"재호가 {repriced.retry_count}/{params.max_retries}",
    )


def close_session(
    orders: Iterable[OpenOrder], *, now: datetime
) -> list[tuple[OpenOrder, Action]]:
    """세션 종료. **미체결을 이월하지 않는다** (backtest/execution.py 의
    ``pending()`` 과 같은 규칙 — 백테스트와 실전이 다르게 굴면 백테스트가
    거짓말이 된다, 불변식 5). 재시도 타이머·횟수와 무관하게 즉시 취소한다."""
    closed: list[tuple[OpenOrder, Action]] = []
    for order in orders:
        if order.status in _TERMINAL:
            continue
        cancelled = replace(order, status=OrderStatus.CANCELLED, last_action_at=now)
        closed.append(
            (
                cancelled,
                Action(
                    type=ActionType.CANCEL,
                    order=cancelled,
                    reason="세션 종료 — 미체결 이월 없음",
                ),
            )
        )
    return closed
