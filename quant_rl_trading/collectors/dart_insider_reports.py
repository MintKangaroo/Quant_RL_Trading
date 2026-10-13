"""DART 「임원ㆍ주요주주 특정증권등 소유상황보고서」 **원문 파싱** — 국장 내부자 재료 심화(AR2 계열, 2026-10-13).

## 왜 원문인가

`insider_trades`(DART ``elestock.json``, 6차 G4 의 입력)는 **증감 주수만** 준다. 보고사유가 없다.
그래서 장내 순매수와 기계적 변동(증여·신규선임·스톡옵션·전환·대여·상속…)이 한 숫자에 섞인다.
표본 첫 건(동화약품 2025-03-24, -115만 주)이 **증여**였다 — G4 는 이걸 "내부자 대량 처분" 으로 읽었다.
그리고 elestock 은 **최근 약 2년만** 준다(창고 최소 2024-08-30) — 확장 패널(2021-08~)을 못 덮는다.

원문 ``document.xml`` 은 접수번호마다 하나이고, 「다. 세부변동내역」 표가 **구조화 표지**를 달고 온다
(2021~2026 표본 24건 모두 같은 서식, 2026-10-13 실측):

    <TU AUNIT="RPT_RSN" AUNITVALUE="01">장내매수(+)</TU>   보고사유 코드·이름
    <TU AUNIT="MDF_DM"  AUNITVALUE="20250319">…</TU>      변동일
    <TU AUNIT="STR_KND" AUNITVALUE="1">보통주</TU>        특정증권 종류
    <TE ACODE="BFR_STK_CNT">·MDF_STK_CNT·AFR_STK_CNT     변동 전·증감·변동 후 주수
    <TE ACODE="ACI_AMT2">74,900</TE>                     취득/처분 단가(원)
    <TU AUNIT="TRD_PLN_RD">                               거래계획보고 일자(2024-07 사전공시제 뒤 서식)

머리(보고자) 쪽도 표지가 있다: ``IFR_NM``(성명) · ``IFR_TP``(개인/법인…) · ``STF_RYN``(등기 Y/N) ·
``STF_PSM``(직위명) · ``MAIN_SH``(10%이상주주 1 · 사실상지배주주 2 · 아님 4) · ``RPT_DST``(신규 1 · 변동 2) ·
``FLT_SUM``(발행주식 총수). 파서를 새로 들이지 않는다 — 표지가 있으니 정규식으로 충분하다.

## 시각

`valid_from = observed_at = 접수일 18:00 KST`(``FilingPolicy`` · `insider_trades` 와 같은 규칙).
**변동일로 창을 세지 않는다** — 변동일은 접수일보다 앞이라 아직 공시되지 않은 거래를 보게 된다. 변동일은 열로만 남긴다.

## 무엇을 버리나

- 정정본(``[기재정정]``)은 **받지 않는다.** 원본과 같은 거래를 다시 적어 두 번 센다(Form 4 의 4/A 와 같은 규칙).
  원본이 그날 알 수 있던 사실이다 — 정정은 나중에 알게 된 것이라 원본을 지우면 시점이 깨진다.
- 생년월일·주소·담당자 연락처는 **싣지 않는다.** 보고자 구분은 성명+생년월일의 해시(``owner_key``)로만 한다.
"""

from __future__ import annotations

import hashlib
import html
import re
from datetime import date, datetime
from typing import Any

from quant_rl_trading.collectors.dart_documents import decode

TABLE = "insider_reports"
SOURCE = "dart-doc"

#: 보고사유 → 범주. **이름(괄호 부호 포함)으로 가른다** — 코드표가 공개 문서에 없어서 코드만 믿지 않는다.
#: 순서가 중요하다(먼저 맞는 것). 범주:
#:   market     장내매수·장내매도·시간외매매 — 거래소에서 자기 판단으로 사고판 것(CMP 의 "거래")
#:   offmarket  장외매수·장외매도 — 상대가 정해진 거래(블록·경영권)
#:   grant      스톡옵션·자사주상여금·우리사주 — 보상(기계적)
#:   role       신규선임·신규상장·신규보고·퇴임·주요주주 해당/해소 — 보고 의무 변화(기계적)
#:   transfer   증여·수증·상속·대여·차입·상환 — 소유권 이전(정보 아님)
#:   corporate  유상·무상증자·전환·행사·합병·분할·감자·주식매수청구권·행사가액조정 — 회사 사건(기계적)
#:   other      기타
CATEGORY_PATTERNS: tuple[tuple[str, str], ...] = (
    ("market", r"^장내(매수|매도)|^시간외|^장내"),
    ("offmarket", r"^장외"),
    ("grant", r"주식매수선택권|스톡옵션|상여|우리사주|성과|보상"),
    ("role", r"신규선임|신규상장|신규보고|선임|퇴임|해임|주요주주|최초"),
    ("transfer", r"증여|수증|상속|대여|차입|상환|반환|담보|신탁|처분신탁|명의"),
    ("corporate", r"증자|전환|행사|합병|분할|감자|청구권|병합|교환|소각|조정|배정|인수|출자"),
)

_TR = re.compile(r"<TR\b[^>]*>(.*?)</TR>", re.S | re.I)
_CELL = re.compile(
    r"<(?P<tag>TE|TU|TD)\b(?P<attrs>[^>]*)>(?P<body>.*?)</(?P=tag)>", re.S | re.I
)
_ATTR = re.compile(r'(ACODE|AUNIT|AUNITVALUE)="([^"]*)"', re.I)
_NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def category(reason: str) -> str:
    text = re.sub(r"\s+", "", reason or "")
    for name, pattern in CATEGORY_PATTERNS:
        if re.search(pattern, text):
            return name
    return "other"


