"""'지금' 조회 짧은 캐시 — 2분 안 '지금' 끼리만 다시 쓰고, 되감기·다른 인자는 다시 계산한다 (dashboard.md §9)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from quant_rl_trading.dashboard.services import live_cache


@pytest.fixture(autouse=True)
def _clean() -> None:
    live_cache.clear()


def test_now_requests_share_rewinds_do_not() -> None:
    calls: list[int] = []

    def compute() -> int:
        calls.append(1)
        return len(calls)

    now = datetime(2026, 10, 14, 7, 30, tzinfo=UTC)
    assert live_cache.cached("x", ("root", 30), as_of=now, compute=compute) == 1
    assert live_cache.cached("x", ("root", 30), as_of=now + timedelta(seconds=40), compute=compute) == 1
    assert live_cache.cached("x", ("root", 30), as_of=now - timedelta(days=3), compute=compute) == 2   # 되감기
    assert live_cache.cached("x", ("root", 90), as_of=now, compute=compute) == 3                       # 다른 인자


def test_ttl_expiry() -> None:
    now = datetime(2026, 10, 14, 7, 30, tzinfo=UTC)
    first = live_cache.cached("y", 1, as_of=now, compute=lambda: "a")
    again = live_cache.cached("y", 1, as_of=now, compute=lambda: "b", ttl=0.0)
    assert first == "a" and again == "b"
