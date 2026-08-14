"""생애주기 판단이 **실제로 브로커까지 간다**는 배선 계약.

``lifecycle.py`` 의 단위 테스트는 판단만 본다 — 그 판단을 아무도 부르지
않아도 전부 통과한다. 실제로 그랬다: 재호가 타이머·최대 재시도·부분체결
잔량 처리가 전부 코드로만 존재하고 호출부가 없었다. 이 파일이 고정하는 것은
"무엇을 판단하는가" 가 아니라 **"판단이 broker.modify/cancel 로 나가는가"** 다.

여기서 못박는 것:
1. 미체결이 타이머를 넘기면 ``modify`` 가 실제로 불린다
2. 조금씩 계속 체결돼도 재호가 판단을 받는다 (부분체결 타이머 회귀)
3. 재호가는 **잔량만** 낸다 — 원 수량으로 다시 내면 초과 매수다
4. 최대 재시도를 소진하면 ``cancel`` 이 불린다
5. 체결 상태를 모르면 아무것도 부르지 않는다
6. 세션이 끝나면 남은 미체결이 전부 취소된다
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from quant_rl_trading.broker import Ack, BrokerError
from quant_rl_trading.broker.fills import FillOutcome, FillState, SyncResult
from quant_rl_trading.executor import supervise
from quant_rl_trading.executor.lifecycle import (
    ActionType,
    LifecycleParams,
    OpenOrder,
    OrderStatus,
)
from quant_rl_trading.executor.orders import SliceParams, plan_slices
from quant_rl_trading.schemas.order import Side

NOW = datetime(2026, 8, 14, 1, 0, tzinfo=UTC)
PARAMS = LifecycleParams(retry_after_sec=300, max_retries=2, max_slippage=0.02)


@dataclass
class FakeBroker:
    """무엇이 실제로 불렸는지만 기록한다."""

    modified: list[tuple[str, int, float]] = field(default_factory=list)
    cancelled: list[tuple[str, int]] = field(default_factory=list)
    fail: bool = False

    def submit(self, order: object, *, as_of: datetime) -> Ack:  # pragma: no cover
        raise NotImplementedError

    def modify(
        self, *, broker_order_no: str, entity_id: str, quantity: int, price: float
    ) -> Ack:
        if self.fail:
            raise BrokerError("정정 전송 실패")
        self.modified.append((broker_order_no, quantity, price))
        return Ack(order_id=broker_order_no, accepted=True)

    def cancel(self, *, broker_order_no: str, entity_id: str, quantity: int) -> Ack:
        if self.fail:
            raise BrokerError("취소 전송 실패")
        self.cancelled.append((broker_order_no, quantity))
        return Ack(order_id=broker_order_no, accepted=True)


def open_order(**overrides) -> OpenOrder:  # type: ignore[no-untyped-def]
    base = {
        "order_id": "order-1",
        "entity_id": "KR:005930",
        "side": Side.BUY,
        "reference_price": 70_000.0,
        "limit_price": 70_000.0,
        "original_quantity": 100,
        "remaining_quantity": 100,
        "retry_count": 0,
        "last_action_at": NOW,
        "status": OrderStatus.SUBMITTED,
        "broker_order_no": "700001",
    }
    base.update(overrides)
    return OpenOrder(**base)  # type: ignore[arg-type]


def run(
    orders: list[OpenOrder],
    broker: FakeBroker,
    *,
    at: datetime,
    filled: dict[str, float],
    price: float = 70_500.0,
) -> supervise.SupervisionResult:
    return supervise.step(
        orders,
        broker,
        now=at,
        market_prices={"KR:005930": price},
        cumulative_filled=filled,
        params=PARAMS,
    )


def test_reprice_actually_reaches_the_broker() -> None:
    """타이머를 넘긴 미체결은 ``modify`` 로 나간다 — 판단만 하고 끝나지 않는다."""
    broker = FakeBroker()

    result = run(
        [open_order()], broker, at=NOW + timedelta(seconds=301), filled={"order-1": 0.0}
    )

    assert [action.type for action in result.actions] == [ActionType.REPRICE]
    assert len(broker.modified) == 1
    order_no, quantity, price = broker.modified[0]
    assert order_no == "700001"
    assert quantity == 100
    assert price == 70_500.0


def test_partial_fills_still_get_a_reprice_decision() -> None:
    """**부분체결 타이머 회귀 테스트.**

    조금씩 계속 체결되는 주문이 재호가 판단을 영영 못 받으면, 잔량은 세션이
    끝날 때까지 낡은 호가에 걸린 채 남는다. 체결은 우리 조치가 아니므로
    타이머를 리셋하지 않는다 — 여기서 실제로 그런지 본다.
    """
    broker = FakeBroker()
    orders = [open_order()]

    # 회차 1: 타이머 전에 30주 체결. 재호가는 아직 없다.
    first = run(orders, broker, at=NOW + timedelta(seconds=100), filled={"order-1": 30.0})
    assert first.actions == ()
    assert first.orders[0].status is OrderStatus.PARTIALLY_FILLED
    assert first.orders[0].remaining_quantity == 70

    # 회차 2: 또 조금 체결됐지만 최초 조치 후 타이머는 지났다 → 재호가.
    second = run(
        first.open, broker, at=NOW + timedelta(seconds=301), filled={"order-1": 45.0}
    )

    assert [action.type for action in second.actions] == [ActionType.REPRICE]
    # **잔량만 낸다.** 원 수량(100)으로 다시 내면 45주를 초과 매수한다.
    assert broker.modified == [("700001", 55, 70_500.0)]
    assert second.orders[0].remaining_quantity == 55


def test_fully_filled_order_is_left_alone() -> None:
    broker = FakeBroker()

    result = run(
        [open_order()], broker, at=NOW + timedelta(seconds=999), filled={"order-1": 100.0}
    )

    assert result.orders[0].status is OrderStatus.FILLED
    assert result.actions == ()
    assert broker.modified == [] and broker.cancelled == []
    assert result.open == ()


def test_retries_run_out_and_the_cancel_is_sent() -> None:
    broker = FakeBroker()
    order = open_order(retry_count=PARAMS.max_retries)

    result = run(
        [order], broker, at=NOW + timedelta(seconds=301), filled={"order-1": 0.0}
    )

    assert [action.type for action in result.actions] == [ActionType.CANCEL]
    assert broker.cancelled == [("700001", 100)]
    assert result.open == ()


def test_slippage_cap_abandons_instead_of_chasing() -> None:
    broker = FakeBroker()

    result = run(
        [open_order()],
        broker,
        at=NOW + timedelta(seconds=301),
        filled={"order-1": 0.0},
        # 기준가 70,000 · 상한 2% → 71,400 을 넘는다.
        price=75_000.0,
    )

    assert [action.type for action in result.actions] == [ActionType.ABANDON]
    assert broker.cancelled == [("700001", 100)]


def test_unknown_fill_state_touches_nothing() -> None:
    """얼마나 채워졌는지 모르는 채로 정정하면 체결된 수량을 다시 산다."""
    broker = FakeBroker()

    result = run([open_order()], broker, at=NOW + timedelta(seconds=301), filled={})

    assert result.actions == ()
    assert broker.modified == [] and broker.cancelled == []
    assert result.skipped == (("order-1", "체결 상태를 모른다"),)
    assert result.orders[0].status is OrderStatus.SUBMITTED


def test_missing_market_price_is_reported_not_guessed() -> None:
    broker = FakeBroker()

    result = supervise.step(
        [open_order()],
        broker,
        now=NOW + timedelta(seconds=301),
        market_prices={},
        cumulative_filled={"order-1": 0.0},
        params=PARAMS,
    )

    assert result.actions == ()
    assert result.skipped == (("order-1", "재호가 기준 시세가 없다"),)


def test_broker_failure_is_reported_and_not_retried_immediately() -> None:
    """전송 실패해도 상태 전이는 되돌리지 않는다 — 되돌리면 같은 정정이
    두 번 나갈 수 있다."""
    broker = FakeBroker(fail=True)

    result = run(
        [open_order()], broker, at=NOW + timedelta(seconds=301), filled={"order-1": 0.0}
    )

    assert result.errors == (("order-1", "정정 전송 실패"),)
    assert result.orders[0].retry_count == 1
    assert result.orders[0].last_action_at == NOW + timedelta(seconds=301)


def test_close_session_cancels_everything_left() -> None:
    """미체결은 이월하지 않는다 (불변식 5 — 백테스트가 그렇게 굴기 때문)."""
    broker = FakeBroker()
    orders = [
        open_order(),
        open_order(
            order_id="order-2",
            broker_order_no="700002",
            remaining_quantity=40,
            status=OrderStatus.PARTIALLY_FILLED,
        ),
        open_order(order_id="order-3", broker_order_no="700003", status=OrderStatus.FILLED),
    ]

    result = supervise.close(orders, broker, now=NOW + timedelta(hours=6))

    assert sorted(broker.cancelled) == [("700001", 100), ("700002", 40)]
    assert {item.order_id for item in result.open} == set()
    statuses = {item.order_id: item.status for item in result.orders}
    assert statuses["order-3"] is OrderStatus.FILLED


def test_cumulative_from_sync_drops_unknown_instead_of_zeroing_it() -> None:
    """체결 조회가 "모른다" 를 내면 그 주문은 지도에서 빠진다 — 0 으로 채우면
    ``step`` 이 "안 채워졌다" 를 근거로 재호가·취소를 낸다."""
    result = SyncResult(
        outcomes=(
            FillOutcome("order-1", FillState.RECORDED, cumulative_quantity=30.0),
            FillOutcome("order-2", FillState.UNKNOWN, detail="브로커 응답에 주문 없음"),
            FillOutcome("order-3", FillState.UNCHANGED, cumulative_quantity=0.0),
        ),
        rows_written=1,
    )

    assert supervise.cumulative_from_sync(result) == {"order-1": 30.0, "order-3": 0.0}


def test_register_only_takes_orders_that_can_be_chased() -> None:
    """paper·거부·시장가는 생애주기 대상이 아니다 — 정정할 방법이 없거나
    이미 끝난 주문이다."""
    slice_params = SliceParams(slice_count=1, slice_interval_sec=0, max_slippage=0.02)
    limit = plan_slices(
        entity_id="KR:005930",
        side=Side.BUY,
        quantity=100,
        reference_price=70_000.0,
        target_weight=0.05,
        session="KR-2026-08-14",
        params=slice_params,
    )
    market = plan_slices(
        entity_id="KR:000660",
        side=Side.SELL,
        quantity=10,
        reference_price=200_000.0,
        target_weight=0.0,
        session="KR-2026-08-14",
        params=slice_params,
        market_order=True,
    )
    acks = [
        Ack(order_id=limit[0].order_id, accepted=True, broker_order_no="700001"),
        Ack(order_id=market[0].order_id, accepted=True, broker_order_no="700002"),
    ]

    registered = supervise.register(
        limit + market,
        acks,
        now=NOW,
        reference_prices={"KR:005930": 70_000.0, "KR:000660": 200_000.0},
    )

    assert [item.entity_id for item in registered] == ["KR:005930"]
    assert registered[0].broker_order_no == "700001"
    assert registered[0].reference_price == 70_000.0


def test_register_skips_paper_acks() -> None:
    """paper 는 ``broker_order_no`` 가 없다 — 보내지 않은 주문을 쫓아갈 수 없다."""
    slice_params = SliceParams(slice_count=1, slice_interval_sec=0, max_slippage=0.02)
    planned = plan_slices(
        entity_id="KR:005930",
        side=Side.BUY,
        quantity=100,
        reference_price=70_000.0,
        target_weight=0.05,
        session="KR-2026-08-14",
        params=slice_params,
    )
    acks = [Ack(order_id=planned[0].order_id, accepted=True, sent=False)]

    assert (
        supervise.register(
            planned, acks, now=NOW, reference_prices={"KR:005930": 70_000.0}
        )
        == []
    )
