"""DART 공시 목록 — `event` Analyst 의 입력.

## 왜 필요한가

event Analyst 에는 **이벤트가 없었다.** 피처가 상장 경과일(maturity)과 거래
정지 대용(no_halt) 둘뿐이라, 이름과 달리 "오래되고 안 멈춘 종목" 을 좋아하는
품질 지표에 가까웠다. IC +0.0333 은 합격선(0.03) 위 0.0033 이다 — 표본이
조금만 달라져도 떨어진다.

실적발표·배당·자사주·유상증자는 **날짜가 박힌 진짜 이벤트**이고, DART
공시목록(``list.json``)이 그걸 준다. 재무(``fnlttMultiAcnt``)로는 못 얻는다 —
재무는 숫자만 주고 "무슨 일이 있었는지" 를 말하지 않는다.

## 이중시간

    valid_from  = 접수일(rcept_dt)
    observed_at = 그날 장 마감 이후 (FilingPolicy)

DART 는 **접수 시각을 주지 않는다.** 날짜만 안다. 그래서 재무와 같은 정책을
쓴다 — 그날 늦게 알게 됐다고 본다. 자정으로 잡으면 그날 아침부터 공시를 알던
것이 되어 하루를 앞당겨 본다.

## 날짜 축으로 받는다

회사 축으로 받으면 2,700종목 × 5년이라 콜이 폭발한다. 날짜 축이면 하루에
Y(유가)·K(코스닥) 두 번 × 페이지 몇 개다. 5년이 대략 7~8천 콜이고 일 한도
20,000 안에 들어온다.

## 분류는 여기서 한 번만

``report_nm`` 은 자연어다("현금ㆍ현물배당결정", "[기재정정]유상증자결정").
Analyst 가 매번 문자열을 뒤지게 두면 규칙이 여러 곳으로 흩어진다. 여기서
``doc_type`` 하나로 접어 두고, Analyst 는 그것만 본다.

**정정공시(``[기재정정]``)도 같은 분류로 넣는다.** 정정 자체가 이벤트이고,
원본과 같은 성격의 사건이기 때문이다. 다만 ``doc_id``(rcept_no)가 다르므로
원본을 덮지 않는다 — append-only 창고에서 둘 다 남는다.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from lattice.collectors.dart_source import DartUnavailable
from lattice.collectors.errors import CollectorError

DOCUMENTS = "documents"
SOURCE = "dart"

#: 상장사만. Y = 유가증권, K = 코스닥. N(코넥스)·E(기타)는 매매 대상이 아니다.
CORP_CLASSES = ("Y", "K")

#: 한 페이지 최대. DART 상한이다.
PAGE_SIZE = 100

#: 데이터 없음. 그날 그 시장에 공시가 없었다는 뜻이지 실패가 아니다.
NO_DATA = "013"

#: ``report_nm`` → ``doc_type``. **순서가 규칙이다** — 먼저 걸리는 것이 이긴다.
#: 예: "자기주식취득 신탁계약 체결 결정" 은 자사주이지 계약이 아니다.
CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    # 실적. 정기보고서와 잠정실적 둘 다.
    ("earnings", ("분기보고서", "반기보고서", "사업보고서", "영업(잠정)실적", "영업실적")),
    # 주주환원. 시장이 양(+)으로 읽는다고 알려진 쪽.
    ("buyback", ("자기주식취득", "자기주식 취득", "자사주")),
    # "배당" 하나로 잡는다. 배당결정·주식배당·기준일 결정이 전부 배당 사건이고,
    # 제목이 제각각이라("현금ㆍ현물배당을위한주주명부폐쇄(기준일)결정") 정확한
    # 문구를 나열하면 반드시 빠뜨린다.
    ("dividend", ("배당",)),
    # 희석. 음(−)으로 읽는 쪽.
    ("dilution", ("유상증자", "전환사채", "신주인수권부사채", "교환사채")),
    ("split", ("무상증자", "액면분할", "주식분할")),
    # 위험 신호. 매수를 막는 쪽으로 쓴다.
    ("distress", ("불성실공시", "관리종목", "상장폐지", "감사의견", "거래정지", "회생절차")),
    ("ownership", ("대량보유상황", "임원ㆍ주요주주", "임원·주요주주")),
    # 수주. 실적으로 이어지기 전에 먼저 공시된다.
    ("contract", ("단일판매", "공급계약", "수주")),
)

#: 위 어디에도 안 걸리는 것. **버리지 않는다** — 공시가 몰리는 것 자체가
#: 정보이고, 분류를 못 했다고 사실이 사라지는 것은 아니다.
OTHER = "other"

FILING_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="


def classify(report_name: str) -> str:
    """공시 제목 → 분류. 못 걸러도 ``other`` 로 남긴다."""
    text = report_name.replace(" ", "")
    for label, needles in CATEGORIES:
        for needle in needles:
            if needle.replace(" ", "") in text:
                return label
    return OTHER


def parse_receipt_date(value: str) -> date | None:
    """``20240516`` → date. 형식이 깨진 행은 버린다."""
    text = str(value or "").strip()
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
    except ValueError:
        return None


def session_timestamp(day: date) -> datetime:
    """``valid_from``. 접수일 그 자체를 사실의 시각으로 둔다."""
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def normalize_filings(
    rows: list[dict[str, Any]],
    *,
    market: str,
    observed_at_for: Callable[[date], datetime],
) -> list[dict[str, Any]]:
    """DART 응답 → ``documents`` 행.

    **상장 종목코드가 없는 행은 버린다.** 비상장 계열사 공시가 섞여 오는데,
    종목이 없으면 어느 종목의 사건인지 말할 수 없다.
    """
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for row in rows:
        stock = str(row.get("stock_code") or "").strip()
        receipt = str(row.get("rcept_no") or "").strip()
        if not stock or not receipt:
            continue
        day = parse_receipt_date(str(row.get("rcept_dt") or ""))
        if day is None:
            continue

        entity = f"{market}:{stock}"
        key = (entity, receipt)
        if key in seen:
            continue
        seen.add(key)

        title = str(row.get("report_nm") or "").strip()
        out.append(
            {
                "entity_id": entity,
                "valid_from": session_timestamp(day),
                "observed_at": observed_at_for(day),
                "source": SOURCE,
                "doc_id": receipt,
                "doc_type": classify(title),
                "title": title,
                "filer": str(row.get("flr_nm") or row.get("corp_name") or "").strip(),
                "url": f"{FILING_URL}{receipt}",
                "raw_path": None,
            }
        )
    return out


def filings_run_id(market: str, day: date, corp_class: str) -> str:
    """재개 단위는 (날짜, 시장구분) 이다."""
    return f"bf-dart-filings-{market}-{day.isoformat()}-{corp_class}"


@dataclass
class FilingsResult:
    day: date
    corp_class: str
    rows: int
    skipped: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class FilingsReport:
    days: int = 0
    rows: int = 0
    skipped: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)
    by_type: dict[str, int] = field(default_factory=dict)

    def absorb(self, result: FilingsResult) -> None:
        if result.skipped:
            self.skipped += 1
            return
        if result.error:
            self.failures.append((f"{result.day} {result.corp_class}", result.error))
            return
        self.rows += result.rows

    def render(self) -> str:
        return (
            f"날짜 {self.days}개 · 공시 {self.rows:,}행 "
            f"(건너뜀 {self.skipped}, 실패 {len(self.failures)})"
        )


@dataclass
class FilingsBackfiller:
    """날짜 축으로 도는 공시 수집. 재무 백필과 축이 다르다."""

    store: Any
    source: Any
    policy: Any
    market: str = "KR"
    corp_classes: tuple[str, ...] = CORP_CLASSES

    def plan(self, start: date, end: date) -> list[date]:
        """달력일 전부. 휴장일에도 공시는 접수된다 — 거래일로 자르면 놓친다."""
        days: list[date] = []
        cursor = start
        while cursor <= end:
            days.append(cursor)
            cursor = date.fromordinal(cursor.toordinal() + 1)
        return days

    def pending(self, days: list[date]) -> list[tuple[date, str]]:
        out: list[tuple[date, str]] = []
        for day in days:
            for corp_class in self.corp_classes:
                run_id = filings_run_id(self.market, day, corp_class)
                if not self.store.ingest_run_recorded(DOCUMENTS, run_id):
                    out.append((day, corp_class))
        return out

    def run_day(self, day: date, corp_class: str) -> FilingsResult:
        run_id = filings_run_id(self.market, day, corp_class)
        if self.store.ingest_run_recorded(DOCUMENTS, run_id):
            return FilingsResult(day, corp_class, 0, skipped=True)

        try:
            raw = self.source.filings(day=day, corp_class=corp_class)
        except CollectorError as error:
            return FilingsResult(day, corp_class, 0, error=str(error))

        if not raw:
            # 공시가 없는 날(휴일)이다. 빈 것도 완료로 기록해야 다시 안 묻는다.
            # 창고는 빈 배치를 매니페스트에 남기지 않으므로 여기서 끝낸다.
            return FilingsResult(day, corp_class, 0)

        rows = normalize_filings(
            raw, market=self.market, observed_at_for=self.policy.for_filing
        )
        if not rows:
            return FilingsResult(day, corp_class, 0)
        written = self.store.append(
            DOCUMENTS, rows, ingest_run_id=run_id, source=SOURCE
        )
        return FilingsResult(day, corp_class, written)

    def run(self, units: list[tuple[date, str]]) -> Iterator[FilingsResult]:
        for day, corp_class in units:
            yield self.run_day(day, corp_class)
