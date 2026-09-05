"""t8407 개장 전 스텁(시·고·저·거래량 0)은 봉이 아니다 — 버린다 (2026-09-02 9/1 사고)."""
from datetime import UTC, date, datetime

from tools.collect_prices_ls import rows_from_block


def test_pre_open_stub_rows_are_dropped() -> None:
    day = date(2026, 9, 1); now = datetime(2026, 9, 2, 6, 30, tzinfo=UTC)
    block = [
        {"shcode": "267250", "price": "219000", "open": "0", "high": "0", "low": "0", "volume": "0", "value": "0"},
        {"shcode": "005930", "price": "252500", "open": "251000", "high": "255000", "low": "249000", "volume": "1000", "value": "252"},
    ]
    rows = rows_from_block(block, day=day, observed_at=now)
    assert [r["entity_id"] for r in rows] == ["KR:005930"]
