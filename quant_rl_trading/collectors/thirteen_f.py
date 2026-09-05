"""13F — 미국 기관투자자의 분기말 보유 명세. 출처는 SEC EDGAR.

    uv run python tools/collect_13f.py --dry-run
    uv run python tools/collect_13f.py

운용자산 1억 달러 이상인 기관은 분기말로부터 **45일 안에** 보유 종목을
신고해야 한다(Form 13F-HR). 무료이고, `us_universe`·`us_shares` 가 이미 쓰는
EDGAR 인증(``SEC_EDGAR_USER_AGENT``)을 그대로 쓴다.

## 이 데이터로 무엇을 못 하나 — 먼저 적는다

**매매 신호로 쓰면 안 된다.** 세 가지가 구조적으로 막는다:

1. **최대 45일 늦다.** 6월 30일 기준 보유가 8월 14일에 공개된다. 그사이
   다 팔았을 수 있다 (버크셔 2026 Q2 도 8월 14일 접수였다 — 실측)
2. **분기말 스냅샷 하나뿐이다.** 분기 중에 사고팔았으면 안 보인다
3. **롱 온리다.** 공매도·옵션·채권·해외주식은 대부분 안 들어온다.
   "그 펀드가 강세로 본다" 로 읽으면 절반만 보는 것이다

그래서 이 표는 **참고 자료**다. Analyst 피처로 쓰려면 그 지연을 명시적으로
모델에 넣어야 하고, 지금은 안 한다.

## 함정 둘 — 둘 다 실측으로 걸렸다

### 1. 한 종목이 여러 줄로 온다

버크셔 2026 Q2 는 ``infoTable`` 이 **89줄인데 실제 보유는 29종목**이다.
애플만 12줄로 쪼개져 있다. 이유는 ``otherManager`` — 어느 자회사 운용역이
그 몫을 들고 있는지를 줄마다 따로 적기 때문이다.

    APPLE INC  value=    200,237,120  운용사=4
    APPLE INC  value= 23,341,172,315  운용사=4,11
    APPLE INC  value= 17,808,079,008  운용사=4,8,11
    ...  (12줄)

**합산하지 않으면 상위 목록이 통째로 거짓말이 된다.** 위 그대로 정렬하면
"애플 7.8% · 애플 6.0% · 애플 3.4%" 가 따로 등수에 오르고, 합쳐서 22.0%
1위인 사실이 사라진다. 그래서 CUSIP 으로 접는다.

### 2. ``value`` 는 달러다. 천 달러가 아니다

2023년까지 13F 의 ``value`` 는 **천 달러 단위**였고 그 시절 코드와 문서가
아직 인터넷에 많다. 지금은 달러다. 옛 규칙대로 1,000 을 곱하면 버크셔
포트폴리오가 **$299조**가 된다 — 실제는 $299십억이다.

두 함정 다 **틀린 값이 아니라 그럴듯한 값**을 만든다. 형식 오류가 아니라
아무 데서도 에러가 안 나므로, 화면에 뜬 뒤에야 이상함을 눈치챈다.

## 창고

    entity_id    US:AAPL 같은 종목 (CUSIP→티커를 못 풀면 CUSIP:037833100)
    valid_from   보고 기준일 (분기말). **사건 시각**
    observed_at  접수일 + EDGAR 마감. **우리가 알 수 있었던 시각**
    filer_cik    신고한 기관
    weight       그 기관 포트폴리오 안에서의 비중

``valid_from`` 과 ``observed_at`` 이 최대 45일 벌어진다 — 이 표의 성격
자체가 그 간격이라, 그것을 지우면 데이터가 거짓이 된다.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from quant_rl_trading.collectors.errors import CollectorError

TABLE = "filings_13f"
SOURCE = "sec_edgar"
FORM = "13F-HR"

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}"

#: EDGAR 접수 마감(17:30 ET). ``us_shares`` 와 같은 규칙 — 그날 낸 공시를
#: 그날 장중에 알았다고 하면 미래를 보는 것이 된다.
FILING_CUTOFF_UTC = 21  # 17:30 ET ≈ 21:30 UTC, 보수적으로 22시로 올림
FILING_CUTOFF_MINUTE = 30

#: SEC 는 초당 10회를 넘기면 막는다. 넉넉히 둔다.
MIN_INTERVAL_SEC = 0.15

#: 기본으로 따라갈 기관. **왜 이들인가** — 보유가 공개적으로 검증 가능하고
#: (언론이 매 분기 다룬다) 포트폴리오가 집중형이라 신호가 희석되지 않는다.
#: 분산형 대형 패시브(뱅가드·블랙록)는 사실상 시장 전체라 볼 것이 없다.
DEFAULT_FILERS: dict[str, str] = {
    "1067983": "Berkshire Hathaway",
    "1350694": "Bridgewater Associates",
    "1037389": "Renaissance Technologies",
    "1649339": "Scion Asset Management",
    "1061165": "Lone Pine Capital",
    "1167483": "Tiger Global",
}


class ThirteenFError(CollectorError):
    pass


@dataclass
class Holding:
    issuer: str
    cusip: str
    value_usd: float
    shares: float
    #: 같은 CUSIP 이 몇 줄로 쪼개져 있었나. 합산했다는 사실을 남긴다.
    rows: int = 1


@dataclass
class Filing:
    cik: str
    filer: str
    report_date: str
    filing_date: str
    accession: str
    holdings: list[Holding] = field(default_factory=list)

    @property
    def total_usd(self) -> float:
        return sum(h.value_usd for h in self.holdings)


# -----------------------------------------------------------------------------


def _text(element: Any, tag: str, ns: dict[str, str]) -> str | None:
    found = element.find(f"n:{tag}", ns) if ns else element.find(tag)
    return found.text if found is not None else None


def parse_information_table(xml: str) -> list[Holding]:
    """13F information table 을 CUSIP 으로 **접어서** 돌려준다.

    접지 않으면 상위 목록이 거짓말이 된다 — 모듈독스트링의 함정 1 참고.
    """
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ThirteenFError(f"13F XML 을 못 읽는다: {exc}") from exc

    ns = {"n": root.tag.split("}")[0].strip("{")} if "}" in root.tag else {}
    rows = root.findall(".//n:infoTable", ns) if ns else root.findall(".//infoTable")
    if not rows:
        # **"0행" 과 "형식이 다르다" 를 구분한다.** 이 저장소에서 제일 자주
        # 나는 결함이다. 진짜 빈 신고(보유를 다 정리한 분기)는 있을 수 있다.
        return []

    folded: dict[str, Holding] = {}
    for row in rows:
        cusip = (_text(row, "cusip", ns) or "").strip().upper()
        if not cusip:
            continue
        value = float(_text(row, "value", ns) or 0)
        amount = row.find("n:shrsOrPrnAmt", ns) if ns else row.find("shrsOrPrnAmt")
        shares = float(_text(amount, "sshPrnamt", ns) or 0) if amount is not None else 0.0
        issuer = (_text(row, "nameOfIssuer", ns) or "").strip()

        if cusip in folded:
            existing = folded[cusip]
            existing.value_usd += value
            existing.shares += shares
            existing.rows += 1
        else:
            folded[cusip] = Holding(issuer=issuer, cusip=cusip, value_usd=value, shares=shares)

    return sorted(folded.values(), key=lambda h: -h.value_usd)


def observed_at_for(filing_date: str) -> datetime:
    """접수일 → 우리가 알 수 있었던 시각. **접수 마감 뒤로 민다.**"""
    day = datetime.strptime(filing_date, "%Y-%m-%d").replace(tzinfo=UTC)
    return day.replace(hour=FILING_CUTOFF_UTC, minute=FILING_CUTOFF_MINUTE)


def valid_from_for(report_date: str) -> datetime:
    """보고 기준일(분기말) 마감."""
    day = datetime.strptime(report_date, "%Y-%m-%d").replace(tzinfo=UTC)
    return day.replace(hour=21, minute=0)


def lag_days(filing: Filing) -> int:
    """공개까지 걸린 날. **이 값을 화면이 반드시 보여줘야 한다.**"""
    return (
        datetime.strptime(filing.filing_date, "%Y-%m-%d")
        - datetime.strptime(filing.report_date, "%Y-%m-%d")
    ).days


def recent_filings(payload: dict[str, Any], *, limit: int) -> list[dict[str, str]]:
    """submissions JSON 에서 13F-HR 만 최근 순으로."""
    recent = (payload.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    out: list[dict[str, str]] = []
    for i, form in enumerate(forms):
        if form != FORM:
            continue
        out.append({
            "accession": recent["accessionNumber"][i],
            "report_date": recent["reportDate"][i],
            "filing_date": recent["filingDate"][i],
        })
        if len(out) >= limit:
            break
    return out


def information_table_name(index: dict[str, Any]) -> str | None:
    """첨부 목록에서 보유 명세 XML 을 고른다.

    ``primary_doc.xml`` 은 표지(요약)이고 보유 목록이 아니다. 파일 이름이
    신고마다 다르므로(예: ``56757.xml``) 이름으로 짐작하지 않고 **표지가
    아닌 xml** 중 제일 큰 것을 고른다 — 보유 목록이 언제나 훨씬 크다.
    """
    items = (index.get("directory") or {}).get("item") or []
    candidates = [
        item for item in items
        if str(item.get("name", "")).endswith(".xml")
        and item.get("name") != "primary_doc.xml"
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: int(item.get("size") or 0))["name"]


# -- 네트워크 ------------------------------------------------------------------


class EdgarClient:
    """EDGAR 호출. **신원 없는 요청은 SEC 가 막는다.**"""

    def __init__(self, user_agent: str, *, timeout: float = 30.0,
                 client: httpx.Client | None = None) -> None:
        if not user_agent.strip():
            raise ThirteenFError(
                "SEC_EDGAR_USER_AGENT 가 없다. SEC 는 신원 없는 요청을 막는다."
            )
        self._headers = {"User-Agent": user_agent}
        self._timeout = timeout
        self._client = client

    def _get(self, url: str) -> httpx.Response:
        http = self._client or httpx.Client(timeout=self._timeout)
        try:
            response = http.get(url, headers=self._headers)
        finally:
            if self._client is None:
                http.close()
        if response.status_code != 200:
            # **"없다" 와 "못 받았다" 를 구분한다.** 404 를 빈 결과로 삼키면
            # 신고를 안 한 분기와 우리가 못 받은 분기가 같아 보인다.
            raise ThirteenFError(f"EDGAR {response.status_code} — {url}")
        return response

    def submissions(self, cik: str) -> dict[str, Any]:
        return self._get(SUBMISSIONS_URL.format(cik=int(cik))).json()

    def filing_index(self, cik: str, accession: str) -> dict[str, Any]:
        url = ARCHIVE_URL.format(cik=int(cik), acc=accession.replace("-", "")) + "/index.json"
        return self._get(url).json()

    def information_table(self, cik: str, accession: str, name: str) -> str:
        url = ARCHIVE_URL.format(cik=int(cik), acc=accession.replace("-", "")) + f"/{name}"
        return self._get(url).text


def fetch_filing(client: EdgarClient, cik: str, filer: str, meta: dict[str, str]) -> Filing:
    filing = Filing(
        cik=cik, filer=filer,
        report_date=meta["report_date"], filing_date=meta["filing_date"],
        accession=meta["accession"],
    )
    index = client.filing_index(cik, filing.accession)
    name = information_table_name(index)
    if name is None:
        raise ThirteenFError(
            f"{filer} {filing.report_date}: 보유 명세 xml 이 첨부에 없다 "
            f"(표지만 있다) — acc={filing.accession}"
        )
    filing.holdings = parse_information_table(client.information_table(cik, filing.accession, name))
    return filing


def to_rows(filing: Filing, *, cusip_to_entity: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """창고 행으로. **비중은 여기서 한 번만 계산한다.**"""
    total = filing.total_usd
    if total <= 0:
        return []
    lookup = cusip_to_entity or {}
    valid_from = valid_from_for(filing.report_date)
    observed_at = observed_at_for(filing.filing_date)
    rows: list[dict[str, Any]] = []
    for holding in filing.holdings:
        rows.append({
            # 티커를 못 풀면 CUSIP 을 그대로 쓴다. **모르는 것을 지어내지
            # 않는다** — 나중에 매핑이 생기면 그때 정정본이 들어온다.
            "entity_id": lookup.get(holding.cusip, f"CUSIP:{holding.cusip}"),
            "valid_from": valid_from,
            "observed_at": observed_at,
            "source": SOURCE,
            "filer_cik": filing.cik,
            "filer_name": filing.filer,
            "issuer": holding.issuer,
            "cusip": holding.cusip,
            "value_usd": holding.value_usd,
            "shares": holding.shares,
            "weight": holding.value_usd / total,
            # 몇 줄을 접었는지 남긴다 — 합산했다는 사실 자체가 검증거리다.
            "folded_rows": float(holding.rows),
            "lag_days": float(lag_days(filing)),
        })
    return rows


def ingest_run_id(cik: str, report_date: str) -> str:
    """기관 하나의 한 분기. 재신고(13F-HR/A)는 정정본으로 들어오므로 이
    단위면 충분하다 — 분기가 바뀌면 run_id 가 바뀐다."""
    return f"13f-{cik}-{report_date.replace('-', '')}"
