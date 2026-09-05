"""market.states — 봉 날짜는 as_of 의 KST 날짜가 아니라 세션 날짜다 (2026-09-04 미장 첫 체결 0건)."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from quant_rl_trading.backtest import market as mm
from quant_rl_trading.store import Store

KST = ZoneInfo("Asia/Seoul")


def _bar(day: date, close: float) -> dict:
    moment = datetime(day.year, day.month, day.day, 9, 0, tzinfo=KST)
    return {
        "entity_id": "US:AAA", "valid_from": moment, "observed_at": moment, "source": "test", "market": "US",
        "open": close, "high": close * 1.02, "low": close * 0.98, "close": close, "volume": 1e6, "value": close * 1e6,
        "adj_factor": 1.0,
    }


def test_us_fill_day_uses_session_day(tmp_path: Path) -> None:
    store = Store(root=tmp_path / "wh")
    rows = [_bar(date(2026, 9, d), 10.0 + d) for d in (1, 2, 3)]
    store.append("prices", rows, ingest_run_id="test-prices")
    # 미장 세션 시각: ET 9/3 16:20 = KST 9/4 05:20. KST 달력 날짜(9/4)에는 봉이 없다.
    as_of = datetime(2026, 9, 4, 5, 20, tzinfo=KST)
    assert mm.states(store, as_of=as_of, entities=["US:AAA"], market="US") == {}
    found = mm.states(store, as_of=as_of, entities=["US:AAA"], market="US", session_day=date(2026, 9, 3))
    assert set(found) == {"US:AAA"}
    assert found["US:AAA"].close == 13.0
