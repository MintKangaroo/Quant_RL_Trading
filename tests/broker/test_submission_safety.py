"""Both market adapters must own the complete concurrent submission attempt."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier, Event, Lock

import pytest

from quant_rl_trading.broker import BrokerError
from quant_rl_trading.broker.ls_order import LSBroker
from quant_rl_trading.broker.ls_order_us import LSUSBroker
from quant_rl_trading.collectors.errors import LSAPIError
from quant_rl_trading.executor.orders import PlannedOrder
from quant_rl_trading.schemas.order import Order, Side

NOW = datetime(2026, 9, 9, 1, tzinfo=UTC)


class Enabled:
    def config(self, name, *, as_of):
        return True


class Client:
    def __init__(self, failure=False):
        self.calls = 0
        self.lock = Lock()
        self.both_entered = Event()
        self.failure = failure

    def request_tr(self, path, tr, body):
        with self.lock:
            self.calls += 1
            number = self.calls
            if number == 2:
                self.both_entered.set()
        if self.failure:
            raise LSAPIError("network response lost")
        # Old implementation permits a second HTTP call while the first is pending.
        self.both_entered.wait(timeout=0.2)
        return {"rsp_cd": "00000", f"{tr}OutBlock2": {"OrdNo": str(number)}}


@pytest.mark.parametrize("adapter,entity", [(LSBroker, "KR:005930"), (LSUSBroker, "US:AAPL")])
def test_concurrent_duplicate_order_claim(adapter, entity):
    client = Client()
    broker = adapter(client=client, store=Enabled())
    order = PlannedOrder(
        Order(entity_id=entity, side=Side.BUY, quantity=1, limit_price=100),
        "one-intent",
        "session",
        0,
        0.1,
    )
    barrier = Barrier(2)

    def send():
        barrier.wait(timeout=5)
        return broker.submit(order, as_of=NOW)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(send) for _ in range(2)]
        acks = [future.result(timeout=10) for future in futures]
    assert client.calls == 1
    assert acks[0] == acks[1]


@pytest.mark.parametrize("adapter,entity", [(LSBroker, "KR:005930"), (LSUSBroker, "US:AAPL")])
def test_unknown_submission_is_not_retried_by_adapter(adapter, entity):
    client = Client(failure=True)
    broker = adapter(client=client, store=Enabled())
    order = PlannedOrder(
        Order(entity_id=entity, side=Side.BUY, quantity=1, limit_price=100),
        "one-intent",
        "session",
        0,
        0.1,
    )
    for _ in range(2):
        with pytest.raises(BrokerError):
            broker.submit(order, as_of=NOW)
    assert client.calls == 1
