"""N6 — 금융위원회 공공데이터(data.go.kr) 주식대차·의무보호예수 반환 (2026-10-14, 사용자 승인 N6).

## 원천 (실측 2026-10-14, 키 `DATA_GO_KR_SERVICE_KEY` — 값은 코드·로그에 남기지 않는다)

| 서비스 | 경로 | 이력 | 하루 행 |
|---|---|---|---|
| 주식대차정보(15059612) | ``GetStocLendBorrInfoService_V2/getStItemLendAndBorrStatu_V2`` | **2008-01-02~** (전체 820만 행) | 약 2,400 종목 |
| 주식발행정보(15043423) | ``GetStocIssuInfoService_V3/getLockUpRetuInfo_V3`` | **2010-07-15~** | 수 건~수십 건 |

- 대차: 종목별 일별 **대차 체결 주식수(lnbCclStckCnt) · 상환(lnbRdptStckCnt) · 잔고(lnbRmanStckCnt)**. 금액 없음. ISIN 키(KR7 + 단축코드 6 + …).
- 보호예수 반환: 반환일(rsrnDt=basDt) · 반환 주식수 · 반환 뒤 잔여 · 등록일 · 사유(최대주주·모집 전매제한 등) · 의무보호 총발행수.
- 기간 조회 인자(beginBasDt/endBasDt)는 **무시된다**(실측: 범위를 줘도 전체 건수) — 날짜(``basDt``) 하나씩 부른다.
- 한도: 개발계정 일 10,000 콜(오류 22 일일 초과 · 23 초당 초과). 백필 = 거래일 수 × 1~3 콜.
- 공공누리 2유형 — **출처표시·상업적 이용 금지**. 상업 이용은 예탁결제원 정보이용계약(portal@ksd.or.kr). 내부 연구 전용.

## 두 시각 (data-contract.md §4-4)

- ``valid_from`` = 기준일(basDt) UTC 자정 — 다른 KRX 패널과 같은 칸.
- ``observed_at`` = **기준일 다음 거래일 13:00 KST**. 포털이 "기준일자 다음 영업일 오후 1시 이후 업데이트" 를 명시한다. 그날 16:00 결정
  세션은 전날 기준일 값을 본다 → 피처는 하루 늦춤이 자동으로 된다. 라이브 수집은 실제 받은 시각을 쓴다(호출부가 ``observed_at`` 을 넘긴다).
- **과거 빈티지가 없다** — 원천이 덮어쓰기라 백필 값은 "지금 보이는 과거" 다. 정정 이력은 모른다(한계로 적는다).
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from quant_rl_trading.collectors.errors import CollectorError
from quant_rl_trading.collectors.market_hours import Market, trading_days

BASE_URL = "https://apis.data.go.kr/1160100"
PATH_LENDING = "GetStocLendBorrInfoService_V2/getStItemLendAndBorrStatu_V2"
PATH_LOCKUP = "GetStocIssuInfoService_V3/getLockUpRetuInfo_V3"
ENV_KEY = "DATA_GO_KR_SERVICE_KEY"
TABLE_LENDING = "stock_lending"
TABLE_LOCKUP = "lockup_returns"
SOURCE = "data.go.kr-fsc"
PAGE_ROWS = 1000
#: 공표 시각 — 다음 거래일 13:00 KST(포털 명시).
PUBLISH_HOUR_KST = 13
SEOUL = ZoneInfo("Asia/Seoul")
#: 오류 코드 → 사람 말. 22·23 은 한도, 30 은 미등록 키, 20·31 은 활용 승인·기간 문제.
ERRORS = {"12": "서비스 없음(경로)", "20": "접근 거부(활용 미승인)", "22": "일일 한도 초과", "23": "초당 한도 초과",
          "30": "등록되지 않은 키", "31": "활용 기간 만료", "32": "등록되지 않은 IP"}


class QuotaExceeded(CollectorError):
    """일일·초당 한도. 백필은 멈추고 다음 날 이어 간다."""


def entity_of(isin: str) -> str | None:
    """KR7 보통·우선주 ISIN → ``KR:XXXXXX``. 외국 상장사(HK·US…)·채권은 None."""
    isin = (isin or "").strip()
    if len(isin) != 12 or not isin.startswith("KR7"):
        return None
    return f"KR:{isin[3:9]}"


def observed_at_for(day: date) -> datetime:
    """기준일 → 다음 거래일 13:00 KST(UTC 로)."""
    nxt = [d for d in trading_days(Market.KR, day + timedelta(days=1), day + timedelta(days=15)) if d > day][0]
    return datetime(nxt.year, nxt.month, nxt.day, PUBLISH_HOUR_KST, tzinfo=SEOUL).astimezone(UTC)


def _day(text: Any) -> date | None:
    t = str(text or "").strip()
    if len(t) != 8 or not t.isdigit():
        return None
    try:
        return date(int(t[:4]), int(t[4:6]), int(t[6:]))
    except ValueError:
        return None


def _num(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def _utc_midnight(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def lending_rows(items: Iterable[Mapping[str, Any]], *, observed_at: datetime | None = None) -> list[dict[str, Any]]:
    """대차 응답 → ``stock_lending`` 행. KR7 이 아닌 종목은 버린다. ``observed_at`` 이 없으면 공표 규칙(다음 거래일 13:00)."""
    rows = []
    for item in items:
        entity, day = entity_of(item.get("isinCd", "")), _day(item.get("basDt"))
        if entity is None or day is None:
            continue
        rows.append({"entity_id": entity, "valid_from": _utc_midnight(day),
                     "observed_at": observed_at or observed_at_for(day), "source": SOURCE, "market": "KR",
                     "isin": str(item.get("isinCd")), "name": str(item.get("isinCdNm") or ""),
                     "lend_new": _num(item.get("lnbCclStckCnt")), "lend_repaid": _num(item.get("lnbRdptStckCnt")),
                     "lend_balance": _num(item.get("lnbRmanStckCnt"))})
    return rows


def lockup_rows(items: Iterable[Mapping[str, Any]], *, observed_at: datetime | None = None) -> list[dict[str, Any]]:
    """보호예수 반환 응답 → ``lockup_returns`` 행. 한 종목·한 날에 사유·등록일별로 여러 행."""
    rows = []
    for item in items:
        entity, day = entity_of(item.get("isinCd", "")), _day(item.get("basDt") or item.get("rsrnDt"))
        if entity is None or day is None:
            continue
        reg = _day(item.get("lkupRegDt"))
        rows.append({"entity_id": entity, "valid_from": _utc_midnight(day),
                     "observed_at": observed_at or observed_at_for(day), "source": SOURCE, "market": "KR",
                     "isin": str(item.get("isinCd")), "lockup_reg_date": reg.isoformat() if reg else "",
                     "reason_code": str(item.get("stckLblHoldRcd") or ""), "reason_name": str(item.get("stckLblHoldRcdNm") or ""),
                     "listing_code": str(item.get("lstgDcd") or ""),
                     "registered_shares": _num(item.get("lkupRegStckCnt")), "returned_shares": _num(item.get("rsrnStckCnt")),
                     "remaining_shares": _num(item.get("afrsRsqtCnt")),
                     "protected_total_shares": _num(item.get("lblProtTsumIssuStckCnt"))})
    return rows


@dataclass
class DataGoKrClient:
    """data.go.kr 금융위 REST(JSON). ``fetch`` 는 시험용 주입점(url → 본문)."""

    service_key: str
    min_interval_sec: float = 0.2
    fetch: Callable[[str], str] | None = None
    calls: int = 0
    _last: float = field(default=0.0, repr=False)

    def _get(self, url: str) -> str:
        if self.fetch is not None:
            return self.fetch(url)
        wait = self.min_interval_sec - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        try:
            with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 — 고정 호스트
                return response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as error:
            return error.read().decode("utf-8", "replace")
        finally:
            self._last = time.monotonic()

    def page(self, path: str, *, bas_dt: date, page_no: int = 1, rows: int = PAGE_ROWS) -> tuple[list[dict[str, Any]], int]:
        query = urllib.parse.urlencode({"serviceKey": self.service_key, "resultType": "json", "numOfRows": rows,
                                        "pageNo": page_no, "basDt": bas_dt.strftime("%Y%m%d")})
        body = self._get(f"{BASE_URL}/{path}?{query}")
        self.calls += 1
        return parse(body)

    def day(self, path: str, bas_dt: date) -> list[dict[str, Any]]:
        """그 기준일 전부(페이지를 넘긴다)."""
        items, total = self.page(path, bas_dt=bas_dt)
        page_no = 1
        while len(items) < total:
            page_no += 1
            more, _ = self.page(path, bas_dt=bas_dt, page_no=page_no)
            if not more:
                break
            items.extend(more)
        return [i for i in items if _day(i.get("basDt")) == bas_dt]


def parse(body: str) -> tuple[list[dict[str, Any]], int]:
    """응답 본문 → (항목, 전체 건수). 오류 봉투(OpenAPI_ServiceResponse)·결과코드 ≠ 00 은 예외. 키는 메시지에 안 싣는다."""
    try:
        data = json.loads(body)
    except ValueError as error:
        if "<returnReasonCode>" in body:
            code = body.split("<returnReasonCode>")[1].split("<")[0]
            raise _error(code) from error
        raise CollectorError(f"JSON 아님: {body[:120]!r}") from error
    if "OpenAPI_ServiceResponse" in data:
        raise _error(str(data["OpenAPI_ServiceResponse"].get("cmmMsgHeader", {}).get("returnReasonCode", "?")))
    response = data.get("response", {})
    code = str(response.get("header", {}).get("resultCode", "00"))
    if code not in ("00", "0"):
        raise _error(code)
    body_ = response.get("body", {})
    items = (body_.get("items") or {}).get("item") or []
    if isinstance(items, dict):
        items = [items]
    return list(items), int(body_.get("totalCount") or 0)


def _error(code: str) -> CollectorError:
    text = f"data.go.kr 오류 {code} — {ERRORS.get(code, '알 수 없음')}"
    return QuotaExceeded(text) if code in ("22", "23") else CollectorError(text)


# ----------------------------------------------------------------------------- 피처(연구·랭커 재료)


def lending_features(balance: Any, shares: Any) -> dict[str, Any]:
    """넓은 표(세션 × 종목) 둘 → 피처. **결정 t 에는 t−1 기준일 값**(공표가 다음 거래일 13:00 — t 16:00 결정이 보는 마지막 값).

    - ``lend_ratio``  = 대차잔고 / 상장주식수
    - ``dlend5``·``dlend20`` = lend_ratio 의 5·20세션 변화 — 숏 수요 증가(문헌 방향: 증가 → 부진)
    """
    ratio = (balance / shares.where(shares > 0)).shift(1)
    return {"lend_ratio": ratio, "dlend5": ratio - ratio.shift(5), "dlend20": ratio - ratio.shift(20)}


def lockup_features(returned: Any, shares: Any, *, window: int = 20) -> Any:
    """반환 주식수 넓은 표(세션 × 종목, 없는 날 0) → 최근 ``window`` 세션 반환 물량 / 상장주식수(하루 늦춤). 오버행 대용."""
    flow = returned.fillna(0.0).rolling(window, min_periods=1).sum()
    return (flow / shares.where(shares > 0)).shift(1)
