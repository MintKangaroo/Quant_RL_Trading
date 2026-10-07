"""늦게 받은 미장 봉은 받은 시각으로 — data-contract §5-0b (2026-10-07).

2026-09-29 09:37 재부팅이 미장 수집을 첫 배치에서 끊었다. 9/28 봉 b001~b003 은 9/30 10:16~11:50 에 받혔는데 observed_at 이
공표 시각(9/29 05:20 KST)이었다. 라이브 세션(9/29 12:20·12:45)은 그 봉 없이 돌았고, 재생하면 그 봉이 보여 결과가 달라진다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from quant_rl_trading.collectors.market_hours import Market
from quant_rl_trading.collectors.publication import (
    LateArrivalPolicy,
    NotYetPublished,
    PublicationPolicy,
    late_arrival_policy,
)
from quant_rl_trading.replay.clock import ReplayClock
from quant_rl_trading.store import Store

SEOUL = ZoneInfo("Asia/Seoul")
SESSION = date(2026, 9, 28)  # 월 — 공표는 9/29 05:20 KST(서머타임, 16:00 ET + 20분)
PUBLISHED = datetime(2026, 9, 29, 5, 20, tzinfo=SEOUL)


def _policy(received: datetime) -> LateArrivalPolicy:
    clock = ReplayClock(received)
    inner = PublicationPolicy(market=Market.US, lag_seconds=1200, clock=clock)
    return LateArrivalPolicy(inner=inner, clock=clock, on_time_until=time(13, 30))


def test_제때_받은_봉은_공표_시각이다() -> None:
    """매일 08:40~11:50 에 받는 정상 봉 — 그날 세션(as_of 05:20)이 봐야 한다."""
    observed = _policy(datetime(2026, 9, 29, 11, 50, tzinfo=SEOUL)).for_session(SESSION)

    assert observed == PUBLISHED


def test_경계_직전도_공표_시각이다() -> None:
    observed = _policy(datetime(2026, 9, 29, 13, 30, tzinfo=SEOUL)).for_session(SESSION)

    assert observed == PUBLISHED


def test_세션이_돈_뒤에_받은_봉은_받은_시각이다() -> None:
    """9/28 b001 실제 도착 — 9/30 10:16. 재생(as_of 9/29 05:20)에서 안 보여야 라이브와 같다."""
    received = datetime(2026, 9, 30, 10, 16, tzinfo=SEOUL)

    observed = _policy(received).for_session(SESSION)

    assert observed == received
    assert observed > PUBLISHED


def test_같은_날_오후에_받은_봉도_받은_시각이다() -> None:
    """10/1: 재부팅으로 18:26 에야 받았다. 세션은 13:30 에 미뤘으니 그 봉을 본 세션이 없다."""
    received = datetime(2026, 9, 29, 18, 26, tzinfo=SEOUL)

    assert _policy(received).for_session(SESSION) == received


def test_표준시에도_경계는_KST_시각이다() -> None:
    """11월 이후 공표는 06:20 KST — 경계는 같은 날 13:30 그대로."""
    day = date(2026, 11, 16)
    on_time = _policy(datetime(2026, 11, 17, 12, 0, tzinfo=SEOUL)).for_session(day)
    late = _policy(datetime(2026, 11, 17, 14, 0, tzinfo=SEOUL)).for_session(day)

    assert on_time == datetime(2026, 11, 17, 6, 20, tzinfo=SEOUL)
    assert late == datetime(2026, 11, 17, 14, 0, tzinfo=SEOUL)


def test_공표_전이면_여전히_거부한다() -> None:
    with pytest.raises(NotYetPublished):
        _policy(datetime(2026, 9, 29, 4, 0, tzinfo=SEOUL)).for_session(SESSION)


def test_경계는_설정에서_읽는다(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = Store(root=tmp_path / "data")
    store.seed_config_defaults()
    clock = ReplayClock(datetime(2026, 9, 30, 1, 16, tzinfo=UTC))

    policy = late_arrival_policy(store, Market.US, clock=clock)

    assert policy.on_time_until == time(13, 30)
    assert policy.for_session(SESSION) == clock.now()
