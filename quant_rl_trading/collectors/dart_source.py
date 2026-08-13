"""DART 재무 — `fundamental` Analyst 의 입력.

## 왜 DART 인가

재무제표에서 **이중시간을 정확히 찍을 수 있는 유일한 소스**다. 회계기간과
공시 접수일이 둘 다 응답에 들어 있다.

    valid_from  = 회계기간 종료일   2024 1분기 → 2024-03-31
    observed_at = 공시 접수일       2024-05-16

**둘의 간격이 한 달 반이다.** 혼동하면 3월 31일에 5월 실적을 아는 백테스트가
된다 — data-contract.md §4 가 경고하는 함정 그대로다. 남이 계산해 준 PER/PBR 을
받아 쓰면 그 값이 어느 시점 재무로 계산됐는지 알 수 없어 이 함정을 피할 방법이
없다. 그래서 재무를 직접 받아 Analyst 가 계산한다.

## 접수일은 rcept_no 에 박혀 있다

``20240516001421`` 의 앞 8자리가 접수일이다. 공시 목록 API 를 따로 부를 필요가
없다. 다만 **시각은 모른다** — DART 는 그날 접수됐다는 것만 알려준다. 그래서
그날 장 마감 이후(설정값)를 관측시각으로 쓴다. 보수적이지만 거짓이 아니다.

## 다중회사 조회

``fnlttMultiAcnt`` 는 한 콜에 **최대 100개 회사**를 받는다 (실측: 200개는
``status 021``). 종목당 개별 호출이면 5년치가 58,000콜(일 한도 20,000이라
3일)인데, 배치로는 28×5×4 = **560콜**이다.
"""

from __future__ import annotations

import io
import os
import time
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import httpx

from quant_rl_trading.collectors.errors import CollectorError
from quant_rl_trading.collectors.publication import NotYetPublished

BASE_URL = "https://opendart.fss.or.kr/api"
SOURCE = "dart"

#: 한 콜에 넣을 수 있는 회사 수. 실측으로 확인했다 — 200이면 status 021.
MAX_CORPS_PER_CALL = 100

#: 분기 → 보고서 코드.
REPORT_CODES = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}

#: 분기 종료일(월, 일). valid_from 이 된다.
QUARTER_END = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}

#: 공시목록 한 페이지 최대. DART 상한이다.
FILINGS_PAGE_SIZE = 100

#: 연결(CFS)을 우선한다. 없는 회사만 별도(OFS)를 쓴다 — 지주회사·금융사에서
#: 둘을 섞으면 같은 지표가 회사마다 다른 의미가 된다.
FS_PREFERENCE = ("CFS", "OFS")

#: 계정명 → 우리 지표 이름. 한글 계정명을 코드 전체에 퍼뜨리지 않는다.
ACCOUNTS = {
    "자산총계": "total_assets",
    "부채총계": "total_liabilities",
    "자본총계": "total_equity",
    "매출액": "revenue",
    "영업이익": "operating_income",
    "당기순이익(손실)": "net_income",
    "유동자산": "current_assets",
    "유동부채": "current_liabilities",
}


class DartUnavailable(CollectorError):
    """DART 가 데이터를 주지 않았다."""


#: 정상 응답이 아닌 상태코드 중 **데이터 없음**은 실패가 아니다.
#: 013 = 조회된 데이터 없음, 021 = 조회 가능 회사 수 초과.
NO_DATA = "013"


