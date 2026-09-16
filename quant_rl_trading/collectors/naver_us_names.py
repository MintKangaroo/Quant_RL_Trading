"""미장 종목 한국어 명칭 — 네이버 증권 해외주식 basic API.

    https://api.stock.naver.com/stock/{ticker}{.O||.K|.A}/basic → stockName("마이크론 테크놀로지"), stockNameEng, exchangeName

거래소 접미사를 모르므로 .O(나스닥) → 없음(NYSE 대부분: JPM·ABT·A) → .K → .A(AMEX) 순으로 시도한다.
409 `{"code":"StockConflict","message":"Not Exist Master [ABT.O]"}` 는 **그 코드가 없다**는 뜻이지 속도 제한이 아니다
(2026-09-16 실측: 0.02초에 온다). 2초 자고 재요청하면 없는 종목 1,801개 × 접미사 3개 × 2.35초 = 3.5시간이 된다 —
그날 한글명 수집이 4시간 걸린 이유가 이것이었다.
화면 표시 전용 참조 데이터다(`names_ko`). Analyst 는 읽지 않는다.
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any

NAMES_KO = "names_ko"
SOURCE = "naver_stock_api"
MARKET = "US"
URL = "https://api.stock.naver.com/stock/{ticker}{suffix}/basic"
SUFFIXES = (".O", "", ".K", ".A")


def not_exist(status_code: int, body: str) -> bool:
    """409 가 '없는 코드' 인지 — 속도 제한이 아니라 다음 접미사로 바로 넘어가도 되는지."""
    return status_code == 409 and ("Not Exist" in body or "StockConflict" in body)


def parse_basic(payload: str | bytes | dict[str, Any]) -> dict[str, str] | None:
    data = payload if isinstance(payload, dict) else json.loads(payload)
    name = str(data.get("stockName") or "").strip()
    if not name:
        return None
    return {
        "name_ko": name,
        "name_en": str(data.get("stockNameEng") or "").strip(),
        "exchange": str(data.get("exchangeName") or data.get("stockExchangeName") or "").strip(),
    }


def row_for(ticker: str, *, day: date, observed_at: datetime, parsed: dict[str, str]) -> dict[str, Any]:
    return {
        "entity_id": f"{MARKET}:{ticker}",
        "valid_from": datetime(day.year, day.month, day.day, tzinfo=UTC),
        "observed_at": observed_at,
        "source": SOURCE,
        "market": MARKET,
        **parsed,
    }


def run_id_for(day: date, *, limit: int = 0, tag: str = "") -> str:
    base = f"names-ko-us-{day.isoformat()}" + (f"-{tag}" if tag else "")
    return f"{base}-smoke{limit}" if limit else f"{base}-full"
