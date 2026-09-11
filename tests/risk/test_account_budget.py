"""Different orders must not spend the same account cash or inventory."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest

from quant_rl_trading.broker import Ack
from quant_rl_trading.executor import pipeline
from quant_rl_trading.executor.orders import PlannedOrder, client_order_id
from quant_rl_trading.replay.clock import ReplayClock
from quant_rl_trading.schemas.order import Order, Side

NOW = datetime(2026, 9, 9, 1, tzinfo=UTC)
SESSION = "KR-2026-09-08"


class Broker:
    def __init__(self):
        self.calls = []

    def submit(self, order, *, as_of):
        self.calls.append(order)
        return Ack(order_id=order.order_id, accepted=True, sent=True, broker_order_no="77")


def intent(entity="KR:A", quantity=60, side=Side.BUY, seq=0, price=1000.0):
    return PlannedOrder(
        order=Order(entity_id=entity, side=side, quantity=quantity, limit_price=price),
        order_id=client_order_id(session=SESSION, entity_id=entity, slice_seq=seq),
        session_id=SESSION,
        slice_seq=seq,
        target_weight=0.6,
    )


def config(store, key, value):
    import json

    store.append(
        "config",
        [
            {
                "entity_id": key,
                "valid_from": NOW - timedelta(days=10),
                "observed_at": NOW - timedelta(days=10),
                "source": "test",
                "revision": 1,
                "value_json": json.dumps(value),
            }
        ],
        ingest_run_id=f"config-{key}",
    )


@pytest.fixture
def fund(store):
    store.seed_config_defaults()
    config(store, "allocator.max_position_weight", 1.0)
    config(store, "execution.max_slippage", 0.0)
    store.append(
        "capital_flows",
        [
            {
                "entity_id": "FUND",
                "valid_from": NOW - timedelta(days=2),
                "observed_at": NOW - timedelta(days=2),
                "source": "test",
                "currency": "KRW",
                "amount": 100_000.0,
                "kind": "deposit",
            }
        ],
        ingest_run_id="capital",
    )
    store.append(
        "fx",
        [
            {
                "entity_id": "FX:USDKRW",
                "valid_from": NOW - timedelta(days=1),
                "observed_at": NOW - timedelta(days=1),
                "source": "test",
                "rate": 1350.0,
            }
        ],
        ingest_run_id="fx",
    )
    store.append(
        "prices",
        [
            {
                "entity_id": entity,
                "valid_from": NOW - timedelta(days=1),
                "observed_at": NOW - timedelta(days=1),
                "source": "test",
                "market": "KR",
                "close": 1000.0,
                "volume": 1e6,
                "value": 1e9,
            }
            for entity in ("KR:A", "KR:B", "KR:C")
        ],
        ingest_run_id="prices",
    )
    return store


def send(store, broker, order):
    return pipeline.submit_orders(
        store,
        ReplayClock(NOW),
        broker,
        planned=[order],
        as_of=NOW,
        market="KR",
    )


def test_different_orders_cannot_spend_cash_twice(fund):
    broker = Broker()
    send(fund, broker, intent())
    acks = send(fund, broker, intent("KR:B"))
    assert len(broker.calls) == 1
    assert acks and not acks[0].accepted


def test_concurrent_different_orders_share_account_budget(fund):
    broker = Broker()
    start = Barrier(2)

    def run(entity):
        start.wait(timeout=5)
        return send(fund, broker, intent(entity))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, ("KR:A", "KR:B")))
    assert len(broker.calls) == 1
    assert sum(ack.accepted for result in results for ack in result) == 1


def test_commission_is_reserved_before_submission(fund):
    broker = Broker()
    acks = send(fund, broker, intent(quantity=100))
    assert not broker.calls  # notional fits exactly, commission does not
    assert not acks[0].accepted


def test_market_buy_without_bounded_price_is_blocked(fund):
    broker = Broker()
    acks = send(fund, broker, intent(quantity=10, price=None))
    assert not broker.calls
    assert not acks[0].accepted


def test_unknown_account_is_not_treated_as_unlimited_cash(store):
    store.seed_config_defaults()
    from tests.executor.test_safety_regressions import seed_price

    seed_price(store, NOW - timedelta(days=1))
    broker = Broker()
    acks = send(store, broker, intent())
    assert not broker.calls
    assert not acks[0].accepted


def test_sells_cannot_exceed_actual_inventory(fund):
    broker = Broker()
    acks = send(fund, broker, intent(side=Side.SELL, quantity=1))
    assert not broker.calls
    assert not acks[0].accepted


def test_all_future_slices_are_reserved_before_first_submit(fund):
    orders = [intent(quantity=30, seq=0), intent(quantity=30, seq=1)]
    clock = ReplayClock(NOW)
    pipeline.record_orders(fund, clock, planned=orders, as_of=NOW, market="KR")
    approved, notes = pipeline.reserve_orders(fund, clock, planned=orders, as_of=NOW, market="KR")
    assert approved == orders and not notes
    assert set(fund.get("orders", as_of=NOW)["status"]) == {"reserved"}
    from tools.release_slices import _planned_rows

    assert len(_planned_rows(fund, as_of=NOW, session_id=SESSION, market="KR")) == 2
    broker = Broker()
    send(fund, broker, orders[0])
    send(fund, broker, intent("KR:B", quantity=50))
    assert len(broker.calls) == 1
    again, notes = pipeline.reserve_orders(fund, clock, planned=orders, as_of=NOW, market="KR")
    assert again == [orders[1]] and not notes


def trade(store, item, quantity, moment=NOW, *, suffix="30"):
    from quant_rl_trading.risk.account import key

    logical = key(item.session_id, item.order.entity_id, item.slice_seq)
    store.append(
        "trades",
        [
            {
                "entity_id": item.order.entity_id,
                "valid_from": moment,
                "observed_at": moment,
                "source": "test",
                "market": "KR",
                "currency": "KRW",
                "side": str(item.order.side),
                "quantity": float(quantity),
                "price": 1000.0,
                "fee": quantity * 0.15,
                "tax": 0.0,
                "order_id": f"{logical}#{suffix}",
            }
        ],
        ingest_run_id=f"fill-{suffix}",
    )


def test_restart_reserves_only_unfilled_remainder(fund):
    from quant_rl_trading.risk import account
    from quant_rl_trading.store import Store

    item = intent()
    send(fund, Broker(), item)
    trade(fund, item, 30)
    reopened = Store(root=fund.root)
    b = account.read(reopened, ReplayClock(NOW), as_of=NOW)
    assert next(iter(b.reservations.values())).quantity == 30
    assert b.holdings == {"KR:A": 30}
    broker = Broker()
    assert send(reopened, broker, intent("KR:B", quantity=35))[0].accepted
    assert len(broker.calls) == 1  # an original-quantity reservation would incorrectly block it


@pytest.mark.parametrize(
    "status",
    [
        "sent",
        "submitting",
        "cancel_unknown",
        "modify_unknown",
        "expired",
        "cancelled",
        "abandoned",
        "filled",
    ],
)
def test_unverified_order_status_never_releases_budget(fund, status):
    from quant_rl_trading.risk import account

    # 같은 거래일 안의 미확인 종결 — 지난 거래일이면 day order 만료로 풀린다(아래 테스트).
    old = NOW - timedelta(hours=2)
    item = intent()
    row = item.row(as_of=old, observed_at=old, market="KR", status=status)
    row["reason"] = "broker_order_no=77" if status != "submitting" else ""
    fund.append("orders", [row], ingest_run_id="old-order")
    b = account.read(fund, ReplayClock(NOW), as_of=NOW)
    assert len(b.reservations) == 1
    broker = Broker()
    assert not send(fund, broker, intent("KR:B"))[0].accepted
    assert not broker.calls


def test_full_fill_releases_reservation_but_keeps_actual_exposure(fund):
    from quant_rl_trading.risk import account

    item = intent()
    send(fund, Broker(), item)
    trade(fund, item, 60, suffix="60")
    b = account.read(fund, ReplayClock(NOW), as_of=NOW)
    assert not b.reservations
    assert b.holdings == {"KR:A": 60}
    assert b.cash["KRW"] == pytest.approx(39_991)


def _process_send(root, entity):
    from quant_rl_trading.store import Store

    return send(Store(root=root), Broker(), intent(entity))[0].accepted


def test_separate_processes_share_account_lock(fund):
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor

    with ProcessPoolExecutor(2, mp_context=multiprocessing.get_context("spawn")) as pool:
        jobs = [pool.submit(_process_send, fund.root, e) for e in ("KR:A", "KR:B")]
        assert sum(job.result(timeout=40) for job in jobs) == 1


@pytest.mark.parametrize("has_bar", [False, True])
def test_simulator_consumes_authorized_slices_and_releases_after_attempt(fund, has_bar):
    from quant_rl_trading.backtest import execution
    from quant_rl_trading.risk import account

    config(fund, "execution.min_order_value", 1.0)
    clock = ReplayClock(NOW)
    item = intent()
    pipeline.record_orders(fund, clock, planned=[item], as_of=NOW, market="KR")
    # Merely recording a plan never creates a simulated fill.
    assert execution.pending(fund, as_of=NOW, session_id=SESSION).empty
    pipeline.reserve_orders(
        fund, clock, planned=[item], as_of=NOW, market="KR", simulation_only=True
    )
    if has_bar:
        fund.append(
            "prices",
            [
                {
                    "entity_id": "KR:A",
                    "valid_from": NOW,
                    "observed_at": NOW,
                    "source": "test",
                    "market": "KR",
                    "close": 980.0,
                    "low": 970.0,
                    "high": 990.0,
                    "volume": 1e6,
                    "value": 980e6,
                }
            ],
            ingest_run_id="execution-price",
        )
    result = execution.run(fund, clock, as_of=NOW, market="KR", session_id=SESSION)
    assert result.requested == 60
    assert result.filled == (60 if has_bar else 0)
    assert set(fund.get("orders", as_of=NOW)["status"]) == {"simulated"}
    b = account.read(fund, clock, as_of=NOW)
    assert not b.reservations
    assert b.holdings == ({"KR:A": 60} if has_bar else {})
    again = execution.run(fund, clock, as_of=NOW, market="KR", session_id=SESSION)
    assert again.filled == result.filled
    assert again.rows_written == 0


def test_repricing_cannot_expand_beyond_account_cash(fund):
    from dataclasses import replace

    from tests.executor.test_safety_regressions import open_order

    from tools.chase_orders import check_account

    order = replace(open_order(), remaining_quantity=100, original_quantity=100)
    decision = check_account(fund, ReplayClock(NOW), order=order, market="KR")
    assert not decision and "cash" in decision.reason


def test_missing_broker_id_is_a_reconciliation_failure(fund):
    from tools.reconcile_fills import missing_broker_ids

    row = intent().row(as_of=NOW, observed_at=NOW, market="KR", status="submitting")
    fund.append("orders", [row], ingest_run_id="unacknowledged")
    assert missing_broker_ids(fund, as_of=NOW, market="KR") == 1


def test_session_close_releases_only_proven_unsent_orders(fund):
    from quant_rl_trading.risk import account

    clock = ReplayClock(NOW)
    orders = [intent(quantity=20, seq=i) for i in range(3)]
    pipeline.record_orders(fund, clock, planned=orders, as_of=NOW, market="KR")
    pipeline.reserve_orders(fund, clock, planned=orders, as_of=NOW, market="KR")
    broker = Broker()
    send(fund, broker, orders[0])
    assert pipeline.withdraw_unsent(fund, clock, session=SESSION, market="KR") == 2
    assert pipeline.withdraw_unsent(fund, clock, session=SESSION, market="KR") == 0
    b = account.read(fund, clock, as_of=NOW)
    assert len(b.reservations) == 1
    send(fund, broker, orders[1])
    assert len(broker.calls) == 1


def test_partial_quantity_does_not_confirm_cancellation(fund):
    from tools.reconcile_fills import unverified_remainders

    item = intent()
    row = item.row(as_of=NOW, observed_at=NOW, market="KR", status="cancel_unknown")
    row["reason"] = "broker_order_no=77"
    fund.append("orders", [row], ingest_run_id="cancel-unknown")
    trade(fund, item, 30, suffix="30")
    assert unverified_remainders(fund, as_of=NOW, market="KR") == 1
    trade(fund, item, 30, suffix="60")
    assert unverified_remainders(fund, as_of=NOW, market="KR") == 0


def test_live_reserved_slices_cannot_be_filled_by_simulator(fund):
    from quant_rl_trading.backtest import execution

    clock = ReplayClock(NOW)
    pipeline.reserve_orders(fund, clock, planned=[intent()], as_of=NOW, market="KR")
    assert execution.pending(fund, as_of=NOW, session_id=SESSION).empty


def test_simulation_reservation_cannot_switch_to_live_submission(fund):
    clock = ReplayClock(NOW)
    item = intent()
    pipeline.reserve_orders(
        fund, clock, planned=[item], as_of=NOW, market="KR", simulation_only=True
    )
    broker = Broker()
    assert not send(fund, broker, item)[0].accepted
    assert not broker.calls


@pytest.mark.parametrize("status", ["abandoned", "filled", "cancelled", "expired"])
def test_previous_day_terminal_order_does_not_reserve(fund, status):
    """지난 거래일의 종결 주문은 거래소가 잔량을 소멸시켰다 — 예약이 아니다.

    2026-09-11 모의계좌: 8/27~9/8 의 abandoned·filled 잔량 317건이 예약으로 남아
    당일 매수 24건이 '현금 부족', 매도 9건이 '재고 초과' 로 막혔다.
    """
    from quant_rl_trading.risk import account

    old = NOW - timedelta(days=3)
    item = intent()
    row = item.row(as_of=old, observed_at=old, market="KR", status=status)
    row["reason"] = "broker_order_no=77"
    fund.append("orders", [row], ingest_run_id="old-order")
    assert account.read(fund, ReplayClock(NOW), as_of=NOW).reservations == {}


def test_previous_day_unknown_cancel_still_reserves(fund):
    """cancel_unknown 은 날짜가 지나도 풀지 않는다 — 확인 없는 취소는 취소가 아니다."""
    from quant_rl_trading.risk import account

    old = NOW - timedelta(days=3)
    item = intent()
    row = item.row(as_of=old, observed_at=old, market="KR", status="cancel_unknown")
    row["reason"] = "broker_order_no=77"
    fund.append("orders", [row], ingest_run_id="old-order")
    assert len(account.read(fund, ReplayClock(NOW), as_of=NOW).reservations) == 1
