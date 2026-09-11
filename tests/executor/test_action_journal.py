"""Receipts are not confirmations; an interrupted action must not be sent twice."""

from dataclasses import replace
from datetime import timedelta

import pytest
from tests.executor.test_supervise import NOW, FakeBroker, open_order, run

from quant_rl_trading.broker import Ack, BrokerError, RejectedOrder
from quant_rl_trading.broker.order_confirmations import accept
from quant_rl_trading.collectors.ls_client import LSCredentials
from quant_rl_trading.executor import supervise
from quant_rl_trading.executor.action_journal import (
    ActionJournal,
    bind_submission,
    cancelled_quantities,
    events,
    refresh_order_states,
)
from quant_rl_trading.executor.lifecycle import Action, ActionType, LifecycleParams, OrderStatus
from quant_rl_trading.executor.orders import PlannedOrder, client_order_id
from quant_rl_trading.replay.clock import ReplayClock
from quant_rl_trading.risk.account import read
from quant_rl_trading.schemas.order import Order, Side
from tools.reconcile_fills import pending_from_orders, unverified_remainders


def test_cancel_receipt_does_not_confirm_cancellation():
    result = supervise.close([open_order()], FakeBroker(), now=NOW + timedelta(hours=1))
    assert result.orders[0].status is OrderStatus.CANCEL_UNKNOWN
    assert result.open == result.orders


def test_modify_receipt_does_not_confirm_new_price():
    original = open_order()
    result = run([original], FakeBroker(), at=NOW + timedelta(seconds=301), filled={"order-1": 0})
    assert result.orders[0].status is OrderStatus.MODIFY_UNKNOWN
    assert result.orders[0].limit_price == original.limit_price


SESSION = "KR-2026-08-13"
ENTITY = "KR:005930"
IDENTITY = f"{SESSION}|{ENTITY}|0"
FINGERPRINT = LSCredentials(
    appkey="test-key", appsecret="test-secret", base_url="https://api.test", kind="paper"
).fingerprint


@pytest.fixture
def booked(store):
    store.seed_config_defaults()
    store.append(
        "orders",
        [
            {
                "entity_id": ENTITY,
                "market": "KR",
                "session_id": SESSION,
                "slice_seq": 0,
                "side": "buy",
                "quantity": 10,
                "limit_price": 1000.0,
                "target_weight": 0.1,
                "status": "sent",
                "reason": "broker_order_no=700001",
                "valid_from": NOW,
                "observed_at": NOW,
                "source": "test",
            }
        ],
        ingest_run_id="order",
    )
    clock = ReplayClock(NOW)
    item = PlannedOrder(
        order=Order(entity_id=ENTITY, side=Side.BUY, quantity=10, limit_price=1000),
        order_id=client_order_id(session=SESSION, entity_id=ENTITY, slice_seq=0),
        session_id=SESSION,
        slice_seq=0,
        target_weight=0.1,
    )
    bind_submission(
        store,
        clock,
        item,
        Ack(item.order_id, True, broker_order_no="700001"),
        submitted_at=NOW,
        fingerprint=FINGERPRINT,
    )
    original = open_order(
        order_id=IDENTITY,
        reference_price=1000.0,
        limit_price=1000.0,
        original_quantity=10,
        remaining_quantity=10,
    )
    return store, clock, original


def action(order, clock, *, reprice=False):
    return Action(
        ActionType.REPRICE if reprice else ActionType.CANCEL,
        replace(
            order,
            limit_price=1010.0 if reprice else order.limit_price,
            retry_count=order.retry_count + 1,
            last_action_at=clock.now(),
            status=OrderStatus.SUBMITTED if reprice else OrderStatus.CANCELLED,
        ),
    )


def message(clock, *, reprice=False, quantity=10, parent="700001", child="700002"):
    from zoneinfo import ZoneInfo

    stamp = clock.now().astimezone(ZoneInfo("Asia/Seoul")).strftime("%H%M%S%f")[:9]
    body = {
        "ordno": child,
        "orgordno": parent,
        "shtnIsuno": "A005930",
        "bnstp": "2",
        "exectime": stamp,
        "unercqty": 0,
        "orgordunercqty": 5,
    }
    body.update(
        {"mdfycnfqty": quantity, "mdfycnfprc": 1010} if reprice else {"canccnfqty": quantity}
    )
    return {"header": {"tr_cd": "SC2" if reprice else "SC3"}, "body": body}


def confirm(store, clock, msg, **kw):
    return accept(
        store,
        clock,
        msg,
        fingerprint=kw.get("fingerprint", FINGERPRINT),
        connected_at=kw.get("connected_at", NOW),
    )


