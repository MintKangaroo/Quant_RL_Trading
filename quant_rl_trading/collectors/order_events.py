"""Read-only LS account notifications, with account identity pinned in Store."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta

from websockets.sync.client import connect

from quant_rl_trading.broker.factory import ACCOUNT_MODE_KEY, PROFILES
from quant_rl_trading.broker.order_confirmations import UnmatchedConfirmation, accept
from quant_rl_trading.collectors.ls_client import LSClient
from quant_rl_trading.collectors.market_hours import Market, local_time
from quant_rl_trading.replay.clock import Clock
from quant_rl_trading.store import Store

# LS official howto-sample and stock websocket guide. No configurable token destination.
URLS = {
    "real": "wss://openapi.ls-sec.co.kr:9443/websocket",
    "paper": "wss://openapi.ls-sec.co.kr:29443/websocket",
}
CODES = frozenset({"SC2", "SC3"})


@dataclass(frozen=True)
class WatchResult:
    confirmed: int
    duplicates: int
    unmatched: int


def pinned_mode(store: Store, client: LSClient, clock: Clock) -> str:
    mode = str(store.config(ACCOUNT_MODE_KEY, as_of=clock.now()))
    profile = PROFILES.get(("KR", mode))
    credentials = client.credentials
    if profile is None or not credentials.usable() or credentials.declared_kind != mode:
        raise ValueError("KR notification account mode/credentials mismatch")
    pinned = str(store.config(profile.fingerprint_key, as_of=clock.now()) or "")
    if not pinned or pinned != credentials.fingerprint:
        raise ValueError("KR notification account fingerprint must be pinned and match")
    return mode


def watch(
    store: Store, client: LSClient, clock: Clock, *, seconds: int, connector=connect
) -> WatchResult:
    """Bounded subscription. Disconnects/errors propagate; no order retries or releases.

    The caller must start this collector before chasing orders. Stream absence is
    not finality evidence, and this function doesn't claim to recover missed events.
    """
    if seconds <= 0:
        raise ValueError("seconds must be positive")
    mode = pinned_mode(store, client, clock)
    # Token can be obtained for a read-only client; no order endpoint is called.
    token = client.ensure_token(allow_paper=True).access_token
    if not token or token == "PAPER_PLACEHOLDER":
        raise ValueError("authenticated token unavailable")
    confirmed = duplicates = unmatched = 0
    subscribed: set[str] = set()
    with connector(
        URLS[mode], open_timeout=10, close_timeout=5, max_size=1_048_576, proxy=None
    ) as socket:
        started = clock.now()
        deadline = started + timedelta(seconds=seconds)
        awaiting = sorted(CODES)
        for code in awaiting:
            socket.send(
                json.dumps(
                    {
                        "header": {"token": token, "tr_type": "1"},
                        "body": {"tr_cd": code, "tr_key": ""},
                    }
                )
            )
        while clock.now() < deadline:
            if (
                pinned_mode(store, client, clock) != mode
                or local_time(Market.KR, clock.now()).date()
                != local_time(Market.KR, started).date()
            ):
                raise ValueError("account configuration or trading date changed")
            try:
                raw = socket.recv(timeout=min(5.0, (deadline - clock.now()).total_seconds()))
            except TimeoutError:
                continue
            message = json.loads(raw)
            header = message.get("header", {})
            if "rsp_cd" in header:
                if header["rsp_cd"] != "00000":
                    raise ValueError("account notification subscription rejected/unknown")
                # **LS 는 구독 확인에 tr_cd 를 실어 보내지 않는다** (모의 실측
                # 2026-09-11: `{"tr_cd": null, "rsp_cd": "00000", "rsp_msg":
                # "정상처리되었습니다"}` 두 번). tr_cd 를 요구하면 정상 응답을
                # "거절" 로 읽어 이 수집기가 한 번도 뜨지 못한다. 실어 보내면
                # 그것을 쓰고, 없으면 **보낸 순서대로** 짝짓는다.
                acked = header.get("tr_cd")
                if acked not in CODES:
                    if not awaiting:
                        raise ValueError("account notification subscription rejected/unknown")
                    acked = awaiting[0]
                if acked in awaiting:
                    awaiting.remove(acked)
                subscribed.add(acked)
                continue
            code = header.get("tr_cd")
            if code not in CODES:
                continue  # Other account events aren't cancellation/modification evidence.
            if code not in subscribed:
                raise ValueError("account event received before subscription acknowledgement")
            try:
                added = accept(
                    store,
                    clock,
                    message,
                    fingerprint=client.credentials.fingerprint,
                    connected_at=started,
                )
            except UnmatchedConfirmation:
                unmatched += 1  # Manual/legacy orders have no trusted local binding.
            else:
                confirmed += int(added)
                duplicates += int(not added)
    if subscribed != CODES:
        raise ValueError("account notification subscription incomplete")
    return WatchResult(confirmed, duplicates, unmatched)
