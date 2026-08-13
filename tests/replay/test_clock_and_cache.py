"""Clock 과 에이전트 캐시.

캐시의 observed_at 을 벽시계로 찍으면 오늘 채운 캐시가 과거 리플레이에서
영영 보이지 않는다. 조용히 무력화되는 종류의 버그라 테스트로 붙잡아 둔다.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from quant_rl_trading.replay import AgentCache, CacheKey, LiveClock, ReplayClock, features_hash
from quant_rl_trading.store.errors import NaiveTimestamp


def test_replay_clock_rejects_naive_time() -> None:
    with pytest.raises(NaiveTimestamp):
        ReplayClock(datetime(2024, 3, 10))


def test_replay_clock_advances_only_forward(ts) -> None:  # type: ignore[no-untyped-def]
    clock = ReplayClock(ts(2024, 3, 10))
    clock.advance(timedelta(hours=1))

    assert clock.now() == ts(2024, 3, 10, 1)
    with pytest.raises(ValueError, match="뒤로"):
        clock.advance(timedelta(hours=-1))


def test_live_clock_is_timezone_aware() -> None:
    assert LiveClock().now().tzinfo is not None


def test_features_hash_is_stable_and_order_independent() -> None:
    assert features_hash({"a": 1, "b": 2}) == features_hash({"b": 2, "a": 1})
    assert features_hash({"a": 1}) != features_hash({"a": 2})


@pytest.fixture
def cache(store, ts):  # type: ignore[no-untyped-def]
    return AgentCache(store=store, clock=ReplayClock(ts(2026, 8, 1)))


def _key(ts, **overrides):  # type: ignore[no-untyped-def]
    base = {
        "agent": "chart",
        "agent_version": "v1",
        "entity_id": "KR:005930",
        "as_of": ts(2024, 3, 10, 18),
        "features_hash": features_hash({"close": 70000}),
    }
    base.update(overrides)
    return CacheKey(**base)  # type: ignore[arg-type]


def test_miss_returns_none(cache, ts) -> None:  # type: ignore[no-untyped-def]
    assert cache.get(_key(ts)) is None


def test_entry_written_today_is_visible_in_a_past_replay(cache, ts) -> None:  # type: ignore[no-untyped-def]
    """캐시를 2026년에 채워도 2024년 as_of 리플레이에서 보여야 한다.

    보이지 않으면 캐시는 존재만 하고 한 번도 적중하지 않는다 —
    리플레이 결정론과 LLM 비용 대책이 동시에 무너진다.
    """
    key = _key(ts)
    cache.put(key, {"score": 0.7}, ingest_run_id="chart-1")

    assert cache.get(key) == {"score": 0.7}


def test_different_features_do_not_share_an_entry(cache, ts) -> None:  # type: ignore[no-untyped-def]
    """입력이 바뀌었는데 옛 출력을 재사용하면 재현이 아니라 날조다."""
    cache.put(_key(ts), {"score": 0.7}, ingest_run_id="chart-1")

    other = _key(ts, features_hash=features_hash({"close": 71000}))

    assert cache.get(other) is None


def test_different_agent_version_does_not_share_an_entry(cache, ts) -> None:  # type: ignore[no-untyped-def]
    cache.put(_key(ts), {"score": 0.7}, ingest_run_id="chart-1")

    assert cache.get(_key(ts, agent_version="v2")) is None


def test_entry_is_invisible_before_its_as_of(cache, ts) -> None:  # type: ignore[no-untyped-def]
    """캐시도 게이트를 지난다. as_of 이전에는 존재하지 않는다."""
    cache.put(_key(ts), {"score": 0.7}, ingest_run_id="chart-1")

    assert cache.get(_key(ts, as_of=ts(2024, 3, 9))) is None