def _clean(body: str) -> str:
    text = re.sub(r"<[^>]+>", " ", body)
    text = html.unescape(text.replace("&cr;", " "))
    return re.sub(r"\s+", " ", text).strip()


def _cells(block: str) -> list[tuple[str, str, str]]:
    """(코드, 표지값, 본문) — 코드는 ACODE 또는 AUNIT."""
    out = []
    for match in _CELL.finditer(block):
        attrs = dict((k.upper(), v) for k, v in _ATTR.findall(match.group("attrs")))
        code = attrs.get("ACODE") or attrs.get("AUNIT") or ""
        out.append((code, attrs.get("AUNITVALUE", ""), _clean(match.group("body"))))
    return out


def number(text: str | None) -> float | None:
    """'1,433,085' → 1433085. '-' · 빈 값 → None. '취득 (9,250)' 처럼 글자·괄호가 섞이면 첫 숫자."""
    raw = (text or "").strip()
    if raw in ("", "-"):
        return None
    found = _NUM.search(raw)
    if not found:
        return None
    try:
        return float(found.group(0).replace(",", ""))
    except ValueError:
        return None


def _day(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 8:
        try:
            return date(int(digits[:4]), int(digits[4:6]), int(digits[6:])).isoformat()
        except ValueError:
            return ""
    return ""


def parse(markup: str) -> dict[str, Any]:
    """원문(HTML 문자열) → {'header': {...}, 'lines': [...]}. 세부변동내역 표가 없으면 lines 가 빈다."""
    header: dict[str, str] = {}
    lines: list[dict[str, Any]] = []
    for block in _TR.findall(markup):
        cells = _cells(block)
        codes = {c for c, _, _ in cells}
        if "RPT_RSN" in codes:
            row: dict[str, Any] = {}
            for code, value, body in cells:
                if code == "RPT_RSN":
                    row["reason_code"], row["reason"] = value, body
                elif code == "MDF_DM":
                    row["change_date"] = _day(value or body)
                elif code == "STR_KND":
                    row["security_code"], row["security"] = value, body
                elif code == "BFR_STK_CNT":
                    row["before"] = number(body)
                elif code == "MDF_STK_CNT":
                    row["change"] = number(body)
                elif code == "AFR_STK_CNT":
                    row["after"] = number(body)
                elif code == "ACI_AMT2":
                    row["price"], row["price_raw"] = number(body), body
                elif code == "RMK":
                    row["remark"] = body
                elif code == "TRD_PLN_RD":
                    row["plan_report_date"] = _day(value) or ("" if body in ("", "-") else body)
            lines.append(row)
            continue
        for code, value, body in cells:
            if code and code not in header:
                header[code] = value if value and value not in ("-",) and code in _VALUE_CODES else body
    return {"header": header, "lines": lines}


#: 머리 표지 중 값(AUNITVALUE)을 쓰는 것 — 나머지는 본문.
_VALUE_CODES = frozenset({"STF_RYN", "MAIN_SH", "RPT_DST", "IFR_TP", "CRP_DST", "RPT_RSP_DT", "RPT_REF_DT"})


def owner_key(name: str, ident: str) -> str:
    """보고자 구분 키. 성명+생년월일(사업자번호)의 해시 — 원값은 싣지 않는다."""
    return hashlib.sha256(f"{name.strip()}|{ident.strip()}".encode()).hexdigest()[:16]


def to_rows(parsed: dict[str, Any], *, rcept_no: str, entity_id: str, moment: datetime) -> list[dict[str, Any]]:
    """파싱 결과 → 적재 행(세부변동 한 줄 = 한 행). ``entity_id`` 는 창고 documents 의 것을 그대로 쓴다."""
    h = parsed["header"]
    base = {
        "entity_id": entity_id, "valid_from": moment, "observed_at": moment, "source": SOURCE, "market": "KR",
        "rcept_no": rcept_no,
        "reporter": h.get("IFR_NM", ""),
        "owner_key": owner_key(h.get("IFR_NM", ""), h.get("IFR_ID2", "")),
        "reporter_type": h.get("IFR_TP", ""),
        "registered": h.get("STF_RYN", ""),
        "position": h.get("STF_PSM", ""),
        "major_holder": h.get("MAIN_SH", ""),
        "report_kind": h.get("RPT_DST", ""),
        "shares_outstanding": number(h.get("FLT_SUM")),
        "obligation_date": _day(h.get("RPT_RSP_DT", "")),
    }
    rows = []
    for i, line in enumerate(parsed["lines"]):
        reason = str(line.get("reason", ""))
        rows.append({
            **base,
            "line": i,
            "reason_code": str(line.get("reason_code", "")),
            "reason": reason,
            "category": category(reason),
            "change_date": str(line.get("change_date", "")),
            "security_code": str(line.get("security_code", "")),
            "before": line.get("before"),
            "change": line.get("change"),
            "after": line.get("after"),
            "price": line.get("price"),
            "price_raw": str(line.get("price_raw", ""))[:60],
            "plan_report_date": str(line.get("plan_report_date", "")),
        })
    return rows


def parse_bytes(raw: bytes) -> dict[str, Any]:
    """``document.xml`` ZIP 안의 파일 바이트(첫 항목) → parse. 인코딩은 선언을 믿지 않는다(dart_documents.decode)."""
    return parse(decode(raw))


def run_id(moment: datetime, tag: str) -> str:
    return f"insider-reports-{tag}-{moment:%Y%m%dT%H%M%S}"