def _number(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    text = str(value).replace(",", "").strip()
    # DART 는 음수를 괄호로 준다: (1,234)
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        return float(text)
    except ValueError:
        return None


def receipt_date(rcept_no: str) -> date | None:
    """``20240516001421`` → 2024-05-16. 이것이 관측시각의 근거다."""
    stamp = str(rcept_no or "")[:8]
    if len(stamp) != 8 or not stamp.isdigit():
        return None
    try:
        return datetime.strptime(stamp, "%Y%m%d").date()
    except ValueError:
        return None


def quarter_end(year: int, quarter: int) -> datetime:
    month, day = QUARTER_END[quarter]
    return datetime(year, month, day, tzinfo=UTC)


def batched(items: list[str], size: int = MAX_CORPS_PER_CALL) -> Iterator[list[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


@dataclass
class DartSource:
    """OpenDART 클라이언트. 레포에서 DART 를 부르는 유일한 곳."""

    api_key: str = ""
    name: str = SOURCE
    timeout: float = 60.0
    retries: int = 3
    retry_pause_sec: float = 1.5
    #: 페이지를 넘길 때의 예의. 하루치가 여러 페이지인 날에만 쓴다.
    page_pause_sec: float = 0.15
    sleep: Callable[[float], None] = time.sleep
    transport: httpx.BaseTransport | None = None
    _client: httpx.Client | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not self.api_key:
            self.api_key = os.environ.get("OPENDART_API_KEY", "").strip()

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(transport=self.transport, timeout=self.timeout)
        return self._client

    def _call(self, path: str, params: dict[str, Any]) -> httpx.Response:
        """외부 세계와의 경계. 여기서만 넓게 잡는다.

        경계 밖에서 넓게 잡으면 우리 버그가 '수집 실패' 로 위장된다.
        """
        if not self.api_key:
            raise DartUnavailable("OPENDART_API_KEY 가 없다")

        last: Exception | None = None
        for attempt in range(self.retries):
            try:
                return self._http().get(
                    f"{BASE_URL}{path}", params={"crtfc_key": self.api_key, **params}
                )
            except Exception as error:
                last = error
                if attempt + 1 < self.retries:
                    self.sleep(self.retry_pause_sec * (attempt + 1))
        raise DartUnavailable(f"DART {path} 실패 ({type(last).__name__}: {last})") from last

    # -- 종목코드 매핑 ---------------------------------------------------------

    def corp_codes(self) -> dict[str, str]:
        """``종목코드 -> corp_code``. 상장사만.

        DART 는 이 매핑을 zip 안의 XML 로만 준다. 하루 한 번이면 충분하다.
        """
        response = self._call("/corpCode.xml", {})
        if response.status_code != 200:
            raise DartUnavailable(f"corpCode.xml HTTP {response.status_code}")
        try:
            archive = zipfile.ZipFile(io.BytesIO(response.content))
            root = ET.fromstring(archive.read(archive.namelist()[0]))
        except (zipfile.BadZipFile, ET.ParseError, IndexError) as error:
            raise DartUnavailable(f"corpCode.xml 파싱 실패: {error}") from error

        mapping: dict[str, str] = {}
        for node in root.iter("list"):
            stock = (node.findtext("stock_code") or "").strip()
            corp = (node.findtext("corp_code") or "").strip()
            if stock and corp:
                mapping[stock] = corp
        if not mapping:
            raise DartUnavailable("corpCode.xml 에 상장사가 없다")
        return mapping

    # -- 재무 ------------------------------------------------------------------

    def financials(
        self, corp_codes: list[str], *, year: int, quarter: int
    ) -> list[dict[str, Any]]:
        """회사 배치 하나의 주요계정. 최대 100개.

        데이터 없음(013)은 **실패가 아니다.** 그 분기에 아직 공시하지 않았거나
        상장 전인 회사가 섞여 있는 것뿐이다. 빈 목록을 돌려준다.
        """
        if len(corp_codes) > MAX_CORPS_PER_CALL:
            raise ValueError(f"한 콜에 {MAX_CORPS_PER_CALL}개까지다 (요청 {len(corp_codes)})")

        response = self._call(
            "/fnlttMultiAcnt.json",
            {
                "corp_code": ",".join(corp_codes),
                "bsns_year": str(year),
                "reprt_code": REPORT_CODES[quarter],
            },
        )
        if response.status_code != 200:
            raise DartUnavailable(f"fnlttMultiAcnt HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as error:
            raise DartUnavailable(f"fnlttMultiAcnt JSON 파싱 실패: {error}") from error

        status = str(payload.get("status"))
        if status == NO_DATA:
            return []
        if status != "000":
            raise DartUnavailable(
                f"fnlttMultiAcnt status={status} msg={payload.get('message')}"
            )
        return list(payload.get("list") or [])

    # -- 공시 목록 -------------------------------------------------------------

    def filings(
        self, *, day: date, corp_class: str, max_pages: int = 20
    ) -> list[dict[str, Any]]:
        """하루치 공시 목록. 페이지를 끝까지 넘긴다.

        데이터 없음(013)은 **실패가 아니다.** 휴일이거나 그 시장에 그날 접수된
        공시가 없는 것뿐이다.

        ``max_pages`` 는 안전장치다 — 응답의 ``total_page`` 가 이상하게 커져도
        무한히 돌지 않는다. 2,000건이면 그날 전 시장 공시를 덮는다.
        """
        stamp = day.strftime("%Y%m%d")
        collected: list[dict[str, Any]] = []
        page = 1

        while page <= max_pages:
            response = self._call(
                "/list.json",
                {
                    "bgn_de": stamp,
                    "end_de": stamp,
                    "corp_cls": corp_class,
                    "page_no": str(page),
                    "page_count": str(FILINGS_PAGE_SIZE),
                },
            )
            if response.status_code != 200:
                raise DartUnavailable(f"list.json HTTP {response.status_code}")
            try:
                payload = response.json()
            except ValueError as error:
                raise DartUnavailable(f"list.json JSON 파싱 실패: {error}") from error

            status = str(payload.get("status"))
            if status == NO_DATA:
                break
            if status != "000":
                raise DartUnavailable(
                    f"list.json status={status} msg={payload.get('message')}"
                )

            collected.extend(payload.get("list") or [])
            if page >= int(payload.get("total_page") or 1):
                break
            page += 1
            self.sleep(self.page_pause_sec)

        return collected

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None


def normalize_financials(
    rows: list[dict[str, Any]],
    *,
    market: str,
    year: int,
    quarter: int,
    observed_at_for: Callable[[date], datetime],
) -> list[dict[str, Any]]:
    """DART 응답 → fundamentals 행 (long 포맷).

    지표 하나가 행 하나다. 계정이 늘어도 스키마를 안 고쳐도 되고, 어떤 지표가
    언제부터 공시됐는지가 데이터에 남는다.

    같은 회사에 연결(CFS)과 별도(OFS)가 함께 오면 **연결을 고른다.** 섞으면
    같은 지표가 회사마다 다른 의미가 된다.
    """
    valid_from = quarter_end(year, quarter)
    period = f"{year}Q{quarter}"

    # (종목, 지표) → (우선순위, 행). 낮은 우선순위가 이긴다.
    chosen: dict[tuple[str, str], tuple[int, dict[str, Any]]] = {}

    for row in rows:
        stock = str(row.get("stock_code") or "").strip()
        metric = ACCOUNTS.get(str(row.get("account_nm") or "").strip())
        if not stock or metric is None:
            continue

        value = _number(row.get("thstrm_amount"))
        if value is None:
            continue

        received = receipt_date(str(row.get("rcept_no") or ""))
        if received is None:
            # 접수일을 모르면 언제 알 수 있었는지 모른다. 저장하지 않는다.
            continue

        fs_div = str(row.get("fs_div") or "CFS")
        rank = FS_PREFERENCE.index(fs_div) if fs_div in FS_PREFERENCE else len(FS_PREFERENCE)

        key = (stock, metric)
        if key in chosen and chosen[key][0] <= rank:
            continue

        chosen[key] = (
            rank,
            {
                "entity_id": f"{market}:{stock}",
                "valid_from": valid_from,
                "observed_at": observed_at_for(received),
                "source": SOURCE,
                "market": market,
                "metric": metric,
                "value": value,
                "fiscal_period": period,
                "report_type": f"dart_{fs_div.lower()}",
            },
        )

    return sorted(
        (row for _, row in chosen.values()),
        key=lambda item: (str(item["entity_id"]), str(item["metric"])),
    )


@dataclass(frozen=True)
class FilingPolicy:
    """공시 접수일 → 관측시각.

    DART 는 **날짜만** 준다. 시각을 모르므로 그날 늦게 알게 됐다고 본다 —
    접수 마감이 18시라 그 이후로 잡으면 절대 낙관적이지 않다. 자정으로 잡으면
    그날 아침부터 실적을 알고 있던 것이 되어 하루를 앞당겨 본다.
    """

    hour_kst: int
    clock: Any

    def for_filing(self, received: date) -> datetime:
        from zoneinfo import ZoneInfo

        local = datetime(
            received.year, received.month, received.day,
            self.hour_kst, tzinfo=ZoneInfo("Asia/Seoul"),
        )
        moment = local.astimezone(UTC)
        now = self.clock.now()
        if moment > now:
            raise NotYetPublished(
                f"공시 {received.isoformat()} 는 {moment.isoformat()} 에 알 수 있다. "
                f"현재 {now.isoformat()}"
            )
        return moment


FUNDAMENTALS = "fundamentals"


def dart_run_id(market: str, year: int, quarter: int, batch: int) -> str:
    """재개 단위는 (연도, 분기, 배치) 다. 결정론적이라 이어받기가 정확하다."""
    return f"bf-dart-{market}-{year}Q{quarter}-b{batch:02d}"


@dataclass(frozen=True)
class DartResult:
    year: int
    quarter: int
    batch: int
    rows: int
    skipped: bool
    error: str | None = None
    #: 아직 공표되지 않은 공시가 섞여 있다. **실패가 아니라 "내일 다시"** 다.
    #: 매니페스트를 남기지 않으므로 다음 실행이 이어받는다.
    deferred: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def counts(self) -> dict[str, int]:
        return {FUNDAMENTALS: self.rows}

    @property
    def unit(self) -> str:
        return f"{self.year}Q{self.quarter}-b{self.batch:02d}"


@dataclass
class DartBackfiller:
    """분기 × 배치 단위로 재무를 채운다."""

    store: Any
    source: DartSource
    clock: Any
    archive: Any
    policy: FilingPolicy
    market: str = "KR"

    def pending(self, plan: list[tuple[int, int, int]]) -> list[tuple[int, int, int]]:
        return [
            item
            for item in plan
            if not self.store.ingest_run_recorded(
                FUNDAMENTALS, dart_run_id(self.market, *item)
            )
        ]

    def run_batch(
        self, corp_codes: list[str], *, year: int, quarter: int, batch: int
    ) -> DartResult:
        run_id = dart_run_id(self.market, year, quarter, batch)
        if self.store.ingest_run_recorded(FUNDAMENTALS, run_id):
            return DartResult(year, quarter, batch, 0, skipped=True)

        try:
            raw = self.source.financials(corp_codes, year=year, quarter=quarter)
        except CollectorError as error:
            return DartResult(year, quarter, batch, 0, skipped=False, error=str(error))

        if not raw:
            # 그 분기에 아직 공시하지 않은 배치. 빈 것을 완료로 기록하면
            # 나중에 공시가 올라와도 영영 건너뛴다.
            return DartResult(year, quarter, batch, 0, skipped=False)

        self.archive.save(
            self.source.name, raw,
            observed_at=self.clock.now(), ingest_run_id=run_id,
            label=f"dart-{year}Q{quarter}-b{batch:02d}",
        )
        try:
            rows = normalize_financials(
                raw, market=self.market, year=year, quarter=quarter,
                observed_at_for=self.policy.for_filing,
            )
        except NotYetPublished as pending:
            # 배치에 아직 알 수 없는 공시가 섞여 있다. 일부만 저장하면 나머지를
            # 영영 못 받으므로(매니페스트가 남는다) 배치째 미룬다.
            return DartResult(
                year, quarter, batch, 0, skipped=False,
                error=str(pending), deferred=True,
            )
        if not rows:
            return DartResult(year, quarter, batch, 0, skipped=False)
        return DartResult(
            year, quarter, batch,
            self.store.append(FUNDAMENTALS, rows, ingest_run_id=run_id),
            skipped=False,
        )
