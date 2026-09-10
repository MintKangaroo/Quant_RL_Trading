"""Authenticated notification stream wiring, with no real network or credentials."""

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from tests.executor.test_action_journal import (
    NOW,
    action,
    message,
)
from tests.executor.test_action_journal import booked as booked
from tests.executor.test_supervise import FakeBroker

from quant_rl_trading.collectors.ls_client import LSCredentials
from quant_rl_trading.collectors.order_events import watch
from quant_rl_trading.executor.action_journal import ActionJournal, events


class Socket:
    def __init__(self, clock, factory):
        self.clock, self.factory, self.sent = clock, factory, []
        self.index = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def send(self, data):
        self.sent.append(json.loads(data))

    def recv(self, timeout):
        self.clock.advance(timedelta(seconds=1))
        self.index += 1
        return json.dumps(self.factory(self.index))


def configured(booked):
    store, clock, original = booked
    credentials = LSCredentials(
        appkey="test-key", appsecret="test-secret", base_url="https://api.test", kind="paper"
    )
    for key, value in [
        ("execution.account_mode", "paper"),
        ("execution.live_account_fingerprint_paper", credentials.fingerprint),
    ]:
        store.append(
            "config",
            [
                {
                    "entity_id": key,
                    "valid_from": NOW,
                    "observed_at": NOW,
                    "revision": 3,
                    "source": "test",
                    "value_json": json.dumps(value),
                }
            ],
            ingest_run_id=key,
        )
    token_calls = []

    def token(**kwargs):
        token_calls.append(kwargs)
        return SimpleNamespace(access_token="test-token")

    client = SimpleNamespace(credentials=credentials, ensure_token=token)
    return store, clock, original, client, token_calls


def test_stream_wires_authenticated_confirmation_to_order_journal(booked):
    store, clock, original, client, tokens = configured(booked)

    def receive(index):
        if index <= 2:
            return {"header": {"tr_cd": ["SC2", "SC3"][index - 1], "rsp_cd": "00000"}}
        if index == 3:
            ActionJournal(store, clock, FakeBroker()).dispatch(action(original, clock), original)
        return message(clock)

    socket = Socket(clock, receive)
    addresses = []

    def connect(address, **kwargs):
        addresses.append(address)
        return socket

    result = watch(store, client, clock, seconds=3, connector=connect)
    assert result.confirmed == 1
    assert addresses == ["wss://openapi.ls-sec.co.kr:29443/websocket"]
    assert tokens == [{"allow_paper": True}]
    assert all(m["header"]["tr_type"] == "1" for m in socket.sent)
    assert {m["body"]["tr_cd"] for m in socket.sent} == {"SC2", "SC3"}
    saved = str(events(store, as_of=clock.now()))
    assert "test-token" not in saved and "test-secret" not in saved
    assert store.get("orders", as_of=clock.now()).iloc[0]["status"] == "cancelled"


def test_unpinned_account_is_blocked_before_token_or_socket(booked):
    store, clock, _original, client, tokens = configured(booked)
    client.credentials = LSCredentials(
        appkey="other", appsecret="other", base_url="https://api.test", kind="paper"
    )
    with pytest.raises(ValueError, match="fingerprint"):
        watch(
            store,
            client,
            clock,
            seconds=1,
            connector=lambda *a, **k: pytest.fail("no connection permitted"),
        )
    assert tokens == []


def test_disconnect_keeps_unconfirmed_order_reserved(booked):
    store, clock, original, client, _tokens = configured(booked)
    ActionJournal(store, clock, FakeBroker()).dispatch(action(original, clock), original)

    def receive(_):
        raise ConnectionError("disconnected")

    with pytest.raises(ConnectionError):
        watch(store, client, clock, seconds=2, connector=lambda *a, **k: Socket(clock, receive))
    assert not any(e["kind"] == "cancel_confirmed" for e in events(store, as_of=clock.now()))


def test_subscription_failure_is_not_a_confirmation(booked):
    store, clock, _original, client, _tokens = configured(booked)
    socket = Socket(clock, lambda _: {"header": {"tr_cd": "SC3", "rsp_cd": "ERROR"}})
    with pytest.raises(ValueError, match="subscription"):
        watch(store, client, clock, seconds=1, connector=lambda *a, **k: socket)
    assert not any(e["kind"] == "cancel_confirmed" for e in events(store, as_of=clock.now()))
