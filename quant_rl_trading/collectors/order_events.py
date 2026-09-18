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


class WatchAborted(ValueError):
    """이 수집기가 스스로 판정한 중단 사유.

    **메시지가 고정 문구라 안전하다.** 호출부가 예외 종류 이름만 찍고 사유를 버리던
    이유는 인증·전송 예외가 본문에 payload(토큰 따위)를 물고 올 수 있어서였다. 그런데
    그 신중함이 우리가 직접 낸 판정까지 함께 삼켰다 — 2026-09-17 에 네 번 죽는 동안
    로그에는 `ValueError` 라는 글자뿐이었고, 그날 15:20 마감 취소 10건이 확인되지
    못한 채 15:45 대사가 rc=1 로 끝났다. **원인을 못 적는 고장은 못 고치는 고장이다.**

    ``ValueError`` 를 물려받는다 — 호출부와 시험이 이미 그것을 잡고 있다.

    ``partial`` 은 중단 전까지 **이미 창고에 적은** 확인 건수다. 버리면 "한 건도 못
    했다" 로 읽히는데, `accept()` 는 루프 안에서 바로 적으므로 사실이 아니다.

    ``retryable`` 은 **같은 창 안에서 다시 붙어도 되는 사유인가**다. 구독이 안 붙은 것은
    다시 걸면 되지만, 계좌 지문·모드가 어긋난 것은 다시 걸어도 같고 걸어서도 안 된다.
    놓친 이벤트는 **다음 회차가 되살리지 못하므로**(스트림은 과거를 다시 주지 않는다)
    남은 시간 안에서 즉시 복구하는 것이 유일한 기회다.
    """

    def __init__(
        self, reason: str, *, partial: "WatchResult | None" = None, retryable: bool = False
    ) -> None:
        super().__init__(reason)
        self.partial = partial
        self.retryable = retryable


@dataclass(frozen=True)
class WatchResult:
    confirmed: int
    duplicates: int
    unmatched: int
    #: 검증에서 거부된 확인 이벤트 수와 사유별 건수. **거부는 감시를 멈추지 않는다** — 아래 참고.
    rejected: int = 0
    reasons: tuple[tuple[str, int], ...] = ()


def pinned_mode(store: Store, client: LSClient, clock: Clock) -> str:
    mode = str(store.config(ACCOUNT_MODE_KEY, as_of=clock.now()))
    profile = PROFILES.get(("KR", mode))
    credentials = client.credentials
    if profile is None or not credentials.usable() or credentials.declared_kind != mode:
        raise WatchAborted("KR notification account mode/credentials mismatch")
    pinned = str(store.config(profile.fingerprint_key, as_of=clock.now()) or "")
    if not pinned or pinned != credentials.fingerprint:
        raise WatchAborted("KR notification account fingerprint must be pinned and match")
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
        raise WatchAborted("authenticated token unavailable")
    confirmed = duplicates = unmatched = rejected = 0
    reasons: dict[str, int] = {}
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
                raise WatchAborted("account configuration or trading date changed", partial=WatchResult(confirmed, duplicates, unmatched))
            try:
                raw = socket.recv(timeout=min(5.0, (deadline - clock.now()).total_seconds()))
            except TimeoutError:
                continue
            message = json.loads(raw)
            header = message.get("header", {})
            if "rsp_cd" in header:
                if header["rsp_cd"] != "00000":
                    raise WatchAborted(
                        "account notification subscription rejected",
                        partial=WatchResult(confirmed, duplicates, unmatched),
                        retryable=True,
                    )
                # **LS 는 구독 확인에 tr_cd 를 실어 보내지 않는다** (모의 실측
                # 2026-09-11: `{"tr_cd": null, "rsp_cd": "00000", "rsp_msg":
                # "정상처리되었습니다"}` 두 번). tr_cd 를 요구하면 정상 응답을
                # "거절" 로 읽어 이 수집기가 한 번도 뜨지 못한다. 실어 보내면
                # 그것을 쓰고, 없으면 **보낸 순서대로** 짝짓는다.
                acked = header.get("tr_cd")
                if acked not in CODES:
                    if not awaiting:
                        raise WatchAborted(
                            "subscription ack arrived with no pending code",
                            partial=WatchResult(confirmed, duplicates, unmatched),
                            retryable=True,
                        )
                    acked = awaiting[0]
                if acked in awaiting:
                    awaiting.remove(acked)
                subscribed.add(acked)
                continue
            code = header.get("tr_cd")
            if code not in CODES:
                continue  # Other account events aren't cancellation/modification evidence.
            if code not in subscribed:
                raise WatchAborted("account event received before subscription acknowledgement", partial=WatchResult(confirmed, duplicates, unmatched))
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
            except ValueError as exc:
                # **이벤트 하나가 거부됐다고 감시 전체를 멈추지 않는다.** 예전에는 `accept()` 가
                # 던진 ValueError 가 그대로 올라가 회차를 통째로 끝냈다 — 확인 이벤트가 **도착하는
                # 바로 그 순간** 감시가 죽고, 그 뒤 같은 창의 다른 확인도 영영 못 받았다.
                # 2026-09-18 실측: 재호가(:20·:40) 직후 네 번 죽었고, 그날 확인이 하루 종일 0건,
                # 장중 재호가 9건이 cancel_unknown 으로 남아 15:45 대사가 rc=1 이었다.
                # 거부된 그 한 건은 기록하지 않는다(예약 유지 — 전과 같은 안전한 기본값)가,
                # **사유는 남긴다.** accept() 의 문구는 전부 고정이라 payload 가 섞이지 않는다.
                rejected += 1
                reasons[str(exc)] = reasons.get(str(exc), 0) + 1
            else:
                confirmed += int(added)
                duplicates += int(not added)
    if subscribed != CODES:
        # **어느 코드가 빠졌는지 적는다.** SC2(체결)와 SC3(정정·취소)는 확인 경로가
        # 달라서, 둘 중 무엇이 안 붙었는지가 곧 처방이다.
        raise WatchAborted(
            f"subscription incomplete — acked {sorted(subscribed) or '없음'} of {sorted(CODES)}",
            partial=WatchResult(confirmed, duplicates, unmatched),
            retryable=True,
        )
    return WatchResult(
        confirmed, duplicates, unmatched, rejected, tuple(sorted(reasons.items()))
    )
