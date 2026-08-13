"""미장 종목 명단 — SEC EDGAR.

LS 는 목록 조회 TR 이 없다. g3101 은 심볼을 알아야 부를 수 있어서, 명단은
LS 밖에서 와야 한다. SEC 가 배포하는 ``company_tickers_exchange.json`` 이
티커와 상장 거래소를 같이 주므로 이것만으로 명단이 선다 — LS 로 거래소를
되묻는 호출(종목당 1~2회)이 통째로 사라진다.

**이 명단은 오늘 시점 명단이다.** 상장폐지된 종목은 들어 있지 않다. 그래서
생존편향이 있고, 그건 이 파일로는 못 고친다 — 어차피 LS 가 상폐 종목 시세를
주지 않기 때문이다 (ls_us_source 모듈 docstring 의 실측 참조). 명단을 고쳐도
가격이 없다.

OTC 는 뺀다. LS 가 취급하지 않고, 유동성이 없어 횡단면 IC 를 왜곡한다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from lattice.collectors.errors import CollectorError

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"

#: SEC 표기 → LS 거래소 코드.
EXCHANGE_CODES = {"Nasdaq": "82", "NYSE": "81"}

#: SEC 는 User-Agent 로 신원을 요구한다. 없으면 403 이다.
UA_ENV = "SEC_EDGAR_USER_AGENT"

#: 티커에 이런 문자가 있으면 우선주·워런트·유닛이다. LS 심볼 규약과 다르고
#: 보통주가 아니라서 횡단면 비교 대상이 아니다.
SPECIAL_CHARS = ("-", ".", "$")


@dataclass(frozen=True)
class UsListing:
    ticker: str
    exchange: str
    name: str
    cik: int


def fetch_listings(
    user_agent: str, *, timeout: float = 30.0, client: httpx.Client | None = None
) -> list[UsListing]:
    """SEC 에서 티커·거래소 목록을 받는다."""
    if not user_agent.strip():
        raise CollectorError(f"{UA_ENV} 가 없다. SEC 는 신원 없는 요청을 막는다.")

    owned = client is None
    http = client or httpx.Client(timeout=timeout)
    try:
        response = http.get(SEC_TICKERS_URL, headers={"User-Agent": user_agent})
        if response.status_code != 200:
            raise CollectorError(f"SEC {response.status_code}: {response.text[:200]}")
        payload: dict[str, Any] = json.loads(response.text)
    finally:
        if owned:
            http.close()

    fields = payload.get("fields") or []
    try:
        idx = {name: fields.index(name) for name in ("cik", "name", "ticker", "exchange")}
    except ValueError as error:
        raise CollectorError(f"SEC 응답 구조가 바뀌었다: {fields}") from error

    listings: list[UsListing] = []
    for row in payload.get("data") or []:
        exchange = EXCHANGE_CODES.get(row[idx["exchange"]])
        ticker = str(row[idx["ticker"]] or "").strip().upper()
        if exchange is None or not ticker:
            continue
        if any(char in ticker for char in SPECIAL_CHARS):
            continue
        listings.append(
            UsListing(
                ticker=ticker,
                exchange=exchange,
                name=str(row[idx["name"]] or ""),
                cik=int(row[idx["cik"]]),
            )
        )

    # 같은 티커가 두 거래소에 있으면 먼저 온 것을 쓴다. 중복을 남기면 같은
    # 종목을 두 번 백필하게 된다.
    unique: dict[str, UsListing] = {}
    for listing in listings:
        unique.setdefault(listing.ticker, listing)
    return sorted(unique.values(), key=lambda item: item.ticker)
