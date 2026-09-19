"""SEC Form 4 — 미장 내부자 거래 (6차 G7, 2026-09-19 등록).

## 왜 분기 벌크인가

SEC 는 Form 3·4·5 를 **구조화된 TSV 로 분기마다** 낸다("Insider Transactions Data
Sets", 2006~). 보고서 XML 을 하나씩 긁을 필요가 없고 과거 이력이 통째로 있다 —
"과거 데이터가 없는 신호를 상태값에 넣지 않는다" 를 처음부터 만족한다.

한계는 **늦다는 것**이다. 분기가 끝나고 몇 주 뒤에 나온다. 그래서 이건 시행(백테스트)
용 경로다. 채택되면 실전엔 EDGAR 일별 색인의 Form 4 XML 경로가 따로 필요하다
(등록 문서 G7 절).

## 관측 시각은 접수일이다

`FILING_DATE` 18:00 ET — `edgar_filings.EdgarPolicy` 와 같은 규칙이다. 거래일
(`TRANS_DATE`)은 접수일보다 최대 이틀(늦으면 그 이상) 앞선다. 거래일로 창을 세면
아직 공시되지 않은 거래를 보는 셈이라 **valid_from 도 접수 시각**으로 둔다.
거래일은 열(`trans_date`)로만 남긴다.

## 무엇을 싣나

Form 4 원본(`DOCUMENT_TYPE == "4"`)의 **비파생 거래**만. 정정본(4/A)은 뺀다 — 원본과
같은 거래를 다시 적어 두 번 세게 된다. 파생(옵션 부여·행사)은 G7 의 질문("내부자가
자기 돈으로 사고 파나")이 아니다.
"""
from __future__ import annotations

import io
import zipfile
from datetime import date, datetime
from typing import Any

import pandas as pd

from quant_rl_trading.collectors.edgar_filings import (
    BACKOFF_BASE,
    RETRIES,
    CollectorUnavailable,
    EdgarPolicy,
    NotYetKnown,
)

TABLE = "form4_trades"
SOURCE = "sec-form345"
#: 분기 ZIP 경로. **SEC 가 2026q2 부터 폴더를 바꿨다**(structureddata → datastandardsinnovation,
#: 2026-09-19 목록 페이지 실측). 옛 분기는 옛 폴더에 그대로 있다 — 차례로 시도하고, 전부 404 면 아직 안 나온 분기다.
DATASET_URLS = (
    "https://www.sec.gov/files/datastandardsinnovation/data/insider-transactions-data-sets/"
    "{year}q{quarter}_form345.zip",
    "https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/"
    "{year}q{quarter}_form345.zip",
)
#: 벌크에 적힌 티커가 비었다는 표기들.
_NO_SYMBOL = {"", "NA", "N/A", "NONE", "-"}


def quarters(start: str, end: str) -> list[tuple[int, int]]:
    """'2024q1' ~ '2026q2' → [(2024, 1), …, (2026, 2)]."""
    def parse(label: str) -> tuple[int, int]:
        year, quarter = label.lower().split("q")
        return int(year), int(quarter)

    (y0, q0), (y1, q1) = parse(start), parse(end)
    out: list[tuple[int, int]] = []
    y, q = y0, q0
    while (y, q) <= (y1, q1):
        out.append((y, q))
        y, q = (y + 1, 1) if q == 4 else (y, q + 1)
    return out


