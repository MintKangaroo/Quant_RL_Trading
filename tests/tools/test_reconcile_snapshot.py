"""스냅샷 대사 정정 단가 — 당일 체결가중평균 (2026-09-07)."""
from __future__ import annotations

from tools.reconcile_snapshot import fill_price_from_rows

ROWS = {
    "1": {"expcode": "194700", "medosu": "매도", "cheqty": 108, "cheprice": 10400},
    "2": {"expcode": "194700", "medosu": "매도정정", "cheqty": 111, "cheprice": 10450},
    "3": {"expcode": "194700", "medosu": "매수", "cheqty": 10, "cheprice": 10000},
    "4": {"expcode": "194700", "medosu": "매도취소", "cheqty": 0, "cheprice": 0},
    "5": {"expcode": "005930", "medosu": "매도", "cheqty": 5, "cheprice": 255500},
}


def test_같은_종목_같은_방향_체결만_가중평균한다() -> None:
    price = fill_price_from_rows(ROWS, code="194700", side="sell")
    assert price is not None
    assert abs(price - (108 * 10400 + 111 * 10450) / 219) < 1e-9


def test_체결이_없으면_None() -> None:
    assert fill_price_from_rows(ROWS, code="000660", side="sell") is None
    assert fill_price_from_rows(ROWS, code="005930", side="buy") is None
