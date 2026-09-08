"""정정 사슬 — 체결은 새 주문번호 밑에 쌓인다 (2026-09-08 KR:081660)."""
from __future__ import annotations

from quant_rl_trading.broker.fills import _index_by_ordno


def test_정정_사슬을_원주문으로_접어_수량_합_가격_가중평균() -> None:
    rows = [
        {"ordno": 12856, "orgordno": 0, "medosu": "매수", "qty": 77, "cheqty": 0, "cheprice": 0, "status": "정정확인"},
        {"ordno": 17000, "orgordno": 12856, "medosu": "매수정정", "qty": 77, "cheqty": 50, "cheprice": 40700, "status": "정정확인"},
        {"ordno": 17500, "orgordno": 17000, "medosu": "매수정정", "qty": 27, "cheqty": 27, "cheprice": 40850, "status": "체결"},
        {"ordno": 999, "orgordno": 0, "medosu": "매도", "qty": 5, "cheqty": 5, "cheprice": 1000, "status": "체결"},
    ]
    by = _index_by_ordno(rows)
    merged = by["12856"]
    assert merged["cheqty"] == 77
    assert abs(merged["cheprice"] - (50 * 40700 + 27 * 40850) / 77) < 1e-6
    assert by["17000"] is merged and by["17500"] is merged
    assert by["999"]["cheqty"] == 5 and by["999"]["cheprice"] == 1000
