"""옛 Yahoo 환율 정정 — 최신 행이 Yahoo 인 날만, revision+1, 일요일은 직전 거래일 SMBS (네트워크 없음)."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

from quant_rl_trading.collectors.ls_investinfo import DailyBar
from tools.correct_fx_yahoo_rows import plan_corrections

SEOUL = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 10, 4, 21, 0, tzinfo=SEOUL)


def _have(rows):
    return pd.DataFrame([
        {"entity_id": "FX:USDKRW", "valid_from": datetime(d.year, d.month, d.day, 9, tzinfo=SEOUL),
         "source": s, "rate": r, "revision": rev}
        for d, s, r, rev in rows
    ])


def _bar(d: date, c: float) -> DailyBar:
    return DailyBar(day=d, open=c, high=c, low=c, close=c)


def test_Yahoo_가_최신인_날만_정정본() -> None:
    have = _have([
        (date(2026, 10, 2), "yahoo", 1360.59, 0),
        (date(2026, 8, 21), "fred", 1385.01, 0),
        (date(2026, 8, 30), "yahoo", 1377.11, 0),   # 일요일 라벨
    ])
    bars = [_bar(date(2026, 8, 21), 1386.0), _bar(date(2026, 8, 28), 1379.5), _bar(date(2026, 10, 2), 1344.2)]
    rows = plan_corrections(have, bars, now=NOW)
    got = {(r["valid_from"].date(), r["rate"], r["revision"], r["_from"]) for r in rows}
    assert got == {
        (date(2026, 10, 2), 1344.2, 1, date(2026, 10, 2)),
        (date(2026, 8, 30), 1379.5, 1, date(2026, 8, 28)),
    }
    assert all(r["observed_at"] == NOW and r["source"] == "ls_t3518" for r in rows)


def test_근처_SMBS_가_없으면_짐작하지_않는다() -> None:
    have = _have([(date(2026, 10, 2), "yahoo", 1360.59, 0)])
    assert plan_corrections(have, [_bar(date(2026, 9, 20), 1370.0)], now=NOW) == []
