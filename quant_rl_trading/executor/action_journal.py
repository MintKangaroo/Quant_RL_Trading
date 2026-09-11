"""Append-only submission identity, action intent, receipt and confirmation events."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, replace
from datetime import datetime
from typing import TYPE_CHECKING

from quant_rl_trading.broker import BrokerError, RejectedOrder
from quant_rl_trading.collectors.market_hours import Market, local_time
from quant_rl_trading.executor.lifecycle import Action, ActionType, OpenOrder, OrderStatus
from quant_rl_trading.replay.events import canonical_json, payload_hash
from quant_rl_trading.schemas.order import Side
from quant_rl_trading.store.locking import account_lock

if TYPE_CHECKING:
    from quant_rl_trading.broker import Ack, Broker
    from quant_rl_trading.executor.orders import PlannedOrder
    from quant_rl_trading.replay.clock import Clock
    from quant_rl_trading.store import Store

TABLE = "execution_events"
SOURCE = "execution-journal-v1"


def logical_id(item: PlannedOrder) -> str:
    return f"{item.session_id}|{item.order.entity_id}|{item.slice_seq}"


def encode(order: OpenOrder) -> dict:
    payload = asdict(order)
    payload.update(
        side=str(order.side),
        status=order.status.value,
        last_action_at=order.last_action_at.isoformat(),
    )
    return payload


def decode(payload: dict) -> OpenOrder:
    return OpenOrder(
        **{
            **payload,
            "side": Side(payload["side"]),
            "status": OrderStatus(payload["status"]),
            "last_action_at": datetime.fromisoformat(payload["last_action_at"]),
        }
    )


def events(store: Store, *, as_of: datetime, order_id: str | None = None) -> list[dict]:
    frame = store.get(TABLE, as_of=as_of)
    if frame.empty:
        return []
    if order_id is not None:
        frame = frame[frame["order_id"] == order_id]
    return [
        dict(row, payload=json.loads(row["payload_json"]))
        for row in frame.sort_values(["valid_from", "observed_at", "event_id"]).to_dict(
            orient="records"
        )
    ]


def record(
    store: Store,
    clock: Clock,
    *,
    order_id: str,
    entity: str,
    kind: str,
    identity: str,
    payload: dict,
    at: datetime | None = None,
) -> bool:
    event_id = payload_hash([order_id, kind, identity])
    run_id = f"execution-event-{event_id}"
    with account_lock(store.root):
        if store.ingest_run_recorded(TABLE, run_id):
            previous = [
                e
                for e in events(store, as_of=clock.now(), order_id=order_id)
                if e["event_id"] == event_id
            ]
            if len(previous) != 1 or previous[0]["payload"] != payload:
                raise ValueError("conflicting or not-yet-visible execution event")
            return False
        store.append(
            TABLE,
            [
                {
                    "entity_id": entity,
                    "market": entity.split(":", 1)[0],
                    "order_id": order_id,
                    "event_id": event_id,
                    "kind": kind,
                    "payload_json": canonical_json(payload),
                    "valid_from": at or clock.now(),
                    "observed_at": clock.now(),
                    "source": SOURCE,
                }
            ],
            ingest_run_id=run_id,
            source=SOURCE,
        )
    return True


def bind_submission(
    store: Store,
    clock: Clock,
    item: PlannedOrder,
    ack: Ack,
    *,
    submitted_at: datetime,
    fingerprint: str,
) -> None:
    if not ack.accepted or not ack.sent or not ack.broker_order_no:
        return
    market = item.order.entity_id.split(":", 1)[0]
    record(
        store,
        clock,
        order_id=logical_id(item),
        entity=item.order.entity_id,
        kind="submitted",
        identity="initial",
        at=submitted_at,
        payload={
            "broker_order_no": str(ack.broker_order_no).lstrip("0"),
            "order_day": local_time(Market(market), submitted_at).date().isoformat(),
            "fingerprint": fingerprint,
            "quantity": item.order.quantity,
            "side": str(item.order.side),
            "limit_price": item.order.limit_price,
        },
    )


def cancelled_quantities(store: Store, *, as_of: datetime) -> dict[str, float]:
    quantities: dict[str, float] = {}
    for event in events(store, as_of=as_of):
        if event["kind"] == "cancel_confirmed":
            key = str(event["order_id"])
            quantities[key] = quantities.get(key, 0.0) + float(event["payload"]["quantity"])
    return quantities


def submission_bindings(store: Store, *, as_of: datetime) -> dict[str, dict]:
    return {
        e["order_id"]: e["payload"] for e in events(store, as_of=as_of) if e["kind"] == "submitted"
    }


def last_intent(history: list[dict]) -> dict | None:
    intents = [e for e in history if e["kind"] == "intent"]
    return max(intents, key=lambda e: int(e["payload"]["attempt"])) if intents else None


def resolved(history: list[dict], intent: dict) -> bool:
    return any(
        e["kind"] in {"rejected", "modify_confirmed", "cancel_confirmed"}
        and e["payload"].get("intent_id") == intent["event_id"]
        for e in history
    )


class ActionJournal:
    def __init__(self, store: Store, clock: Clock, broker: Broker | None):
        self.store, self.clock, self.broker = store.execution_view(), clock, broker

    def restore(self, order: OpenOrder) -> OpenOrder:
        from quant_rl_trading.risk.account import filled_quantities

        history = events(self.store, as_of=self.clock.now(), order_id=order.order_id)
        intent = last_intent(history)
        current = order
        if intent is not None:
            before, proposed = (
                decode(intent["payload"]["before"]),
                decode(intent["payload"]["proposed"]),
            )
            confirmations = [
                e for e in history if e["payload"].get("intent_id") == intent["event_id"]
            ]
            confirmed = next((e for e in confirmations if e["kind"] == "modify_confirmed"), None)
            rejected = any(e["kind"] == "rejected" for e in confirmations)
            current = before
            if confirmed:
                current = replace(
                    proposed,
                    status=OrderStatus.SUBMITTED,
                    broker_order_no=confirmed["payload"]["broker_order_no"],
                )
            elif not rejected:
                current = replace(
                    before,
                    status=(
                        OrderStatus.MODIFY_UNKNOWN
                        if intent["payload"]["action"] == "reprice"
                        else OrderStatus.CANCEL_UNKNOWN
                    ),
                )
            current = replace(
                current,
                retry_count=int(intent["payload"]["attempt"]),
                last_action_at=intent["valid_from"].to_pydatetime(),
            )
        fill_ledger = filled_quantities(self.store, as_of=self.clock.now())
        filled = fill_ledger.get(order.order_id, 0.0)
        if "|" in order.order_id:
            from quant_rl_trading.executor.orders import client_order_id

            session, entity, seq = order.order_id.split("|")
            hashed = client_order_id(session=session, entity_id=entity, slice_seq=int(seq))
            filled += fill_ledger.get(hashed, 0.0)
        cancelled = sum(
            float(e["payload"]["quantity"]) for e in history if e["kind"] == "cancel_confirmed"
        )
        remaining = current.original_quantity - filled - cancelled
        if remaining < 0 or not remaining.is_integer():
            raise ValueError("fill/cancellation ledger exceeds original integer quantity")
        status = current.status
        if remaining == 0:
            status = OrderStatus.CANCELLED if cancelled else OrderStatus.FILLED
        return replace(
            current,
            remaining_quantity=int(remaining),
            cancelled_quantity=int(cancelled),
            status=status,
        )

    def dispatch(self, action: Action, before: OpenOrder) -> tuple[OpenOrder, str | None]:
        with account_lock(self.store.root):
            history = events(self.store, as_of=self.clock.now(), order_id=before.order_id)
            previous = last_intent(history)
            if previous is not None and (
                not resolved(history, previous)
                or int(previous["payload"]["attempt"]) != before.retry_count
            ):
                return self.restore(before), "durable action state requires reconciliation/reload"
            current = self.restore(before)
            if current != before:
                return current, "durable action state requires reconciliation/reload"
            if before.status not in {OrderStatus.SUBMITTED, OrderStatus.PARTIALLY_FILLED}:
                return before, "order is not actionable"
            proposed = action.order
            if (
                action.type not in {ActionType.REPRICE, ActionType.CANCEL, ActionType.ABANDON}
                or proposed.order_id != before.order_id
                or proposed.entity_id != before.entity_id
                or proposed.side != before.side
                or proposed.original_quantity != before.original_quantity
                or proposed.remaining_quantity != before.remaining_quantity
                or proposed.reference_price != before.reference_price
                or proposed.broker_order_no != before.broker_order_no
                or proposed.cancelled_quantity != before.cancelled_quantity
                or proposed.remaining_quantity <= 0
                or not math.isfinite(proposed.limit_price)
                or proposed.limit_price <= 0
            ):
                return before, "invalid action"
            if self.broker is None:
                return before, "broker unavailable"
            if not before.broker_order_no:
                return before, "missing broker order number"
            attempt = (int(previous["payload"]["attempt"]) if previous else before.retry_count) + 1
            payload = {
                "attempt": attempt,
                "action": action.type.value,
                "before": encode(before),
                "proposed": encode(action.order),
            }
            record(
                self.store,
                self.clock,
                order_id=before.order_id,
                entity=before.entity_id,
                kind="intent",
                identity=str(attempt),
                payload=payload,
            )
            intent_id = payload_hash([before.order_id, "intent", str(attempt)])
            pending = replace(
                before,
                retry_count=attempt,
                last_action_at=self.clock.now(),
                status=(
                    OrderStatus.MODIFY_UNKNOWN
                    if action.type is ActionType.REPRICE
                    else OrderStatus.CANCEL_UNKNOWN
                ),
            )
            try:
                if action.type is ActionType.REPRICE:
                    ack = self.broker.modify(
                        broker_order_no=before.broker_order_no,
                        entity_id=before.entity_id,
                        quantity=action.order.remaining_quantity,
                        price=action.order.limit_price,
                    )
                else:
                    ack = self.broker.cancel(
                        broker_order_no=before.broker_order_no,
                        entity_id=before.entity_id,
                        quantity=action.order.remaining_quantity,
                    )
            except RejectedOrder:
                record(
                    self.store,
                    self.clock,
                    order_id=before.order_id,
                    entity=before.entity_id,
                    kind="rejected",
                    identity=intent_id,
                    payload={"intent_id": intent_id},
                )
                return replace(
                    before, retry_count=attempt, last_action_at=self.clock.now()
                ), "action rejected"
            except BrokerError:
                return pending, "broker action outcome unknown"
            record(
                self.store,
                self.clock,
                order_id=before.order_id,
                entity=before.entity_id,
                kind="receipt",
                identity=intent_id,
                payload={
                    "intent_id": intent_id,
                    "accepted": ack.accepted,
                    "sent": ack.sent,
                    "broker_order_no": str(ack.broker_order_no or "").lstrip("0"),
                    "rsp_cd": ack.rsp_cd,
                },
            )
            return pending, None if ack.accepted and ack.sent else "action receipt unconfirmed"


def refresh_order_states(store: Store, clock: Clock, *, order_ids: set[str] | None = None) -> int:
    """Rebuild order status from durable evidence and actual fills, retaining root ID.

    Safe to repeat after a crash between confirmation/trade append and this projection.
    Reservations always read evidence directly, never trust the projected status alone.
    """
    store = store.execution_view()
    with account_lock(store.root):
        history = events(store, as_of=clock.now())
        if order_ids is not None:
            history = [e for e in history if e["order_id"] in order_ids]
        grouped: dict[str, list[dict]] = {}
        for event in history:
            grouped.setdefault(event["order_id"], []).append(event)
        latest = {key: last_intent(group) for key, group in grouped.items()}
        rows = []
        for row in store.get("orders", as_of=clock.now()).to_dict(orient="records"):
            key = f"{row['session_id']}|{row['entity_id']}|{row['slice_seq']}"
            intent = latest.get(key)
            if intent is None:
                continue
            order = ActionJournal(store, clock, None).restore(decode(intent["payload"]["before"]))
            status = (
                "sent"
                if order.status in {OrderStatus.SUBMITTED, OrderStatus.PARTIALLY_FILLED}
                else order.status.value
            )
            if status != row["status"]:
                rows.append(
                    {
                        **row,
                        "status": status,
                        "revision": int(row["revision"]) + 1,
                        "observed_at": clock.now(),
                        "source": SOURCE,
                    }
                )
        if not rows:
            return 0
        identity = payload_hash(
            [
                (
                    r["entity_id"],
                    r["session_id"],
                    int(r["slice_seq"]),
                    int(r["revision"]),
                    r["status"],
                )
                for r in rows
            ]
        )
        return int(
            store.append("orders", rows, ingest_run_id=f"execution-state-{identity}", source=SOURCE)
        )
