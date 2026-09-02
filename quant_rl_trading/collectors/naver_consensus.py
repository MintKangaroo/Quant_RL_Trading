"""네이버 금융 종목 페이지 → `consensus` 행 (방향 ③, 2026-09-02).

페이지 하나에 투자의견·목표주가(`투자의견 정보` 표)와 PER/EPS·추정PER/EPS(`_per`,
`_eps`, `_cns_per`, `_cns_eps` id)·PBR·배당수익률이 있다. **지금 값만** 보여주므로
매일 긁어야 이력이 되고, 이력이 200일은 있어야 IC 를 잰다.

값이 없는 종목(커버리지 없음)은 행을 **안 만든다** — 0 을 적으면 "추정 EPS 0" 이
된다. 페이지 자체를 못 받으면 예외를 올린다(빈 결과로 위장하지 않는다).
"""
from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Any

CONSENSUS = "consensus"
SOURCE = "naver_finance"
MARKET = "KR"
URL = "https://finance.naver.com/item/main.naver?code={code}"

_ID = re.compile(r'id="(_per|_eps|_cns_per|_cns_eps|_pbr|_dvr)"[^>]*>\s*([^<]*?)\s*<')
_OPINION = re.compile(
    r'summary="투자의견 정보".*?<em>\s*([0-9.]+)\s*</em>\s*[^<]*</span>\s*<span class="bar">l</span>\s*<em>\s*([0-9,]+)\s*</em>',
    re.S,
)


class ConsensusUnavailable(RuntimeError):
    """페이지를 못 받았거나 형식이 바뀌었다."""


def _number(text: str) -> float | None:
    text = text.replace(",", "").replace("배", "").replace("원", "").replace("%", "").strip()
    if text in ("", "-", "N/A"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_main_page(html: str) -> dict[str, float | None]:
    """종목 페이지 HTML → 필드. 페이지가 종목 페이지가 아니면 예외."""
    if 'summary="PER/EPS 정보"' not in html and 'id="_eps"' not in html:
        raise ConsensusUnavailable("종목 페이지 형식이 아니다")
    out: dict[str, float | None] = {
        "rating": None, "target_price": None, "eps_ttm": None, "per_ttm": None,
        "eps_fwd": None, "per_fwd": None, "pbr": None, "dividend_yield": None,
    }
    for key, value in _ID.findall(html):
        num = _number(value)
        out[{"_per": "per_ttm", "_eps": "eps_ttm", "_cns_per": "per_fwd", "_cns_eps": "eps_fwd",
             "_pbr": "pbr", "_dvr": "dividend_yield"}[key]] = num
    m = _OPINION.search(html)
    if m:
        out["rating"] = _number(m.group(1))
        out["target_price"] = _number(m.group(2))
    return out


def row_for(code: str, *, day: date, observed_at: datetime, parsed: dict[str, float | None]) -> dict[str, Any] | None:
    """컨센서스가 하나도 없으면 None — 커버리지 없는 종목은 행을 안 만든다."""
    if all(parsed.get(k) is None for k in ("rating", "target_price", "eps_fwd")):
        return None
    return {
        "entity_id": f"{MARKET}:{code}",
        "valid_from": datetime(day.year, day.month, day.day, tzinfo=UTC),
        "observed_at": observed_at,
        "source": SOURCE,
        "market": MARKET,
        **parsed,
    }


def run_id_for(day: date, *, limit: int = 0) -> str:
    """전체 실행과 배관 확인(--limit)의 run id 를 가른다 — 확인 실행이 그날의 전체
    실행을 "이미 받았다" 로 막았던 실수(2026-09-02)를 되풀이하지 않기 위해서다."""
    base = f"consensus-naver-{day.isoformat()}"
    return f"{base}-smoke{limit}" if limit else f"{base}-full"
