"""LS 원달러(SMBS) 수집 — 확정 시각·건너뛰기·같은 원천 정정본 (네트워크 없음)."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

from quant_rl_trading.collectors.ls_investinfo import DailyBar
from tools.collect_fx_ls import ENTITY, SOURCE, final_through, plan_rows

SEOUL = ZoneInfo("Asia/Seoul")


def _bar(day: date, close: float) -> DailyBar:
    return DailyBar(day=day, open=close, high=close, low=close, close=close)


def _have(rows: list[tuple[date, str, float, int]]) -> pd.DataFrame:
    return pd.DataFrame([
        {"entity_id": ENTITY, "valid_from": datetime(d.year, d.month, d.day, 9, tzinfo=SEOUL),
         "source": s, "rate": r, "revision": rev}
        for d, s, r, rev in rows
    ])


def test_06시_전에는_어제_봉을_적지_않는다() -> None:
    assert final_through(datetime(2026, 10, 6, 5, 59, tzinfo=SEOUL)) == date(2026, 10, 4)
    assert final_through(datetime(2026, 10, 6, 6, 0, tzinfo=SEOUL)) == date(2026, 10, 5)


def test_valid_from_은_FRED_와_같은_칸_observed_at_은_받은_시각() -> None:
    now = datetime(2026, 10, 6, 8, 40, tzinfo=SEOUL)
    rows = plan_rows([_bar(date(2026, 10, 5), 1350.0), _bar(date(2026, 10, 6), 1351.0)], _have([]), now=now)
    assert len(rows) == 1  # 오늘(10/6) 봉은 미완성
    row = rows[0]
    assert row["valid_from"] == datetime(2026, 10, 5, 9, tzinfo=SEOUL)
    assert row["valid_from"].astimezone(ZoneInfo("UTC")).hour == 0  # FRED d 00:00 UTC 와 같다
    assert row["observed_at"] == now and row["source"] == SOURCE and row["revision"] == 0


def test_다른_원천_행이_있는_날은_건드리지_않는다() -> None:
    now = datetime(2026, 10, 6, 8, 40, tzinfo=SEOUL)
    have = _have([(date(2026, 10, 2), "yahoo", 1360.59, 0), (date(2026, 10, 1), "fred", 1356.0, 0)])
    rows = plan_rows([_bar(date(2026, 10, 1), 1358.4), _bar(date(2026, 10, 2), 1344.2)], have, now=now)
    assert rows == []


def test_같은_원천이_값을_고쳤으면_정정본() -> None:
    now = datetime(2026, 10, 6, 8, 40, tzinfo=SEOUL)
    have = _have([(date(2026, 10, 5), SOURCE, 1350.0, 0), (date(2026, 10, 2), SOURCE, 1344.2, 0)])
    rows = plan_rows([_bar(date(2026, 10, 2), 1344.2), _bar(date(2026, 10, 5), 1349.1)], have, now=now)
    assert [(r["valid_from"].date(), r["revision"], r["rate"]) for r in rows] == [(date(2026, 10, 5), 1, 1349.1)]
