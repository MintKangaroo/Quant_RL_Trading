"""국면 확인 창은 관측된 종가 세션 축으로 센다 — 2026-10-04 결함(08:40 세션이 같은 종가로 두 번 판정) 수정.

portfolio-construction.md "노출 국면 확인 창". (a) 08:40 형태(as_of 에 그날 종가 없음)에서 직전 국면이 다른 종가로 판정된다
(b) 지수가 제때 있는 형태(23:05)에서는 옛 규칙(as_of 날짜만 바꿔 되감기)과 같은 결과다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from quant_rl_trading.analysts.regime import MARKET_PROXIES, RegimeAnalyst, classify
from quant_rl_trading.collectors.market_hours import Market, trading_days
from quant_rl_trading.replay.clock import ReplayClock

INDEX = MARKET_PROXIES[0]
DAYS = list(trading_days(Market.KR, datetime(2023, 1, 2).date(), datetime(2024, 6, 28).date()))


def _closes() -> pd.Series:
    rng = np.random.default_rng(7)
    return pd.Series(1000 * np.cumprod(1 + rng.normal(0, 0.012, len(DAYS))), index=DAYS)


def _flip_point(closes: pd.Series) -> int:
    """국면이 바로 앞 세션과 달라지는 마지막 근처의 위치 — 확인 창이 '다른 종가' 를 보는지 가르려면 둘이 달라야 한다."""
    for k in range(len(closes) - 1, 300, -1):
        if classify(closes.iloc[:k + 1]) != classify(closes.iloc[:k]):
            return k
    raise AssertionError("국면이 바뀌는 지점이 없다")


def _session_moment(day) -> datetime:   # 16:00 KST
    return datetime(day.year, day.month, day.day, tzinfo=UTC) + timedelta(hours=7)


def _store_with(store, closes: pd.Series):  # type: ignore[no-untyped-def]
    store.seed_config_defaults()
    store.append("indices", [
        {"entity_id": INDEX, "valid_from": datetime(d.year, d.month, d.day, tzinfo=UTC), "observed_at": _session_moment(d),
         "source": "test", "market": "KR", "board": None, "open": None, "high": None, "low": None, "close": float(c),
         "volume": None, "value": None}
        for d, c in closes.items()
    ], ingest_run_id="t-regime-index")
    return store


def _old_rule(regime: RegimeAnalyst, as_of: datetime, previous_day) -> str:
    """옛 규칙 — as_of 의 날짜만 바꿔 다시 잰다(session/daily.py, 2026-10-04 전)."""
    earlier = as_of.replace(year=previous_day.year, month=previous_day.month, day=previous_day.day)
    return regime.state(earlier)


def test_0840_form_judges_previous_state_on_a_different_close(store):  # type: ignore[no-untyped-def]
    closes = _closes()
    k = _flip_point(closes)
    # 세션 d = DAYS[k+1] 의 08:40 형태: 창고엔 d−1 = DAYS[k] 종가까지만 있다(국면 지수가 하루 늦게 온다).
    _store_with(store, closes.iloc[:k + 1])
    d = DAYS[k + 1]
    as_of = _session_moment(d)
    regime = RegimeAnalyst(store, ReplayClock(as_of))
    now, prev = regime.state(as_of), regime.recent_states(as_of, 1)
    assert now == classify(closes.iloc[:k + 1])
    assert prev == [classify(closes.iloc[:k])]
    assert prev != [now]                           # 서로 다른 종가 — 확인 창 2 가 실제로 둘을 본다
    assert _old_rule(regime, as_of, DAYS[k]) == now   # 옛 규칙은 같은 종가로 또 판정했다(결함 재현)


def test_timely_index_matches_old_rule(store):  # type: ignore[no-untyped-def]
    closes = _closes()
    _store_with(store, closes)
    for d_pos in (_flip_point(closes), len(DAYS) - 1, 300):
        d = DAYS[d_pos]
        as_of = _session_moment(d)                 # 그날 종가가 있다(23:05 형태)
        regime = RegimeAnalyst(store, ReplayClock(as_of))
        assert regime.recent_states(as_of, 1) == [_old_rule(regime, as_of, DAYS[d_pos - 1])]


def test_count_and_order(store):  # type: ignore[no-untyped-def]
    closes = _closes()
    _store_with(store, closes)
    as_of = _session_moment(DAYS[-1])
    regime = RegimeAnalyst(store, ReplayClock(as_of))
    assert regime.recent_states(as_of, 0) == []
    three = regime.recent_states(as_of, 3)
    assert three == [classify(closes.iloc[:len(DAYS) - j]) for j in (3, 2, 1)]   # 오래된 것부터


@pytest.mark.parametrize("count", [1, 2])
def test_short_history_is_unknown(store, count):  # type: ignore[no-untyped-def]
    closes = _closes().iloc[:100]
    _store_with(store, closes)
    as_of = _session_moment(DAYS[99])
    assert RegimeAnalyst(store, ReplayClock(as_of)).recent_states(as_of, count) == ["unknown"] * count
