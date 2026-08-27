"""모의계좌 실운용 배선 (backtest.md §9) — 세 가지 계약.

1. 실브로커로 나간 주문(``sent``)은 봉으로 시뮬레이션하지 않는다 — 계좌 체결
   위에 가짜 체결이 한 벌 더 얹히면 장부가 계좌보다 두 배 산다.
2. ``submit_orders`` 는 ``broker_order_no`` 를 ``orders.reason`` 에 남긴다 —
   대사(reconcile_fills)가 t0425 에서 그 주문을 찾을 유일한 열쇠다.
3. 대사는 그 열쇠로 PendingFill 을 만든다. 열쇠가 없는 ``sent`` 는 건너뛴다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from quant_rl_trading.backtest import execution
from quant_rl_trading.broker import Ack
from quant_rl_trading.executor import pipeline
from quant_rl_trading.executor.orders import PlannedOrder, client_order_id
from quant_rl_trading.replay.clock import ReplayClock
from quant_rl_trading.schemas.order import Order, Side
from tools.reconcile_fills import pending_from_orders

NOW = datetime(2026, 8, 31, 6, 40, tzinfo=UTC)  # 한국시간 15:40
SESSION = "KR-2026-08-31"


def _order_row(entity: str, status: str, *, slice_seq: int = 0, reason: str = "") -> dict:
    return {
        "entity_id": entity, "valid_from": NOW, "observed_at": NOW, "source": "executor",
        "market": "KR", "session_id": SESSION, "slice_seq": slice_seq, "side": "buy",
        "quantity": 10.0, "limit_price": 1_000.0, "target_weight": 0.1,
        "status": status, "reason": reason,
    }


def test_sent_주문은_봉으로_체결시키지_않는다(store) -> None:
    store.seed_config_defaults()
    store.append(
        "orders",
        [
            _order_row("KR:A", "paper"),
            _order_row("KR:B", "sent", reason="broker_order_no=77"),
            _order_row("KR:C", "submitting"),
            _order_row("KR:D", "rejected"),
            _order_row("KR:E", "planned"),
        ],
        ingest_run_id="orders-test",
    )
    frame = execution.pending(store, as_of=NOW, session_id=SESSION)
    assert sorted(frame["entity_id"]) == ["KR:A", "KR:E"]


@dataclass
class _Broker:
    number: str = "20260831-0001"
    calls: list[str] = field(default_factory=list)

    def submit(self, order: PlannedOrder, *, as_of: datetime) -> Ack:
        self.calls.append(order.order_id)
        return Ack(order_id=order.order_id, accepted=True, sent=True, broker_order_no=self.number)

    def cancel(self, **kwargs):  # pragma: no cover - 계약상 존재만
        raise NotImplementedError

    def modify(self, **kwargs):  # pragma: no cover
        raise NotImplementedError


def _planned(entity: str, *, slice_seq: int = 0) -> PlannedOrder:
    return PlannedOrder(
        order=Order(entity_id=entity, side=Side.BUY, quantity=10, limit_price=1_000.0),
        order_id=client_order_id(session=SESSION, entity_id=entity, slice_seq=slice_seq),
        session_id=SESSION, slice_seq=slice_seq, target_weight=0.1,
    )


def test_전송된_주문은_브로커_주문번호를_reason_에_남긴다(store) -> None:
    store.seed_config_defaults()
    clock = ReplayClock(NOW)
    broker = _Broker()
    acks = pipeline.submit_orders(
        store, clock, broker, planned=[_planned("KR:A")], as_of=NOW, market="KR"
    )
    assert acks and acks[0].sent
    frame = store.get("orders", as_of=NOW, lookback=3)
    row = frame[frame["entity_id"] == "KR:A"].iloc[0]
    assert row["status"] == "sent"
    assert row["reason"] == "broker_order_no=20260831-0001"

    # 3. 대사는 그 열쇠로 PendingFill 을 만든다.
    pending = pending_from_orders(store, as_of=NOW, market="KR", session_id=SESSION)
    assert len(pending) == 1
    assert pending[0].broker_order_no == "20260831-0001"
    assert pending[0].entity_id == "KR:A"
    assert pending[0].requested_quantity == 10.0


def test_주문번호_없는_sent_는_대사하지_않는다(store, capsys) -> None:
    store.seed_config_defaults()
    store.append("orders", [_order_row("KR:Z", "sent")], ingest_run_id="orders-nokey")
    pending = pending_from_orders(store, as_of=NOW, market="KR", session_id=SESSION)
    assert pending == []
    assert "주문번호가 없다" in capsys.readouterr().err
