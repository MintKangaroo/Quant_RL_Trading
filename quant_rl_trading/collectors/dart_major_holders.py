"""DART 지분공시 — **주식등의대량보유상황보고서(5% 룰)** 를 `major_holders` 에 넣는다.

## 왜 이 재료인가

창고는 이 공시를 **제목으로만** 들고 있다(`documents` 51,463건 · 2,581종목 · 2021-08~, 9/27 실측).
제목에는 "누가 몇 %를 왜 들었나" 가 없다. 그 셋이 신호다 — 기관이 5% 선을 넘어 사 모으는 중인지,
경영참가 목적으로 새로 들어왔는지, 반대로 5% 아래로 빠져나갔는지.

## 접수일 한 시각뿐이다 — 사유발생일은 API 에 없다

``majorstock.json`` 의 시각 필드는 ``rcept_dt``(접수일) 하나다. 보유 변동이 **실제로 일어난 날**
(사유발생일, 법정 보고기한은 5영업일)은 이 응답에 없다 — 원문 표에만 있다. 그래서

    valid_from  = 접수일 09:00 KST      같은 공시를 가리키는 `documents` 행과 같은 규약
    observed_at = 그 공시가 목록에 처음 올라온 시각(`documents` 첫 revision) = 접수일 18:00 KST

로 잡는다. **창을 사유발생일로 세면 아직 공시되지 않은 변동을 보게 된다** — G7(미장 Form 4)이
거래일 대신 접수일로 창을 센 것과 같은 이유다(`ranker_sources.form4_trading`). 사유발생일을 넣지
않는 대신 잃는 것은 "언제 산 주식인가" 뿐이고, 우리가 쓰는 것은 "언제 알려졌나" 다.

`documents` 에 같은 접수번호가 없으면(목록 수집이 그날을 못 덮었으면) 접수일 18:00 KST 로 떨어뜨린다
— 관측 시각을 앞당기지 않는 쪽이다.

## 응답이 2년만 온다 (2026-09-27 실측)

60개사 346행의 접수일 최소가 **2024-09-30** 이었다(오늘 −2년). 삼성전자도 41행 전부 2024-10 이후다.
즉 이 엔드포인트는 **최근 2년 롤링 창**이다. 6차 판정 창(2025-05~2026-06)과 11월 금고 창(2026-07~11)은
덮지만 **확장 패널(2021-08~)은 못 덮는다.** 그 앞 구간을 채우려면 공시 원문 51,463건을 받아
표를 파싱해야 한다(일 한도 20,000 → 3일). 등록 문서에 한계로 적는다.

## 보유목적도 응답에 없다

``report_tp``(일반|약식)와 ``report_resn``(사유 자유서술)뿐이다. 그래서 목적은 **대리변수**다 —
약식보고는 경영권에 영향을 줄 목적이 아닌 전문투자자·공적기금만 할 수 있으므로(자본시장법 §147)
`약식` → 단순투자로 읽고, `일반` 은 사유 문구로 가른다. 대리변수라는 사실을 열 이름
(`purpose`)이 아니라 이 docstring 과 등록 문서에 남긴다.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from datetime import time as dtime
from typing import Any
from zoneinfo import ZoneInfo

TABLE = "major_holders"
SOURCE = "dart-majorstock"
MARKET = "KR"
SEOUL = ZoneInfo("Asia/Seoul")

#: `documents` 규약 — 접수일 09:00 KST 가 valid_from, 18:00 KST 가 observed_at.
VALID_HOUR_KST = 9
OBSERVED_HOUR_KST = 18

# --------------------------------------------------------------------------- 보고자 분류

#: 국민연금은 따로 센다 — 국내 최대 단일 기관이고 배분 규칙이 공개돼 있어
#: 다른 기관과 행동이 다르다.
_NPS = re.compile(r"국민연금")

#: 기관 표지. 라틴문자 검사보다 **앞**에 둔다 — ``BlackRockFundAdvisors`` 를
#: 외국계가 아니라 기관으로 세고 싶다(운용 주체인 것이 국적보다 중요하다).
#: 첫 판(9/27 스모크)에서 `시너지아이비투자`·`지케이에셋`·`점프업투자조합` 이 개인으로
#: 떨어졌다 — 맨 '투자'·'에셋'·'조합' 이 빠져 있었다.
_INSTITUTION = re.compile(
    r"자산운용|운용|투자|자문|일임|인베스트|캐피탈|파트너스|사모|조합|에셋|"
    r"신탁|증권|은행|생명|화재|해상|보험|공제|연금|기금|벤처|신기술|리츠|"
    r"자산관리|어드바이저|Asset|Capital|Partner|Invest|Fund|Advis|Management|"
    r"Securities|Bank"
)

#: 라틴문자 세 자 이상이면 외국계로 본다(NorgesBank·GIC·ChugokuMarinePaints …).
_LATIN = re.compile(r"[A-Za-z]{3,}")

#: 법인 표지. 길이 규칙 **앞**에 둔다 — `삼성물산`·`현대차` 처럼 네 자 이하인 상호가 있다.
_CORPORATE = re.compile(
    r"주식회사|\(주\)|㈜|유한회사|합자회사|홀딩스|지주|그룹|물산|산업|전자|화학|건설|"
    r"중공업|조선|항공|해운|철강|제철|제약|바이오|반도체|디스플레이|식품|제과|통신|"
    r"엔지니어링|쇼핑|백화점|케미칼|소재|에너지|정밀|기계|공업|생명과학|헬스케어|"
    r"유통|물류|텔레콤|네트웍스|시스템|Inc|Corp|Ltd|LLC|Co\."
)

#: **개인은 순한글 네 자 이하**로 가른다. 법인 표지 목록만으로 개인을 거르려 하면
#: 목록에 없는 상호가 전부 개인이 되는데(첫 판에서 69% 가 개인이었다), 반대로
#: 사람 이름은 형태가 좁다 — 이 방향이 틀릴 여지가 적다.
#: **남는 오분류는 개인 ↔ 법인 사이뿐이고, G10 피처는 그 둘을 쓰지 않는다**
#: (기관·국민연금·외국계만 쓴다) — 틀려도 피처가 바뀌지 않는 자리에 오차를 몰아 둔 것이다.
_HANGUL_NAME = re.compile(r"^[가-힣]{2,4}$")

REPORTER_CLASSES = ("nps", "institution", "foreign", "corporate", "individual")


def reporter_class(reporter: str) -> str:
    """보고자 이름 → 분류. 규칙은 이 함수 하나에만 있다(등록 문서 §보고자 분류).

    순서가 의미다: 국민연금 → 기관 표지 → 라틴문자(외국계) → 법인 표지 → 순한글 이름(개인) → 법인.
    """
    name = str(reporter or "").strip()
    if not name:
        return "individual"
    if _NPS.search(name):
        return "nps"
    if _INSTITUTION.search(name):
        return "institution"
    if _LATIN.search(name):
        return "foreign"
    if _CORPORATE.search(name):
        return "corporate"
    if _HANGUL_NAME.match(name.replace(" ", "")):
        return "individual"
    return "corporate"


# --------------------------------------------------------------------------- 보유목적 대리변수

#: `일반` 보고의 사유 문구에서 경영참가를 읽는다.
_MANAGEMENT = re.compile(r"경영참[가여]|경영권|임원.{0,4}선임|주주제안|지배구조")
#: 단순투자를 명시한 문구.
_SIMPLE = re.compile(r"단순투자|단순\s*추가|일반투자")

PURPOSES = ("simple", "general", "management")


def purpose(report_tp: str, report_resn: str) -> str:
    """(보고구분, 사유) → ``simple`` | ``general`` | ``management``.

    **응답에 보유목적 필드가 없다.** 약식보고는 경영권에 영향을 줄 목적이 아닌
    전문투자자·공적기금만 할 수 있어 그 자체가 단순투자의 증거다(자본시장법 §147).
    `일반` 은 사유 문구로 가르고, 문구가 아무것도 말하지 않으면 ``general`` —
    "모르겠다" 를 ``simple`` 로 접으면 경영참가가 단순투자에 섞인다.
    """
    tp = str(report_tp or "").strip()
    resn = str(report_resn or "")
    if _MANAGEMENT.search(resn):
        return "management"
    if tp == "약식":
        return "simple"
    if _SIMPLE.search(resn):
        return "simple"
    return "general"


# --------------------------------------------------------------------------- 정규화


def _number(value: Any) -> float | None:
    text = str(value or "").replace(",", "").replace("−", "-").strip()
    if text in ("", "-", "--"):
        return None
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        return float(text)
    except ValueError:
        return None


def _receipt_date(value: Any) -> date | None:
    text = str(value or "").strip().replace(".", "-").replace("/", "-")
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def valid_at(day: date) -> datetime:
    return datetime.combine(day, dtime(VALID_HOUR_KST, 0), tzinfo=SEOUL)


def observed_at(day: date) -> datetime:
    return datetime.combine(day, dtime(OBSERVED_HOUR_KST, 0), tzinfo=SEOUL)


def normalize(
    stock_code: str,
    payload: list[dict[str, Any]],
    *,
    since: date,
    after: date | None = None,
    observed_by_receipt: dict[str, datetime] | None = None,
) -> list[dict[str, Any]]:
    """``majorstock.json`` 의 ``list`` → `major_holders` 행.

    ``after`` 가 있으면 그 날짜 **이후** 접수분만 — 이어받기 단위다.
    ``observed_by_receipt`` 는 접수번호 → 공시 목록 첫 관측 시각. 없는 접수번호는
    접수일 18:00 KST 로 떨어진다(관측 시각을 앞당기지 않는 쪽).
    """
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for item in payload:
        day = _receipt_date(item.get("rcept_dt"))
        if day is None or day < since or (after is not None and day <= after):
            continue
        rcept_no = str(item.get("rcept_no") or "").strip()
        reporter = str(item.get("repror") or "").strip()
        if not rcept_no:
            continue
        ratio = _number(item.get("stkrt"))
        change = _number(item.get("stkrt_irds"))
        if ratio is None and change is None:
            # 비율도 증감도 없는 행은 신호가 없다. 저장하면 결측만 늘어난다.
            continue
        report_tp = str(item.get("report_tp") or "").strip()
        resn = " / ".join(
            line.strip(" -\t") for line in str(item.get("report_resn") or "").splitlines() if line.strip(" -\t")
        )
        moment = None if observed_by_receipt is None else observed_by_receipt.get(rcept_no)
        row = {
            "entity_id": f"{MARKET}:{stock_code}",
            "valid_from": valid_at(day),
            "observed_at": moment or observed_at(day),
            "source": SOURCE,
            "market": MARKET,
            "rcept_no": rcept_no,
            "reporter": reporter,
            "reporter_class": reporter_class(reporter),
            "report_tp": report_tp,
            "purpose": purpose(report_tp, resn),
            "report_resn": resn,
            "ratio": ratio,
            "ratio_change": change,
            "shares": _number(item.get("stkqy")),
            "shares_change": _number(item.get("stkqy_irds")),
            "contract_ratio": _number(item.get("ctr_stkrt")),
        }
        # 한 응답 안에 같은 (접수번호, 보고자) 가 두 번 오는 경우가 있다(특별관계자 줄).
        # 자연키가 같으므로 마지막 것만 남긴다 — 창고에 중복을 밀어넣지 않는다.
        seen[(rcept_no, reporter)] = row
    return sorted(seen.values(), key=lambda r: (str(r["valid_from"]), str(r["rcept_no"]), str(r["reporter"])))


def batch_run_id(batch: int, now: datetime) -> str:
    """묶음 × 실행 시각 단위. 회사마다 적재하면 파티션이 터진다(tools docstring)."""
    return f"major-holders-{now:%Y%m%dT%H%M%S}-b{batch:03d}"
