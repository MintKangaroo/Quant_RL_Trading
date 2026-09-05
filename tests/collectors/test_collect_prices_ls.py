from datetime import UTC, date, datetime

from tools.collect_prices_ls import rows_from_block


def test_t8407_행을_prices_행으로() -> None:
    now = datetime(2026, 8, 28, 7, 0, tzinfo=UTC)
    rows = rows_from_block([
        {"shcode": "005930", "price": 257000, "open": 262500, "high": 266000, "low": 256000, "volume": 15106746, "value": 3925853},
        {"shcode": "", "price": 100}, {"shcode": "000001", "price": 0},
    ], day=date(2026, 8, 28), observed_at=now)
    assert len(rows) == 1
    r = rows[0]
    assert r["entity_id"] == "KR:005930" and r["close"] == 257000.0 and r["value"] == 3925853 * 1_000_000
    assert r["valid_from"].isoformat().startswith("2026-08-28T09:00:00+09:00") and r["adj_factor"] is None
