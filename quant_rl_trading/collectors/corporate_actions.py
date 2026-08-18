"""기업행위 조정계수 — `prices.adj_factor` 를 채우는 곳.

## 왜 필요한가

창고에는 **원주가**가 들어 있다(``market_collector`` 모듈 독스트링). 수정주가에
미래의 분할이 섞이는 것을 막으려는 옳은 선택이었지만, 짝이 되어야 할
조정계수를 아무도 채우지 않아 ``adj_factor`` 가 전 행 ``None`` 이었다.

그 결과 액면분할·무상증자·감자·주식병합이 **가격 급변으로 남는다.** 실제
손실이 아닌데 수익률이 그렇게 계산되고, 모멘텀 창이 250일이면 사건 하나가
그 뒤 250세션을 오염시킨다.

규모(창고 ``documents``, 2021-08~2026-08 실측):

    권리락        480건 · 368종목      감자      1,069건 · 294종목
    무상증자결정   675건 · 327종목      주식병합    561건 · 319종목
    주식분할결정   147건 ·  83종목      액면분할      47건 ·  40종목

중복을 걷어내도 유니버스의 4분의 1이 닿는다.

**급락을 훑는 방식으로는 못 찾는다.** 5% 무상증자는 가격이 5% 내려갈 뿐이라
어떤 급락 필터에도 안 걸린다. 반대로 급락은 대부분 기업행위가 아니다 —
2026년 국장 급락(-60% 미만) 60건을 전수 조회했더니 진짜 기업행위는 21종목,
나머지는 상장폐지(27) 와 거래정지 후 정리매매(5) 였다. **60건 중 50건이
직전일 거래량 0** 이었다.

## 소스 — LS 가 계수를 직접 준다 (실측 2026-08-15)

``t8410``(국장) · ``g3204``(미장) 의 ``sujung`` 을 ``"Y"``/``"N"`` 두 번 불러
나눈 값이 곧 누적 조정계수다.

    삼성전자 2018-05-04 액면분할   0.020000  (= 1/50)
    NVDA     2024-06-10 10:1 분할  0.100000
    AAPL     2020-08-31  4:1 분할  0.250000

감자·병합은 계수가 1보다 크게 나온다(실측 KR:025560 2025-07-22 ×17.4).

**국장 t8410 의 경로는 ``/stock/chart`` 다.** ``/stock/market-data`` 로 부르면
HTTP 500 ``IGW00215``(유효하지 않은 TR CD) 가 온다 — ``ls_client.PATH_CHART``
참고.

## 무엇을 저장하나 — 누적이 아니라 **그 세션의 배율**

``adj_factor`` 는 **그 세션에 발효된 기업행위의 가격 배율** 하나다. 사건이
없는 날은 ``None`` 이다.

    f(D) = ratio(D-1) / ratio(D)        ratio(t) = LS_수정주가(t) / LS_원주가(t)

**누적계수를 저장하면 안 된다.** 누적은 "오늘" 기준이라 저장하는 순간 as_of
마다 틀린다 — 오늘 계산한 0.02 를 2017년 행에 적어 두면 그 행이 미래를 담게
된다. 사건 단위로 저장하고 **읽을 때 접는다**(``store.prices.adjust``).

## 두 시각

| 필드 | 값 |
|---|---|
| ``valid_from`` | 발효일 D (권리락일·변경상장일) |
| ``observed_at`` | **그 세션 원본 행의 관측시각 그대로** |

조정 기준가는 **발효일 아침에 거래소가 공표한다.** 그날 종가를 알 수 있었던
시각이면 계수도 알 수 있었다 — 원본 행의 관측시각을 그대로 쓰는 것이 가장
보수적이면서 정직하다. 정책을 새로 만들지 않으므로 두 벌이 어긋날 일도 없다.

**분할을 알기 전 조회가 보정값을 못 보는 것은 구조가 보장한다.** 사건 행의
``observed_at`` 이 D 이므로 ``as_of < D`` 인 조회에는 애초에 안 들어온다.
읽는 쪽이 조심할 필요가 없다.

공시일(권리락 공시 접수일, 보통 D-1)을 쓰지 않는 이유: 공시는 "곧 조정된다"
만 말하고 배율의 확정값은 발효일 기준가가 정한다.

## 왜 정정본(revision+1)인가

창고는 append-only 라 기존 행을 못 고친다. 정정본으로 얹는다.

같은 창고의 다른 두 선례와 갈리는 지점이 여기다.

    종가 0 세션      소스가 지금도 0 을 준다 — **받아올 값이 없어서** 견뎠다
    us_shares 미래날짜  재수집해도 같은 값이 온다 — **소스가 틀려서** 막았다
    조정계수         **소스가 옳은 값을 준다.** 견딜 이유가 없다

행 수도 작다. 사건 하나당 한 행이고 5년 국장 전체가 수천 행이다.

**정정본은 행 전체를 다시 쓴다.** 게이트가 자연키마다 최신 revision **하나**
를 고르므로(``reader.query`` 의 ``ORDER BY revision DESC``), 값 컬럼을 비워
두면 그 행의 시세가 통째로 null 이 된다. 원본을 통째로 복사한 뒤 ``adj_factor``
하나만 얹는 이유다.
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from statistics import median
from typing import Any

from quant_rl_trading.collectors.errors import CollectorError
from quant_rl_trading.collectors.ls_client import PATH_CHART, LSClient
from quant_rl_trading.collectors.market_hours import Market

#: 국장 일봉 TR. 경로는 ``PATH_CHART`` 다.
TR_CHART_KR = "t8410"

#: 미장 일봉 TR. 경로는 ``/overseas-stock/chart`` (``ls_us_source.PATH_CHART``).
TR_CHART_US = "g3204"
PATH_CHART_US = "/overseas-stock/chart"

#: 미장 종목 단건. 거래소 코드를 알아내는 데만 쓴다.
TR_MASTER_US = "g3101"
PATH_MARKET_US = "/overseas-stock/market-data"

#: 미장 거래소 코드. ``ls_us_source`` 와 같은 값이다.
NYSE = "81"
NASDAQ = "82"

#: 한 콜의 실측 상한. ``qrycnt`` 를 1500·3000 으로 줘도 501행에서 끊긴다
#: (실측 2026-08-15, 005930). 그래서 ``edate`` 를 뒤로 밀며 페이지를 넘긴다.
MAX_ROWS_PER_CALL = 500

#: 원주가 / 수정주가.
SUJUNG_RAW = "N"
SUJUNG_ADJUSTED = "Y"

#: 사건 판정 문턱의 설정 이름. **하드코딩하지 않는다** (불변식 10).
MIN_LOG_FACTOR_CONFIG = "corporate_action.min_log_factor"

#: 페이지를 넘기다 같은 자리를 맴돌면 멈춘다. 응답이 요청 구간 밖 날짜를
#: 주는 일이 실제로 있어서(ls_us_source 의 같은 방어) 무한루프가 가능하다.
MAX_PAGES = 40


class AdjustmentUnavailable(CollectorError):
    """조정계수를 만들 수 없다. 상장폐지·코드소멸이면 정상적으로 일어난다."""


@dataclass(frozen=True)
class CorporateAction:
    """발효일 하나의 가격 배율.

    ``factor < 1`` 이면 분할·무상증자(가격이 내려간다), ``> 1`` 이면 감자·
    병합이다.
    """

    entity_id: str
    effective_on: date
    factor: float
    #: 계단 양쪽 평탄부의 대표값. 진단용으로 남긴다 — 계수가 이상할 때
    #: 잡음인지 진짜인지 이 둘로 가른다.
    ratio_before: float
    ratio_after: float

    @property
    def log_factor(self) -> float:
        return math.log(self.factor)


def _to_float(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _to_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
    except ValueError:
        return None


# -----------------------------------------------------------------------------
# 소스
# -----------------------------------------------------------------------------


@dataclass
class LSAdjustmentSource:
    """LS 에서 원주가·수정주가를 받아 비율을 낸다. 이 파일의 유일한 외부 경계."""

    client: LSClient
    market: Market = Market.KR

    @property
    def _path(self) -> str:
        return PATH_CHART if self.market is Market.KR else PATH_CHART_US

    @property
    def _tr(self) -> str:
        return TR_CHART_KR if self.market is Market.KR else TR_CHART_US

    def _body(
        self, symbol: str, *, sujung: str, start: date, end: date, exchange: str
    ) -> dict[str, Any]:
        tr = self._tr
        if self.market is Market.KR:
            block = {
                "shcode": symbol.lstrip("A"),
                "gubun": "2",
                "qrycnt": MAX_ROWS_PER_CALL,
                "sdate": start.strftime("%Y%m%d"),
                "edate": end.strftime("%Y%m%d"),
                "cts_date": "",
                "comp_yn": "N",
                "sujung": sujung,
            }
        else:
            block = {
                "sujung": sujung,
                "delaygb": "R",
                "comp_yn": "N",
                "keysymbol": f"{exchange}{symbol}",
                "exchcd": exchange,
                "symbol": symbol,
                "gubun": "2",
                "qrycnt": MAX_ROWS_PER_CALL,
                "sdate": start.strftime("%Y%m%d"),
                "edate": end.strftime("%Y%m%d"),
                "cts_date": "",
                "cts_info": "",
            }
        return {f"{tr}InBlock": block}

    def closes(
        self,
        symbol: str,
        *,
        start: date,
        end: date,
        sujung: str,
        exchange: str = "",
    ) -> dict[date, float]:
        """종가만 뽑는다. ``edate`` 를 뒤로 밀며 페이지를 넘긴다.

        ``cts_date`` 를 쓰지 않는 이유: ``qrycnt`` 를 키우면 응답이 501행에서
        끊기면서 ``cts_date`` 를 빈 문자열로 주는 경우가 있었다(실측). 날짜
        커서는 응답 내용만으로 결정되므로 그런 응답에도 흔들리지 않는다.
        """
        collected: dict[date, float] = {}
        cursor = end
        for _ in range(MAX_PAGES):
            payload = self.client.request_tr(
                self._path, self._tr, self._body(
                    symbol, sujung=sujung, start=start, end=cursor, exchange=exchange
                )
            )
            rows = payload.get(f"{self._tr}OutBlock1") or []
            page: dict[date, float] = {}
            for row in rows:
                day = _to_date(row.get("date"))
                close = _to_float(row.get("close"))
                # 요청 구간 밖의 행이 섞여 오면 버린다. 넘겨받은 구간만이 이
                # 호출이 책임지는 범위다 (ls_us_source 와 같은 방어).
                if day is None or close is None or not (start <= day <= cursor):
                    continue
                page[day] = close
            if not page:
                break
            collected.update(page)
            oldest = min(page)
            if oldest <= start:
                break
            nxt = oldest - timedelta(days=1)
            if nxt >= cursor:
                break
            cursor = nxt
        return collected

    def exchange_of(self, symbol: str) -> str:
        """미장 심볼의 거래소 코드. 국장은 빈 문자열이다.

        차트 TR 이 ``exchcd`` 를 요구하는데 우리 창고는 그 값을 안 들고 있다.
        나스닥을 먼저 본다 — 후보가 더 많아 평균 호출이 줄어든다
        (``ls_us_source.resolve_exchange`` 와 같은 규칙).

        **심볼 하나당 최대 2콜이 얹힌다.** 미장 스캔 비용의 대부분이 여기서
        온다 — 국장에 없는 단계다.
        """
        if self.market is Market.KR:
            return ""
        for exchange in (NASDAQ, NYSE):
            try:
                payload = self.client.request_tr(
                    PATH_MARKET_US,
                    TR_MASTER_US,
                    {
                        f"{TR_MASTER_US}InBlock": {
                            "keysymbol": f"{exchange}{symbol}",
                            "exchcd": exchange,
                            "symbol": symbol,
                        }
                    },
                )
            except CollectorError:
                continue
            if payload.get(f"{TR_MASTER_US}OutBlock"):
                return exchange
        raise AdjustmentUnavailable(f"{symbol}: 나스닥·뉴욕 어디에도 없다")

    def ratios(
        self, symbol: str, *, start: date, end: date, exchange: str = ""
    ) -> dict[date, float]:
        """``수정주가 / 원주가``. 두 계열에 다 있는 날만 남긴다.

        0 이하인 종가는 버린다. 국장 창고에는 전 종목 종가 0 인 휴장일 세션이
        있고(``store/prices.py``), 그 날을 나누면 ZeroDivision 이거나 무의미한
        비율이 된다.
        """
        raw = self.closes(symbol, start=start, end=end, sujung=SUJUNG_RAW, exchange=exchange)
        if not raw:
            raise AdjustmentUnavailable(f"{symbol}: 원주가 0행 (상폐·코드소멸 추정)")
        adjusted = self.closes(
            symbol, start=start, end=end, sujung=SUJUNG_ADJUSTED, exchange=exchange
        )
        return {
            day: adjusted[day] / value
            for day, value in raw.items()
            if day in adjusted and value > 0 and adjusted[day] > 0
        }


# -----------------------------------------------------------------------------
# 사건 검출
# -----------------------------------------------------------------------------


def _plateaus(
    days: Sequence[date], ratios: Mapping[date, float], *, min_log_factor: float
) -> Iterator[tuple[int, int]]:
    """계단 사이의 평탄 구간 ``[시작, 끝)`` 을 낸다."""
    start = 0
    for index in range(1, len(days)):
        before = ratios[days[index - 1]]
        after = ratios[days[index]]
        if abs(math.log(after / before)) > min_log_factor:
            yield start, index
            start = index
    yield start, len(days)


def detect_events(
    ratios: Mapping[date, float],
    *,
    entity_id: str,
    min_log_factor: float,
) -> list[CorporateAction]:
    """비율 계열의 계단을 사건으로 바꾼다.

    **평탄부의 중앙값끼리 나눈다.** 이웃한 두 점을 그냥 나누면 안 된다 — KRX
    가격이 정수라 비율이 ±0.05% 씩 흔들리고(실측: 2.31520~2.31664 사이를
    100번 넘게 오간다), 그 잡음이 그대로 계수 오차가 된다. 중앙값은 그
    흔들림을 통째로 지운다.

    ``min_log_factor`` 는 그 잡음보다 크고 가장 작은 진짜 사건(5% 무상증자,
    |log| ≈ 0.049)보다 작아야 한다. 설정에서 읽는다 (불변식 10).
    """
    days = sorted(ratios)
    if len(days) < 2:
        return []

    blocks = list(_plateaus(days, ratios, min_log_factor=min_log_factor))
    events: list[CorporateAction] = []
    for previous, current in pairwise(blocks):
        before = median(ratios[day] for day in days[previous[0] : previous[1]])
        after = median(ratios[day] for day in days[current[0] : current[1]])
        if before <= 0 or after <= 0:
            continue
        events.append(
            CorporateAction(
                entity_id=entity_id,
                # 계단의 오른쪽 첫 세션이 발효일이다. 그날부터 새 비율이 선다.
                effective_on=days[current[0]],
                factor=before / after,
                ratio_before=before,
                ratio_after=after,
            )
        )
    return events


# -----------------------------------------------------------------------------
# 정규화
# -----------------------------------------------------------------------------

#: 정정본이 다시 써야 하는 값 컬럼. 하나라도 빠뜨리면 그 세션 시세가 null 이 된다.
PRICE_VALUE_COLUMNS = ("market", "open", "high", "low", "close", "volume", "value")


def revision_rows(
    actions: Sequence[CorporateAction],
    existing: Mapping[tuple[str, date], Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """사건 → ``prices`` 정정본 행.

    ``existing`` 은 그 (종목, 발효일) 의 **현재 창고 행**이다. 값 컬럼과
    ``observed_at``·``source`` 를 그대로 물려받고 ``adj_factor`` 하나만 얹는다.
    창고에 그 세션 행이 없으면 건너뛴다 — 없는 세션에 정정본을 얹으면 시세
    없는 행이 생긴다.
    """
    rows: list[dict[str, Any]] = []
    for action in actions:
        source_row = existing.get((action.entity_id, action.effective_on))
        if source_row is None:
            continue
        row: dict[str, Any] = {
            "entity_id": action.entity_id,
            "valid_from": _session_timestamp(action.effective_on),
            "observed_at": source_row["observed_at"],
            "source": source_row["source"],
            "revision": int(source_row["revision"]) + 1,
            "adj_factor": float(action.factor),
        }
        for column in PRICE_VALUE_COLUMNS:
            value = source_row.get(column)
            row[column] = None if value is None else value
        rows.append(row)
    return rows


def _session_timestamp(day: date) -> datetime:
    """거래일 → ``valid_from``. 저장은 UTC 자정으로 통일한다."""
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def run_id(market: Market, batch: int, *, stamp: str = "") -> str:
    """결정론적 run id. 작업 단위는 종목이 아니라 **배치**다.

    종목 축으로 append 하면 파티션마다 파일이 하나씩 생겨 창고가 마비된다 —
    미장 백필이 그렇게 파일 247만 개를 만들었다 (``ls_us_source.us_run_id``).

    ## ``stamp`` 가 없으면 매일이 같은 id 가 된다

    전에는 배치 번호뿐이라 언제 돌려도 ``adjfactor-KR-0000`` 이었다. 그래서
    **첫 적재 이후 매일의 기업행위가 영원히 건너뛰어졌다** — 2026-08-17 에
    5년치 1,123건을 넣은 뒤로 일일 스캔이 매일 이렇게 끝나고 있었다:

        기업행위 스캔 rc=0
        adjfactor-KR-0000 는 이미 적재됐다 — 건너뛴다
        기업행위 적재 rc=0

    스캔은 매일 돌고 rc=0 이라 아무도 몰랐다. 새 액면분할이 나도 창고에
    계수가 안 들어가고, 그 종목은 다음 전체 백필까지 -50% 로 남는다.

    일일 경로는 그날 날짜를 ``stamp`` 로 준다. 5년치 백필은 안 준다 — 그건
    한 번만 도는 작업이고, 날짜를 넣으면 재실행이 중복을 못 막는다.
    """
    suffix = f"-{stamp}" if stamp else ""
    return f"adjfactor-{market}{suffix}-{batch:04d}"
