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
        for code in sorted(CODES):
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
                if header["rsp_cd"] != "00000" or header.get("tr_cd") not in CODES:
                    raise ValueError("account notification subscription rejected/unknown")
                subscribed.add(header["tr_cd"])
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