def _date(value: Any) -> date | None:
    """'30-MAY-2025' → date. 빈 값·깨진 값은 None."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text.title(), "%d-%b-%Y").date()
    except ValueError:
        return None


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _read(archive: zipfile.ZipFile, name: str, columns: list[str]) -> pd.DataFrame:
    with archive.open(name) as handle:
        return pd.read_csv(
            handle, sep="\t", dtype=str, usecols=columns, keep_default_na=False, quoting=3
        )


def parse(content: bytes) -> pd.DataFrame:
    """분기 ZIP → Form 4 비파생 거래 한 행씩. 종목 매핑 전(발행사 CIK·티커 원문)."""
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        sub = _read(archive, "SUBMISSION.tsv", [
            "ACCESSION_NUMBER", "FILING_DATE", "DOCUMENT_TYPE", "ISSUERCIK",
            "ISSUERTRADINGSYMBOL", "AFF10B5ONE",
        ])
        trans = _read(archive, "NONDERIV_TRANS.tsv", [
            "ACCESSION_NUMBER", "NONDERIV_TRANS_SK", "TRANS_DATE", "TRANS_CODE",
            "TRANS_SHARES", "TRANS_PRICEPERSHARE", "TRANS_ACQUIRED_DISP_CD",
            "SHRS_OWND_FOLWNG_TRANS", "DIRECT_INDIRECT_OWNERSHIP",
        ])
        owners = _read(archive, "REPORTINGOWNER.tsv", [
            "ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNERNAME", "RPTOWNER_RELATIONSHIP",
            "RPTOWNER_TITLE",
        ])
    sub = sub[sub["DOCUMENT_TYPE"].str.strip() == "4"]
    # 공동 보고(여러 보고자)는 첫 보고자를 대표로, 관계는 전부 잇는다.
    first = owners.groupby("ACCESSION_NUMBER", sort=False).first()
    relation = owners.groupby("ACCESSION_NUMBER", sort=False)["RPTOWNER_RELATIONSHIP"].agg(
        lambda s: ",".join(sorted({p.strip() for v in s for p in v.split(",") if p.strip()}))
    )
    frame = trans.merge(sub, on="ACCESSION_NUMBER", how="inner")
    frame = frame.join(first[["RPTOWNERCIK", "RPTOWNERNAME", "RPTOWNER_TITLE"]], on="ACCESSION_NUMBER")
    frame["relationship"] = frame["ACCESSION_NUMBER"].map(relation).fillna("")
    return frame


def to_rows(
    frame: pd.DataFrame, *, tickers: dict[str, str], policy: EdgarPolicy
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """파싱된 표 → 적재 행. 종목은 **발행사 CIK → 티커**(edgar_filings 와 같은 매핑)로,
    없으면 벌크의 티커 원문으로. 둘 다 없으면 버리고 센다(조용히 버리지 않는다)."""
    rows: list[dict[str, Any]] = []
    stats = {"rows": 0, "no_symbol": 0, "bad_date": 0, "not_yet": 0}
    for record in frame.to_dict(orient="records"):
        cik = str(record["ISSUERCIK"]).strip().zfill(10)
        ticker = tickers.get(cik) or str(record["ISSUERTRADINGSYMBOL"]).strip().upper().replace(".", "-")
        if ticker in _NO_SYMBOL:
            stats["no_symbol"] += 1
            continue
        filed = _date(record["FILING_DATE"])
        if filed is None:
            stats["bad_date"] += 1
            continue
        try:
            moment = policy.for_filing(filed)
        except NotYetKnown:
            stats["not_yet"] += 1
            continue
        traded = _date(record["TRANS_DATE"])
        rows.append({
            "entity_id": f"US:{ticker}",
            "valid_from": moment,
            "observed_at": moment,
            "source": SOURCE,
            "market": "US",
            "accession": str(record["ACCESSION_NUMBER"]),
            "trans_sk": str(record["NONDERIV_TRANS_SK"]),
            "owner_cik": str(record.get("RPTOWNERCIK") or ""),
            "owner_name": str(record.get("RPTOWNERNAME") or ""),
            "relationship": str(record.get("relationship") or ""),
            "title": str(record.get("RPTOWNER_TITLE") or ""),
            "trans_date": traded.isoformat() if traded else "",
            "trans_code": str(record["TRANS_CODE"]).strip(),
            "acquired_disposed": str(record["TRANS_ACQUIRED_DISP_CD"]).strip(),
            "shares": _num(record["TRANS_SHARES"]),
            "price": _num(record["TRANS_PRICEPERSHARE"]),
            "shares_after": _num(record["SHRS_OWND_FOLWNG_TRANS"]),
            "direct_indirect": str(record["DIRECT_INDIRECT_OWNERSHIP"]).strip(),
            "plan_10b5_1": _num(record["AFF10B5ONE"]),
        })
        stats["rows"] += 1
    return rows, stats


def fetch(year: int, quarter: int, *, user_agent: str, sleep: Any = None, client: Any = None) -> bytes:
    """분기 ZIP 을 받는다. 5xx·429 는 지수 백오프로 재시도, 모든 경로가 404 면 **아직 안 나온 분기**다."""
    for template in DATASET_URLS:
        content = _fetch_one(template.format(year=year, quarter=quarter), user_agent=user_agent, sleep=sleep, client=client)
        if content is not None:
            return content
    raise NotYetKnown(f"{year}q{quarter} 벌크가 아직 없다(경로 {len(DATASET_URLS)}곳 모두 404)")


def _fetch_one(url: str, *, user_agent: str, sleep: Any, client: Any) -> bytes | None:
    """한 경로. 404 는 None — 다음 경로를 보라는 뜻이다."""
    import time

    import httpx

    wait = sleep or time.sleep
    last: Exception | None = None
    for attempt in range(RETRIES):
        owned = client is None
        http = client or httpx.Client(
            timeout=120.0, headers={"User-Agent": user_agent}, follow_redirects=True
        )
        try:
            response = http.get(url)
            if response.status_code == 404:
                return None
            if response.status_code >= 500 or response.status_code == 429:
                last = RuntimeError(f"HTTP {response.status_code}")
                wait(BACKOFF_BASE * (2**attempt))
                continue
            response.raise_for_status()
            if response.content[:2] != b"PK":
                raise CollectorUnavailable(f"{url} 가 ZIP 이 아니다(차단·UA 문제일 수 있다)")
            return bytes(response.content)
        except httpx.TransportError as error:
            last = error
            wait(BACKOFF_BASE * (2**attempt))
        finally:
            if owned:
                http.close()
    raise CollectorUnavailable(f"{RETRIES}회 재시도 실패: {url} — {last}")


def run_id(year: int, quarter: int) -> str:
    return f"form4-{year}q{quarter}"

