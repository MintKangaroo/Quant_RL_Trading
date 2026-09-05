from datetime import UTC, datetime

from tools.collect_consensus_ff import rows_from_feed


def test_피드를_우리_지표_id로_매핑한다() -> None:
    now = datetime(2026, 8, 29, 0, 0, tzinfo=UTC)
    feed = [
        {"country": "USD", "title": "Unemployment Claims", "date": "2026-08-27T08:30:00-04:00", "forecast": "205K", "previous": "207K", "actual": "203K"},
        {"country": "USD", "title": "FOMC Member Barkin Speaks", "date": "2026-08-25T08:30:00-04:00", "forecast": "", "previous": ""},
        {"country": "EUR", "title": "CPI m/m", "date": "2026-08-27T05:00:00-04:00", "forecast": "0.2%", "previous": "0.1%"},
        {"country": "USD", "title": "Some New Thing", "date": "2026-08-28T10:00:00-04:00", "forecast": "1.0", "previous": "0.9"},
    ]
    rows = rows_from_feed(feed, observed_at=now)
    assert [r["entity_id"] for r in rows] == ["US:JOBLESS_CLAIMS", "US:FF:SOME_NEW_THING"]
    assert rows[0]["forecast"] == "205K" and rows[0]["actual"] == "203K" and rows[0]["valid_from"].isoformat().startswith("2026-08-27T08:30")
