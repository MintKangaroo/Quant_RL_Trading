"""미장 종목 한국어 명칭 — 네이버 증권 해외주식 basic API.

    https://api.stock.naver.com/stock/{ticker}{.O|.K|.A}/basic → stockName("마이크론 테크놀로지"), stockNameEng, exchangeName

거래소 접미사를 모르므로 .O(나스닥) → .K(NYSE) → .A(AMEX) 순으로 시도한다. 너무 빠르면 409 를 준다 — 요청 간격을 둔다.
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
SUFFIXES = (".O", ".K", ".A")


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
