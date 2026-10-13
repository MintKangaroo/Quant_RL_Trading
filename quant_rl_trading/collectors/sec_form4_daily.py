"""SEC Form 4 **일별 증분** — EDGAR 일별 색인 + 접수 원문 XML (2026-10-13, AR shadow 의 미장 내부자 입력).

## 왜 따로 있나

분기 벌크(`sec_form4.py`)는 분기가 끝나고 몇 주 뒤에 나온다. 그 사이 `form4_trades` 가 멈춰
AR shadow 의 G7 열이 0 으로 채점됐다(마지막 행 2026-07-01, 2026-10-13 발견). 매일 받는 길이 필요하다.

1. ``Archives/edgar/daily-index/<연>/QTR<n>/form.<YYYYMMDD>.idx`` — 그날 접수 목록(폼 종류·CIK·파일 경로).
   Form 4 는 발행사·보고자 쪽으로 **두 번** 실릴 수 있다 → 파일 경로로 하나만 남긴다. ``4/A``(정정)는 뺀다(벌크와 같은 규칙).
2. ``Archives/<파일 경로>.txt`` — 접수 원문. 안의 ``<ownershipDocument>`` XML 을 읽는다.

sec.gov robots.txt 는 ``/Archives`` 를 막지 않는다(2026-10-13 확인). 초당 10건 규칙은 `EdgarSource` 와 같이 8건으로 잡는다.

## 벌크와 같은 열

행 모양은 `sec_form4.to_rows` 와 같다(비파생 거래 한 줄 = 한 행). 다른 것은 둘:
- ``trans_sk`` 는 벌크의 대리키(NONDERIV_TRANS_SK)가 없어 ``d<순번>`` 으로 둔다. 나중에 그 분기 벌크를 받을 때는
  **이미 있는 접수번호를 건너뛴다**(`tools/backfill_form4.py`) — 같은 거래를 두 번 세지 않게.
- ``observed_at`` 은 **실제로 받은 시각**이다(라이브 규칙). ``valid_from`` 은 접수일 18:00 ET(`EdgarPolicy`).
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date, datetime
from typing import Any

SOURCE = "sec-form4-daily"
INDEX_URL = "https://www.sec.gov/Archives/edgar/daily-index/{year}/QTR{quarter}/form.{day:%Y%m%d}.idx"
ARCHIVE_URL = "https://www.sec.gov/Archives/{path}"
_DOC = re.compile(r"<ownershipDocument>.*?</ownershipDocument>", re.S)
_NO_SYMBOL = {"", "NA", "N/A", "NONE", "-"}


def index_url(day: date) -> str:
    return INDEX_URL.format(year=day.year, quarter=(day.month - 1) // 3 + 1, day=day)


def form4_paths(index_text: str) -> list[str]:
    """form.idx 본문 → 원본 Form 4(정정 제외)의 파일 경로, 중복 없이 순서대로."""
    seen: dict[str, None] = {}
    for line in index_text.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0] == "4" and parts[-1].startswith("edgar/data/"):
            seen.setdefault(parts[-1], None)
    return list(seen)


def accession_of(path: str) -> str:
    return path.rsplit("/", 1)[-1].removesuffix(".txt")


def _text(node: ET.Element | None, path: str) -> str:
    if node is None:
        return ""
    found = node.find(path)
    if found is None:
        return ""
    value = found.find("value")
    return ((value.text if value is not None else found.text) or "").strip()


def _num(text: str) -> float | None:
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def parse(submission: str) -> dict[str, Any] | None:
    """접수 원문 → {issuer_cik, symbol, document_type, aff10b5one, owners[], trades[]}. XML 이 없으면 None."""
    match = _DOC.search(submission)
    if not match:
        return None
    root = ET.fromstring(match.group(0))
    owners = []
    for owner in root.findall("reportingOwner"):
        rel = owner.find("reportingOwnerRelationship")
        kinds = [name for tag, name in (("isDirector", "Director"), ("isOfficer", "Officer"),
                                        ("isTenPercentOwner", "TenPercentOwner"), ("isOther", "Other"))
                 if rel is not None and (rel.findtext(tag) or "").strip().lower() in ("1", "true")]
        owners.append({
            "cik": (owner.findtext("reportingOwnerId/rptOwnerCik") or "").strip(),
            "name": (owner.findtext("reportingOwnerId/rptOwnerName") or "").strip(),
            "relationship": kinds,
            "title": ((rel.findtext("officerTitle") if rel is not None else "") or "").strip(),
        })
    trades = []
    for tx in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        trades.append({
            "trans_date": _text(tx, "transactionDate"),
            "trans_code": (tx.findtext("transactionCoding/transactionCode") or "").strip(),
            "shares": _num(_text(tx, "transactionAmounts/transactionShares")),
            "price": _num(_text(tx, "transactionAmounts/transactionPricePerShare")),
            "acquired_disposed": _text(tx, "transactionAmounts/transactionAcquiredDisposedCode"),
            "shares_after": _num(_text(tx, "postTransactionAmounts/sharesOwnedFollowingTransaction")),
            "direct_indirect": _text(tx, "ownershipNature/directOrIndirectOwnership"),
        })
    aff = (root.findtext("aff10b5One") or "").strip().lower()
    return {
        "document_type": (root.findtext("documentType") or "").strip(),
        "issuer_cik": (root.findtext("issuer/issuerCik") or "").strip(),
        "symbol": (root.findtext("issuer/issuerTradingSymbol") or "").strip(),
        "aff10b5one": 1.0 if aff in ("1", "true") else (0.0 if aff in ("0", "false") else None),
        "owners": owners,
        "trades": trades,
    }


def to_rows(parsed: dict[str, Any], *, accession: str, filed: date, valid_from: datetime, observed_at: datetime,
            tickers: dict[str, str]) -> tuple[list[dict[str, Any]], str]:
    """한 접수 → 적재 행. 두 번째 값은 버린 사유('' 이면 실었다). 공동 보고는 첫 보고자를 대표로(벌크와 같다)."""
    if parsed["document_type"] != "4":
        return [], "not_form4"
    cik = parsed["issuer_cik"].zfill(10)
    ticker = tickers.get(cik) or parsed["symbol"].upper().replace(".", "-")
    if ticker in _NO_SYMBOL:
        return [], "no_symbol"
    if not parsed["trades"]:
        return [], "no_nonderiv"
    first = parsed["owners"][0] if parsed["owners"] else {"cik": "", "name": "", "title": ""}
    relation = ",".join(sorted({k for o in parsed["owners"] for k in o["relationship"]}))
    rows = []
    for i, tx in enumerate(parsed["trades"]):
        rows.append({
            "entity_id": f"US:{ticker}", "valid_from": valid_from, "observed_at": observed_at,
            "source": SOURCE, "market": "US", "accession": accession, "trans_sk": f"d{i}",
            "owner_cik": first["cik"], "owner_name": first["name"], "relationship": relation, "title": first["title"],
            "trans_date": tx["trans_date"], "trans_code": tx["trans_code"], "acquired_disposed": tx["acquired_disposed"],
            "shares": tx["shares"], "price": tx["price"], "shares_after": tx["shares_after"],
            "direct_indirect": tx["direct_indirect"], "plan_10b5_1": parsed["aff10b5one"],
        })
    return rows, ""


def run_id(day: date) -> str:
    return f"form4-daily-{day:%Y%m%d}"