def fill(store, clock, quantity, *, hashed=False):
    key = client_order_id(session=SESSION, entity_id=ENTITY, slice_seq=0) if hashed else IDENTITY
    store.append(
        "trades",
        [
            {
                "entity_id": ENTITY,
                "market": "KR",
                "side": "buy",
                "quantity": float(quantity),
                "price": 1000.0,
                "currency": "KRW",
                "fee": 0.0,
                "tax": 0.0,
                "order_id": f"{key}#{quantity}",
                "valid_from": clock.now(),
                "observed_at": clock.now(),
                "source": "test",
            }
        ],
        ingest_run_id=f"fill-{quantity}",
    )


@pytest.mark.parametrize("crash", ["before_api", "after_api", "timeout", "transport"])
def test_crash_never_resends_durable_intent(booked, monkeypatch, crash):
    store, clock, original = booked
    calls = []
    broker = FakeBroker()

    def interrupted(**kwargs):
        if crash != "before_api":
            calls.append(kwargs)
        if crash == "timeout":
            raise BrokerError("timeout")
        if crash == "transport":
            # 토큰 갱신 중 SSL EOF — BrokerError 가 아닌 전송 계층 예외 (2026-09-11 실측)
            raise ConnectionError("[SSL: UNEXPECTED_EOF_WHILE_READING]")
        raise KeyboardInterrupt("process terminated")

    monkeypatch.setattr(broker, "cancel", interrupted)
    journal = ActionJournal(store, clock, broker)
    if crash in ("timeout", "transport"):
        state, error = journal.dispatch(action(original, clock), original)
        assert error and state.status is OrderStatus.CANCEL_UNKNOWN
    else:
        with pytest.raises(KeyboardInterrupt):
            journal.dispatch(action(original, clock), original)
    restarted = ActionJournal(store, clock, FakeBroker())
    state, error = restarted.dispatch(action(original, clock), original)
    assert error and state.status is OrderStatus.CANCEL_UNKNOWN
    assert restarted.broker.cancelled == []
    assert len(calls) == (crash != "before_api")
    assert [e["kind"] for e in events(store, as_of=clock.now())].count("intent") == 1
    assert read(store, clock, as_of=clock.now()).reservations[IDENTITY].quantity == 10


def test_receipt_keeps_reservation_and_confirmed_cancellation_releases_it(booked):
    store, clock, original = booked
    journal = ActionJournal(store, clock, FakeBroker())
    journal.dispatch(action(original, clock), original)
    refresh_order_states(store, clock)
    assert read(store, clock, as_of=clock.now()).reservations[IDENTITY].quantity == 10
    assert unverified_remainders(store, as_of=clock.now(), market="KR") == 1
    clock.advance(timedelta(seconds=1))
    msg = message(clock)
    assert confirm(store, clock, msg)
    assert not confirm(store, clock, msg)
    assert cancelled_quantities(store, as_of=clock.now()) == {IDENTITY: 10.0}
    assert IDENTITY not in read(store, clock, as_of=clock.now()).reservations
    assert journal.restore(original).status is OrderStatus.CANCELLED
    assert unverified_remainders(store, as_of=clock.now(), market="KR") == 0
    assert pending_from_orders(store, as_of=clock.now(), market="KR", session_id=SESSION) == []
    assert store.get("orders", as_of=clock.now()).iloc[0]["status"] == "cancelled"


@pytest.mark.parametrize("hashed", [False, True])
def test_partial_cancel_preserves_unrecorded_fill_reservation(booked, hashed):
    store, clock, original = booked
    journal = ActionJournal(store, clock, FakeBroker())
    journal.dispatch(action(original, clock), original)
    clock.advance(timedelta(seconds=1))
    assert confirm(store, clock, message(clock, quantity=6))
    restored = journal.restore(original)
    assert restored.remaining_quantity == 4 and restored.cancelled_quantity == 6
    assert restored.status is OrderStatus.CANCEL_UNKNOWN
    assert read(store, clock, as_of=clock.now()).reservations[IDENTITY].quantity == 4
    # A repeated close may not retry the partially confirmed cancellation.
    assert (
        supervise.close(
            [restored], journal.broker, now=clock.now(), dispatch=journal.dispatch
        ).actions
        == ()
    )
    clock.advance(timedelta(seconds=1))
    fill(store, clock, 4, hashed=hashed)
    refresh_order_states(store, clock)
    assert journal.restore(original).status is OrderStatus.CANCELLED
    assert IDENTITY not in read(store, clock, as_of=clock.now()).reservations


