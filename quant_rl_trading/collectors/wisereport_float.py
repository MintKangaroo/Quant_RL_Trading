"""유동주식비율 수집 — 네이버 종목분석(WiseReport) 기업개요.

    발행주식수/유동비율   234,000,000주 / 20.29%

KOSPI200 은 유동주식(free-float) 가중이고 우리 시총 데이터는 전액시총이다. 그 차이가 대용지수를 연 −12%p 못 따라가게
했다(benchmark-aligned-construction-2b, 2026-09-04). 비율은 준정적이라 `float_ratio` 참조 테이블에 주 1회 남긴다.
Analyst 는 이 파일을 부르지 않는다 — Collector 만 수집한다(CLAUDE.md).
"""
from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Any

FLOAT_RATIO = "float_ratio"
SOURCE = "naver_wisereport"
MARKET = "KR"
URL = "https://navercomp.wisereport.co.kr/v2/company/c1010001.aspx?cmp_cd={code}"

_ROW = re.compile(r"발행주식수\s*/\s*유동비율.*?<td[^>]*>\s*([\d,]+)\s*주\s*/\s*([\d.]+)\s*%", re.S)


def parse_company_page(html: str) -> dict[str, float] | None:
    """(발행주식수, 유동주식비율 0~1). 셀이 없으면 None — 0 으로 채우면 "유동주식 없음" 이 된다."""
    m = _ROW.search(html)
    if not m:
        return None
    shares = float(m.group(1).replace(",", ""))
    ratio = float(m.group(2)) / 100.0
    if shares <= 0 or not (0.0 < ratio <= 1.0):
        return None
    return {"shares_outstanding": shares, "float_ratio": ratio}


def row_for(code: str, *, day: date, observed_at: datetime, parsed: dict[str, float]) -> dict[str, Any]:
    return {
        "entity_id": f"{MARKET}:{code}",
        "valid_from": datetime(day.year, day.month, day.day, tzinfo=UTC),
        "observed_at": observed_at,
        "source": SOURCE,
        "market": MARKET,
        **parsed,
    }


def run_id_for(day: date, *, limit: int = 0) -> str:
    base = f"float-ratio-{day.isoformat()}"
    return f"{base}-smoke{limit}" if limit else f"{base}-full"
