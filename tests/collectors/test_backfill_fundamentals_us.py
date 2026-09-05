"""companyfacts → fundamentals 행: 국장과 같은 분기 규약(Q1~Q3 3개월, Q4 연간), 첫 공시 우선, 부채 보정."""

from datetime import UTC, date, datetime

from tools.backfill_fundamentals_us import facts_to_rows


def _e(val, start, end, fy, fp, form, filed):
    return {"val": val, "start": start, "end": end, "fy": fy, "fp": fp, "form": form, "filed": filed}


def test_분기_규약과_첫_공시_우선() -> None:
    payload = {"facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [
            _e(100, "2025-01-01", "2025-03-31", 2025, "Q1", "10-Q", "2025-05-01"),   # 3개월 ✓
            _e(210, "2025-01-01", "2025-06-30", 2025, "Q2", "10-Q", "2025-08-01"),   # YTD(6개월) ✗
            _e(110, "2025-04-01", "2025-06-30", 2025, "Q2", "10-Q", "2025-08-01"),   # 3개월 ✓
            _e(450, "2025-01-01", "2025-12-31", 2025, "FY", "10-K", "2026-02-20"),   # 연간 ✓ → Q4
            _e(100, "2025-01-01", "2025-03-31", 2026, "Q1", "10-Q", "2026-05-01"),   # 비교 표시(나중 접수) ✗
        ]}},
        "Assets": {"units": {"USD": [_e(1000, None, "2025-03-31", 2025, "Q1", "10-Q", "2025-05-01")]}},
        "StockholdersEquity": {"units": {"USD": [_e(600, None, "2025-03-31", 2025, "Q1", "10-Q", "2025-05-01")]}},
    }}}
    rows = facts_to_rows(payload, ticker="ACME", since=date(2024, 1, 1))
    by = {(r["metric"], r["fiscal_period"]): r for r in rows}
    assert by[("revenue", "2025Q1")]["value"] == 100 and by[("revenue", "2025Q2")]["value"] == 110
    assert by[("revenue", "2025Q4")]["value"] == 450 and by[("revenue", "2025Q4")]["report_type"] == "edgar_10k"
    # 접수일 18:00 ET 컷오프 = 22:00 UTC (us_shares.filing_moment)
    assert by[("revenue", "2025Q1")]["observed_at"] == datetime(2025, 5, 1, 22, 0, tzinfo=UTC)
    assert by[("revenue", "2025Q1")]["valid_from"] == datetime(2025, 3, 31, tzinfo=UTC)
    # 총부채가 없으면 자산 − 자본
    assert by[("total_liabilities", "2025Q1")]["value"] == 400
    assert all(r["entity_id"] == "US:ACME" and r["source"] == "edgar" for r in rows)