def test_reprice_confirmation_restores_child_number_timer_and_retry_budget(booked):
    store, clock, original = booked
    journal = ActionJournal(store, clock, FakeBroker())
    journal.dispatch(action(original, clock, reprice=True), original)
    assert journal.restore(original).limit_price == 1000
    clock.advance(timedelta(seconds=1))
    assert confirm(store, clock, message(clock, reprice=True))
    restarted = ActionJournal(store, clock, FakeBroker())
    restored = restarted.restore(original)
    assert restored.status is OrderStatus.SUBMITTED
    assert restored.limit_price == 1010 and restored.reference_price == 1000
    assert restored.broker_order_no == "700002" and restored.retry_count == 1
    assert restored.last_action_at == NOW
    # Stale process input cannot reuse the original number after confirmation.
    assert restarted.dispatch(action(original, clock), original)[1]
    assert restarted.broker.cancelled == []
    clock.advance(timedelta(seconds=301))
    result = supervise.step(
        [restored],
        restarted.broker,
        now=clock.now(),
        market_prices={ENTITY: 1015},
        cumulative_filled={IDENTITY: 0},
        params=LifecycleParams(300, 1, 0.02),
        pretrade_check=lambda _: True,
        dispatch=restarted.dispatch,
    )
    assert result.orders[0].retry_count == 2
    assert restarted.broker.cancelled == [("700002", 10)]
    clock.advance(timedelta(seconds=1))
    assert confirm(store, clock, message(clock, parent="700002", child="700003"))
    assert restarted.restore(original).status is OrderStatus.CANCELLED


@pytest.mark.parametrize(
    "field,value",
    [
        ("shtnIsuno", "A000660"),
        ("bnstp", "1"),
        ("orgordno", "999999"),
        ("canccnfqty", 11),
        ("canccnfqty", 0),
        ("canccnfqty", "nan"),
        ("exectime", "250000000"),
    ],
)
def test_mismatched_confirmation_cannot_release(booked, field, value):
    store, clock, original = booked
    ActionJournal(store, clock, FakeBroker()).dispatch(action(original, clock), original)
    clock.advance(timedelta(seconds=1))
    msg = message(clock)
    msg["body"][field] = value
    with pytest.raises(ValueError):
        confirm(store, clock, msg)
    assert cancelled_quantities(store, as_of=clock.now()) == {}
    assert read(store, clock, as_of=clock.now()).reservations[IDENTITY].quantity == 10


def test_account_date_and_conflicting_duplicate_are_rejected(booked):
    store, clock, original = booked
    ActionJournal(store, clock, FakeBroker()).dispatch(action(original, clock), original)
    clock.advance(timedelta(seconds=1))
    msg = message(clock)
    with pytest.raises(ValueError):
        confirm(store, clock, msg, fingerprint="another-account")
    assert confirm(store, clock, msg)
    with pytest.raises(ValueError, match="conflicting"):
        confirm(store, clock, message(clock, quantity=9))
    clock.advance(timedelta(days=1))
    with pytest.raises(ValueError, match="date"):
        confirm(store, clock, msg)
    with pytest.raises(ValueError):
        confirm(store, clock, message(clock), connected_at=clock.now())


def test_confirmed_rejection_is_durable_but_does_not_cancel_order(booked, monkeypatch):
    store, clock, original = booked
    broker = FakeBroker()

    def reject(**kwargs):
        raise RejectedOrder("01443")

    monkeypatch.setattr(broker, "cancel", reject)
    journal = ActionJournal(store, clock, broker)
    state, error = journal.dispatch(action(original, clock), original)
    assert error and state.status is OrderStatus.SUBMITTED
    assert journal.restore(original).retry_count == 1
    assert read(store, clock, as_of=clock.now()).reservations[IDENTITY].quantity == 10


def test_recorded_fill_plus_confirmation_cannot_exceed_original(booked):
    store, clock, original = booked
    ActionJournal(store, clock, FakeBroker()).dispatch(action(original, clock), original)
    clock.advance(timedelta(seconds=1))
    fill(store, clock, 4)
    with pytest.raises(ValueError, match="exceed"):
        confirm(store, clock, message(clock, quantity=7))
    assert cancelled_quantities(store, as_of=clock.now()) == {}


def test_original_order_date_survives_later_status_revision(booked):
    store, clock, original = booked
    ActionJournal(store, clock, FakeBroker()).dispatch(action(original, clock), original)
    clock.advance(timedelta(days=1))
    refresh_order_states(store, clock)
    pending = pending_from_orders(store, as_of=clock.now(), market="KR", session_id=SESSION)
    assert pending[0].observed_day == NOW.date()


def test_simultaneous_action_dispatch_sends_once(booked):
    from concurrent.futures import ThreadPoolExecutor

    store, clock, original = booked
    broker = FakeBroker()

    def send(_):
        return ActionJournal(store, clock, broker).dispatch(action(original, clock), original)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(send, range(2)))
    assert len(broker.cancelled) == 1
    assert sum(error is None for _, error in results) == 1


