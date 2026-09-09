"""Audited failures: current-time gates, stale data and uncertain cancellation."""

from datetime import UTC, datetime, timedelta

import pytest

from quant_rl_trading.broker import Ack, BrokerError
from quant_rl_trading.executor import guards, pipeline, supervise
from quant_rl_trading.executor.lifecycle import LifecycleParams, OpenOrder, OrderStatus
from quant_rl_trading.executor.orders import PlannedOrder
from quant_rl_trading.replay.clock import ReplayClock
from quant_rl_trading.schemas.order import Order, Side
from quant_rl_trading.store.memo import MemoStore

NOW = datetime(2026, 9, 9, 1, tzinfo=UTC)


class RecordingBroker:
    def __init__(self):
        self.submitted = []
        self.cancelled = []

    def submit(self, order, *, as_of):
        self.submitted.append((order.order_id, as_of))
        return Ack(order_id=order.order_id, accepted=True, broker_order_no="100")

    def cancel(self, **kwargs):
        self.cancelled.append(kwargs)
        raise BrokerError("cancel timeout")

    def modify(self, **kwargs):
        raise BrokerError("modify timeout")


def planned(side=Side.BUY, seq=0):
    return PlannedOrder(
        order=Order(entity_id="KR:A", side=side, quantity=10, limit_price=1000),
        order_id=f"KR-2026-09-08|KR:A|{seq}",
        session_id="KR-2026-09-08",
        slice_seq=seq,
        target_weight=0.1,
    )


def seed_price(store, moment):
    store.append(
        "prices",
        [
            {
                "entity_id": "KR:A",
                "valid_from": moment,
                "observed_at": moment,
                "source": "test",
                "market": "KR",
                "open": 1000.0,
                "high": 1000.0,
                "low": 1000.0,
                "close": 1000.0,
                "volume": 1e6,
                "value": 1e9,
                "adj_factor": None,
            }
        ],
        ingest_run_id="price",
    )


def test_kill_switch_blocks_released_slices_at_execution_time(store):
    store.seed_config_defaults()
    old = NOW - timedelta(days=1)
    seed_price(store, old)
    orders = [planned(), planned(Side.SELL, 1)]
    pipeline.record_orders(store, ReplayClock(old), planned=orders, as_of=old, market="KR")
    guards.engage(store, as_of=NOW, observed_at=NOW, reason="risk limit", by="test")
    broker = RecordingBroker()
    acks = pipeline.submit_orders(
        store,
        ReplayClock(NOW),
        broker,
        planned=orders,
        as_of=old,
        market="KR",
    )
    assert broker.submitted == [(orders[1].order_id, NOW)]
    assert any(not ack.accepted and "킬스위치" in (ack.rsp_msg or "") for ack in acks)
    frame = store.get("orders", as_of=NOW)
    assert frame[frame["side"] == "buy"].iloc[-1]["status"] == "risk_blocked"


def test_stale_market_data_blocked(store):
    store.seed_config_defaults()
    seed_price(store, NOW - timedelta(days=2))
    assert not guards.check_data_quality(store, as_of=NOW, market="KR", entities=["KR:A"])


def test_pretrade_kill_read_is_not_hidden_by_session_cache(store):
    store.seed_config_defaults()
    cached = MemoStore(store)
    assert guards.check_killswitch(cached, as_of=NOW)
    guards.engage(store, as_of=NOW, observed_at=NOW, reason="new latch", by="test")
    assert not guards.check_pretrade(
        cached,
        as_of=NOW,
        market="KR",
        entity_id="KR:A",
        side=Side.BUY,
    )


def test_us_utc_session_label_is_not_shifted_to_previous_day(store):
    store.seed_config_defaults()
    label = datetime(2026, 9, 8, tzinfo=UTC)
    store.append(
        "prices",
        [
            {
                "entity_id": "US:AAPL",
                "valid_from": label,
                "observed_at": label + timedelta(hours=21),
                "source": "test",
                "market": "US",
                "open": 100.0,
                "high": 100.0,
                "low": 100.0,
                "close": 100.0,
                "volume": 1e6,
                "value": 1e8,
                "adj_factor": None,
            }
        ],
        ingest_run_id="us-session",
    )
    assert guards.check_data_quality(store, as_of=NOW, market="US", entities=["US:AAPL"])


def test_pipeline_execution_clock_is_separate_from_signal_clock(store):
    store.seed_config_defaults()
    old = NOW - timedelta(days=1)
    seed_price(store, old)
    guards.engage(store, as_of=NOW, observed_at=NOW, reason="new latch", by="test")
    broker = RecordingBroker()
    result = pipeline.run(
        store,
        ReplayClock(old),
        as_of=old,
        market="KR",
        targets=[pipeline.Target("KR:A", weight=0.1, price=1000, adv_value=1e9)],
        holdings={},
        equity=1e7,
        cash=1e7,
        broker=broker,
        execution_clock=ReplayClock(NOW),
    )
    assert result.planned  # historical signal still creates the original intent
    assert not broker.submitted
    assert result.acks and not result.acks[0].accepted


def test_friday_close_is_fresh_on_monday_morning(store):
    store.seed_config_defaults()
    seed_price(store, datetime(2026, 9, 4, 7, tzinfo=UTC))
    assert guards.check_data_quality(
        store,
        as_of=datetime(2026, 9, 7, 1, tzinfo=UTC),
        market="KR",
        entities=["KR:A"],
    )


def open_order():
    return OpenOrder(
        order_id="order-1",
        entity_id="KR:A",
        side=Side.BUY,
        reference_price=1000,
        limit_price=1000,
        original_quantity=10,
        remaining_quantity=7,
        retry_count=2,
        last_action_at=NOW,
        status=OrderStatus.PARTIALLY_FILLED,
        broker_order_no="100",
    )


def test_cancel_timeout_remains_unresolved():
    broker = RecordingBroker()
    result = supervise.close([open_order()], broker, now=NOW + timedelta(hours=1))
    assert result.errors
    assert result.orders[0].status.value == "cancel_unknown"
    assert result.open == result.orders
    assert result.orders[0].remaining_quantity == 7
    again = supervise.close(result.orders, broker, now=NOW + timedelta(hours=2))
    assert len(broker.cancelled) == 1
    assert again.open == again.orders


@pytest.mark.parametrize("accepted,sent", [(False, True), (True, False)])
def test_unconfirmed_cancel_ack_is_not_terminal(accepted, sent):
    class Unconfirmed(RecordingBroker):
        def cancel(self, **kwargs):
            return Ack(order_id="100", accepted=accepted, sent=sent)

    result = supervise.close([open_order()], Unconfirmed(), now=NOW)
    assert result.orders[0].status.value == "cancel_unknown"


def test_reprice_requires_current_risk_approval():
    broker = RecordingBroker()
    result = supervise.step(
        [open_order()],
        broker,
        now=NOW + timedelta(seconds=301),
        market_prices={"KR:A": 1001},
        cumulative_filled={"order-1": 3},
        params=LifecycleParams(retry_after_sec=300, max_retries=5, max_slippage=0.02),
        pretrade_check=lambda _: guards.GateResult(False, "kill engaged"),
    )
    assert len(broker.cancelled) == 1
    assert result.orders[0].status.value == "cancel_unknown"
