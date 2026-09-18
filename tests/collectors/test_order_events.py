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


def test_ack_without_tr_cd_counts_in_send_order(booked):
    """LS 의 구독 확인에는 tr_cd 가 없다 — 정상 응답을 거절로 읽지 않는다.

    2026-09-11 모의 실측: 두 구독 모두 `{"tr_cd": null, "rsp_cd": "00000",
    "rsp_msg": "정상처리되었습니다"}` 로 온다. tr_cd 를 요구하던 탓에 이 수집기는
    한 번도 뜨지 못했고, 취소·정정 확인이 당일에 들어오지 않았다.
    """
    store, clock, original, client, _ = configured(booked)

    def receive(index):
        if index <= 2:
            return {"header": {"tr_cd": None, "rsp_cd": "00000", "rsp_msg": "정상처리되었습니다"}}
        if index == 3:
            ActionJournal(store, clock, FakeBroker()).dispatch(action(original, clock), original)
        return message(clock)

    result = watch(store, client, clock, seconds=3, connector=lambda *a, **k: Socket(clock, receive))
    assert result.confirmed == 1
    assert store.get("orders", as_of=clock.now()).iloc[0]["status"] == "cancelled"


def test_rejected_ack_still_fails(booked):
    store, clock, original, client, _ = configured(booked)
    socket = Socket(clock, lambda _: {"header": {"tr_cd": None, "rsp_cd": "40000"}})
    with pytest.raises(ValueError):
        watch(store, client, clock, seconds=3, connector=lambda *a, **k: socket)


# ---------------------------------------------------------------------------
# 중단 사유는 적히고, 일시적인 것은 같은 창 안에서 다시 붙는다 (2026-09-17)
# ---------------------------------------------------------------------------

def test_구독이_안_붙으면_어느_코드가_빠졌는지_적는다(booked):
    """예전엔 사유가 `ValueError` 한 글자였다. 네 번 죽는 동안 원인을 못 찾았다."""
    from quant_rl_trading.collectors.order_events import WatchAborted

    store, clock, _original, client, _tokens = configured(booked)
    # SC2 만 확인해 준다 — SC3(정정·취소)는 끝내 안 붙는다.
    socket = Socket(clock, lambda i: {"header": {"tr_cd": "SC2", "rsp_cd": "00000"}} if i == 1 else {"header": {}})
    with pytest.raises(WatchAborted) as caught:
        watch(store, client, clock, seconds=3, connector=lambda *a, **k: socket)
    assert "SC3" in str(caught.value), "빠진 코드가 곧 처방이다"
    assert caught.value.retryable, "구독은 다시 걸면 된다"


def test_계좌_지문_불일치는_다시_걸지_않는다(booked):
    """구독 실패와 달리, 지문이 어긋난 것은 다시 걸어도 같고 걸어서도 안 된다."""
    from quant_rl_trading.collectors.order_events import WatchAborted

    store, clock, _original, client, _tokens = configured(booked)
    client.credentials = LSCredentials(
        appkey="other", appsecret="other", base_url="https://api.test", kind="paper"
    )
    with pytest.raises(WatchAborted) as caught:
        watch(store, client, clock, seconds=1, connector=lambda *a, **k: pytest.fail("no connection"))
    assert not caught.value.retryable


def test_일시적_중단은_남은_시간_안에서_다시_붙는다(booked):
    """**놓친 이벤트는 다음 회차가 못 되살린다** — 스트림은 과거를 다시 주지 않는다.

    2026-09-17 15:19 회차가 구독 실패로 죽어 15:20 마감 취소 10건이 미확정으로 남았고
    15:45 대사가 rc=1 이 됐다. 창을 버리지 않고 즉시 다시 붙는 것이 유일한 기회다.
    """
    from tools.watch_order_events import _watch_until

    store, clock, _original, client, _tokens = configured(booked)
    attempts: list[int] = []

    def connect(*_args, **_kwargs):
        attempts.append(len(attempts))
        if len(attempts) == 1:
            # 첫 회: 구독이 거절된다 → retryable 중단이 바로 난다(창을 다 안 쓴다)
            return Socket(clock, lambda _i: {"header": {"tr_cd": "SC3", "rsp_cd": "ERROR"}})
        # 둘째 회: 정상으로 붙고 조용히 창을 지킨다
        return Socket(
            clock,
            lambda i: {"header": {"tr_cd": ["SC2", "SC3"][i - 1], "rsp_cd": "00000"}}
            if i <= 2
            else {"header": {}},
        )

    result = _watch_until(store, client, clock, seconds=8, connector=connect)

    assert len(attempts) == 2, "첫 회가 죽었으면 창이 끝나기 전에 다시 붙는다"
    assert result.confirmed == 0


def test_창이_끝날_때까지_못_붙으면_성공으로_끝내지_않는다(booked):
    """조용히 0건으로 끝내면 '이벤트가 없었다' 와 '못 들었다' 가 같아 보인다."""
    from quant_rl_trading.collectors.order_events import WatchAborted
    from tools.watch_order_events import _watch_until

    store, clock, _original, client, _tokens = configured(booked)
    with pytest.raises(WatchAborted):
        _watch_until(
            store, client, clock, seconds=4,
            connector=lambda *a, **k: Socket(clock, lambda _i: {"header": {"tr_cd": "SC3", "rsp_cd": "ERROR"}}),
        )


def test_확인_하나가_거부돼도_감시는_계속된다(booked):
    """2026-09-18: `accept()` 가 던진 ValueError 가 회차를 통째로 끝냈다 — 확인 이벤트가 **도착하는
    바로 그 순간** 감시가 죽어, 그날 확인이 하루 종일 0건이었고 재호가 9건이 미확정으로 남았다.
    거부된 한 건은 기록하지 않되 감시는 이어가고, 사유를 남긴다."""
    store, clock, original, client, _tokens = configured(booked)

    def receive(index):
        if index <= 2:
            return {"header": {"tr_cd": ["SC2", "SC3"][index - 1], "rsp_cd": "00000"}}
        if index == 3:
            # 검증에서 걸리는 이벤트 — 종목코드가 엉터리다
            return {"header": {"tr_cd": "SC3"}, "body": {"shtnIsuno": "BAD", "ordno": "1", "orgordno": "2"}}
        if index == 4:
            ActionJournal(store, clock, FakeBroker()).dispatch(action(original, clock), original)
        return message(clock)

    result = watch(store, client, clock, seconds=6, connector=lambda *a, **k: Socket(clock, receive))

    assert result.rejected >= 1, "거부를 세야 한다"
    assert result.confirmed == 1, "거부 뒤에 온 정상 확인은 받아야 한다 — 예전엔 여기서 죽었다"
    assert any("invalid KR instrument" in reason for reason, _ in result.reasons), "사유가 남아야 한다"
