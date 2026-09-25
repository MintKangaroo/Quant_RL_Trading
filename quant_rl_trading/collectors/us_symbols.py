"""미장 증권 종류 — Nasdaq Trader 심볼 디렉터리(nasdaqlisted.txt · otherlisted.txt).

## 왜 필요했나 (2026-09-25)

미장 명단(`universe`)은 시세에서 유도한다(us-universe-derived-from-prices). 그래서 **보통주가 아닌 상장증권**이 섞인다 —
회사채(TMUSL "6.250% Senior Notes due 2069"), 우선주 예탁증서(AGNCL·HBANZ·SMCIP), 유닛(BTSGU), ETN(BNKD), 상품 ETF(USCI·SOYB).
이들이 모회사 CIK 로 재무·시총을 달고 들어와 합성 점수 상위와 시총 상위에 올랐다(미장 G1 트랙·시행 AT 진단).

## 소스 — 실측으로 골랐다

- LS g3101(종목마스터): 증권 종류 필드가 없다.
- SEC company_tickers: 보통주 위주라 **없는 것**만 알 뿐 무엇인지 모른다.
- **Nasdaq Trader 심볼 디렉터리 — 채택.** 무료·키 없음·매일 갱신. NASDAQ 상장(nasdaqlisted)과 NYSE·AMEX·Arca 등(otherlisted)을
  합치면 우리 명단 6,570 중 6,560(99.8%)이 붙는다(2026-09-25). 행마다 **정식 증권명**과 ETF·시험 종목 표시가 있다.

## 분류는 증권명으로 — 순서가 규칙이다

ETF 표시가 먼저, 그다음 이름의 낱말. "American Depositary Shares" 는 ADR(주식), "Depositary Shares Each Representing … Preferred"
는 우선주 — 둘 다 "Depositary Shares" 를 품으므로 **ADR 을 먼저** 본다. 모르는 이름은 ``other`` 로 두고 버리지 않는다(필터가 정한다).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

SOURCE = "nasdaqtrader"
URLS = (
    "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
    "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt",
)
TABLE = "instrument_types"

#: (분류, 정규식) — 위에서부터 첫 일치. 대소문자 무시, 낱말 경계.
RULES: tuple[tuple[str, str], ...] = (
    ("etn", r"\betns?\b|exchange traded notes?"),
    ("warrant", r"\bwarrants?\b"),
    ("right", r"\brights?\b"),
    ("unit", r"\bunits?\b"),
    ("adr", r"\bamerican deposit[ao]ry\b|\bads?s\b|\badrs?\b"),
    ("note", r"\bnotes?\b|\bdebentures?\b|\bbonds?\b|\bdue (?:\d{4}|january|february|march|april|may|june|july|august|september|october|november|december)\b"),
    ("preferred", r"\bpreferred\b|\bpreference\b|\bpfd\b|\bdepositary shares\b|%"),
    ("fund", r"\bfund\b|\bclosed[- ]end\b|\bmunicipal\b|\bincome trust\b|\bmuni\b"),
    ("common", r"\bcommon\b|\bordinary shares?\b|\bshares of beneficial interest\b|\bcapital stock\b"
               r"|\blimited partner|\blimited voting shares\b|\bclass [a-c] shares?\b"),
)
#: 보통주로 다루는 분류. ``other`` 는 "Trane Technologies plc" 처럼 종류 낱말이 없는 **이름뿐인 보통주**가 대부분이라 남긴다
#: (2026-09-25 표본 15건 중 보통주 5·펀드/ETN 은 위 규칙으로 옮겼다).
EQUITY = frozenset({"common", "adr", "other"})


@dataclass(frozen=True)
class Symbol:
    ticker: str
    name: str
    instrument: str
    test_issue: bool


def classify(name: str, *, etf: bool) -> str:
    if etf:
        return "etf"
    text = name.lower()
    for label, pattern in RULES:
        if re.search(pattern, text):
            return label
    return "other"


def parse(text: str) -> list[Symbol]:
    """파이프 구분 파일 하나. 첫 줄 머리, 마지막 줄 "File Creation Time" 은 버린다. 두 파일의 칸 이름이 다르다."""
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    head = lines[0].split("|")
    col = {name: i for i, name in enumerate(head)}
    sym = col.get("Symbol", col.get("ACT Symbol"))
    if sym is None or "Security Name" not in col or "ETF" not in col or "Test Issue" not in col:
        raise ValueError(f"심볼 디렉터리 머리가 바뀌었다: {head}")
    out = []
    for line in lines[1:]:
        if line.startswith("File Creation Time"):
            continue
        cells = line.split("|")
        if len(cells) < len(head):
            continue
        name = cells[col["Security Name"]]
        etf = cells[col["ETF"]].strip().upper() == "Y"
        out.append(Symbol(ticker=cells[sym].strip(), name=name.strip(), instrument=classify(name, etf=etf),
                          test_issue=cells[col["Test Issue"]].strip().upper() == "Y"))
    return out


def merge(parts: Iterable[list[Symbol]]) -> dict[str, Symbol]:
    """두 파일을 합친다. 같은 티커가 두 번 오면 먼저 온 쪽(NASDAQ 상장)을 쓴다."""
    out: dict[str, Symbol] = {}
    for part in parts:
        for s in part:
            out.setdefault(s.ticker, s)
    return out