@pytest.mark.parametrize("filled", [0, 4])
def test_chase_cli_restarts_without_repeating_cancel_and_uses_actual_remainder(
    booked, monkeypatch, filled
):
    from datetime import date
    from types import SimpleNamespace

    from quant_rl_trading.broker.fills import FillOutcome, FillState, SyncResult
    from tools import chase_orders

    store, clock, _original = booked
    broker = FakeBroker()
    if filled:
        fill(store, clock, filled)
    monkeypatch.setattr(chase_orders, "last_settled_day", lambda *a: date(2026, 8, 13))
    monkeypatch.setattr(chase_orders, "_client", lambda *a, **kw: object())
    monkeypatch.setattr(
        chase_orders.broker_factory, "build_broker", lambda *a, **kw: (broker, "test")
    )
    monkeypatch.setattr(
        chase_orders,
        "sync_fills",
        lambda *a, **kw: SyncResult(
            (FillOutcome(IDENTITY, FillState.UNCHANGED, cumulative_quantity=filled),), 0
        ),
    )
    args = SimpleNamespace(market="KR", close=True)

    def chase():
        from quant_rl_trading.store.locking import account_lock

        with account_lock(store.root):
            return chase_orders._chase(args, store, clock)

    assert chase() == 1
    assert broker.cancelled == [("700001", 10 - filled)]
    clock.advance(timedelta(seconds=1))
    assert chase() == 1
    assert len(broker.cancelled) == 1
    confirm(store, clock, message(clock, quantity=10 - filled))
    assert chase() == 0
    assert len(broker.cancelled) == 1


def _dispatch_in_process(root, clock_at, original, queue):
    from pathlib import Path

    from quant_rl_trading.store import Store

    clock = ReplayClock(clock_at)
    broker = FakeBroker()
    _, error = ActionJournal(Store(root=Path(root)), clock, broker).dispatch(
        action(original, clock), original
    )
    queue.put((len(broker.cancelled), error is not None))


def test_separate_processes_share_one_action_claim(booked):
    import multiprocessing

    store, clock, original = booked
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    workers = [
        context.Process(
            target=_dispatch_in_process, args=(str(store.root), clock.now(), original, queue)
        )
        for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=30)
        assert worker.exitcode == 0
    outcomes = [queue.get(timeout=2) for _ in workers]
    assert sum(count for count, _ in outcomes) == 1
    assert sum(blocked for _, blocked in outcomes) == 1


def test_reconnected_duplicate_confirmation_is_idempotent(booked):
    store, clock, original = booked
    ActionJournal(store, clock, FakeBroker()).dispatch(action(original, clock), original)
    clock.advance(timedelta(seconds=1))
    msg = message(clock)
    assert confirm(store, clock, msg)
    clock.advance(timedelta(seconds=1))
    assert not confirm(store, clock, msg, connected_at=clock.now())
    assert cancelled_quantities(store, as_of=clock.now()) == {IDENTITY: 10.0}


def test_fill_reconciliation_cannot_use_another_account_or_overfill_cancelled_remainder(booked):
    from dataclasses import replace
    from types import SimpleNamespace

    from quant_rl_trading.broker.fills import FillState, PendingFill, sync_fills

    store, clock, original = booked
    journal = ActionJournal(store, clock, FakeBroker())
    journal.dispatch(action(original, clock), original)
    clock.advance(timedelta(seconds=1))
    confirm(store, clock, message(clock, quantity=6))
    item = PendingFill(IDENTITY, ENTITY, Side.BUY, "KR", "700001", 10, NOW.date())
    client = SimpleNamespace(
        credentials=SimpleNamespace(fingerprint="another-account"),
        request_tr=lambda *a, **kw: {
            "t0425OutBlock1": [{"ordno": "700001", "cheqty": 4, "cheprice": 1000}]
        },
    )
    result = sync_fills(store, client, clock, as_of=clock.now(), pending=[item])
    assert result.rows_written == 0 and result.outcomes[0].state is FillState.UNKNOWN
    client.credentials.fingerprint = FINGERPRINT
    # Even with the right account, the remaining fill budget is four shares.
    client.request_tr = lambda *a, **kw: {
        "t0425OutBlock1": [{"ordno": "700001", "cheqty": 5, "cheprice": 1000}]
    }
    result = sync_fills(store, client, clock, as_of=clock.now(), pending=[item])
    assert result.rows_written == 0 and result.outcomes[0].state is FillState.UNKNOWN
    clock.advance(timedelta(days=1))
    result = sync_fills(
        store,
        client,
        clock,
        as_of=clock.now(),
        pending=[replace(item, observed_day=clock.now().date())],
    )
    assert result.rows_written == 0 and result.outcomes[0].state is FillState.UNKNOWN
