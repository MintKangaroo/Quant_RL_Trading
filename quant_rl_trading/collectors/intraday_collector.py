"""분봉 수집 — 국장 LS ``t8412``, 미장 LS ``g3203``.

대시보드 트레이딩 탭의 ``1m/5m/15m/1H/4H`` 버튼은 지금까지 전부 ``disabled``
였고 이유는 "분봉은 창고에 없다" 였다 — 거짓말이 아니라 입력이 없었다. 이
파일이 그 입력을 채운다 (``dashboard/services/trading.py`` 가 읽고,
``dashboard/api/trading.py`` 가 화면에 얹는다).

## TR 확정 — 실호출로 확인했다 (2026-08-18)

**국장은 ``t8412``** (경로 ``/stock/chart``). LS 개발자포털 카탈로그
(``GET openapi.ls-sec.co.kr/api/apis/public``, 인증 불필요)에서
``[주식] 차트`` 그룹의 TR 목록(``t8411·t8412·t8413·t1665·t8410·t4201·
t8451·t8452·t8453``)을 받고, 그 그룹의 TR 가이드(``/api/apis/guide/tr/
{api_id}``)에서 ``t8412`` 의 ``trName`` 이 "주식차트(N분)" 임을 확인했다.
005930 으로 ``ncnt`` 1·5·15·60·240 전부 실계좌 키로 직접 호출해 정상 응답
(``rsp_cd=00000``)과 실제 봉을 받았다 — 예: ``ncnt=1`` → 2026-08-18
15:20 봉, ``ncnt=240`` → 13:00 봉(4시간 창).

**미장은 ``g3202`` 가 아니라 ``g3203`` 이다.** ``ls_us_source.py:510`` 의
기존 주석이 g3202 를 "N분봉" 이라 적어 두었는데, 그 주석 자체가 틀렸다 —
카탈로그 ``trName`` 기준 g3202 는 "차트NTICK 조회"(틱봉)이고, N분봉은
**g3203**("차트NMIN 조회")이다. AAPL 로 ncnt 1·5·15·60·240 전부 실호출해
정상 응답을 받았고, ``ncnt=10`` 요청 시 ``loctime`` 이 정확히 10분 간격으로
오는 것도 실측으로 확인했다(01:10:00 → 01:20:00 → …) — 짐작이 아니다.

## 미장 봉 시각 — ``loctime`` 은 거래소 현지시간이다

실측(2026-08-18 UTC 07:24 부근 호출, AAPL): 응답 ``loctime`` 이 "03:19" 대,
``s_time``/``e_time`` 이 자정을 넘어가는 구간을 표시했다. UTC-4(미 동부
서머타임)로 역산하면 정확히 현재 UTC 와 맞아떨어진다. 그래서 ``date`` +
``loctime`` 을 **거래소 현지 시각**(``America/New_York``)으로 해석해
``zoneinfo`` 로 UTC 로 바꾼다. DST 전환은 라이브러리가 처리한다 — 손으로
오프셋을 계산하지 않는다(``market_hours.py`` 와 같은 원칙).

국장 ``t8412`` 의 ``time`` 필드는 거래소가 곧 한국이라 애초에 KST 이고,
같은 방식(``Asia/Seoul``)으로 변환한다.

## 왜 새 표(``prices_intraday``)인가

``prices``(일봉)를 읽는 코드 전부 — 회계·백테스트·``store/prices.py`` 의
0-세션 제거, 조정계수 누적 — 는 **하루에 한 행**을 전제로 짜여 있다. 분봉이
섞이면 그 전제가 깨지고, 일봉 자연키 ``(entity_id, valid_from)`` 가 분 단위
``valid_from`` 과 충돌한다. 완전히 별도 표로 둔다(``store/tables.py`` 의
``prices_intraday``).

## 수집 범위를 좁힌 이유

전 종목 5년 분봉은 목표가 아니다. 화면이 보여주는 것은 **선택한 한 종목의
최근 캔들**뿐이다. 그래서 수집도 **보유 + 워치리스트 종목의 최근 며칠**로
좁힌다 — 전 종목·전 구간을 받으면 호출 수·파일 수가 목적에 안 맞게
부풀고, 화면은 그 데이터를 한 글자도 안 쓴다(``us-backfill-partition-
blowup`` 전례). ``recent_bars``(``ls_us_source.py``)와 같은 설계다 — 짧은
창은 페이징 없이 **한 번만** 부른다. LS 는 ``qrycnt`` 상한(500) 안의
**최신** 봉을 주므로, 분해능이 높은 구간(1분봉)은 하루 이틀치만, 낮은
구간(4시간봉)은 몇 달치가 한 호출에 들어온다 — 의도된 트레이드오프다.

## 파티션 — 날짜 축, 종목은 한 배치에 묶는다

``store/paths.py`` 는 ``(observed_date, ingest_run_id)`` 조합마다 파일
하나를 쓴다. 종목별로 ``store.append()`` 를 따로 부르면 파일 수가 종목
수만큼 늘어난다(미장 백필이 파일 247만 개를 만든 전례). 그래서 이 수집기는
**한 번의 실행(한 시장 · 한 interval)에 속한 모든 종목의 행을 한 번의
``store.append()`` 호출**로 적재한다 — 파일 수는 "실행 횟수 × interval 수"
에 비례하지 종목 수에 비례하지 않는다.

## ``ingest_run_id`` — 시간축이 반드시 들어간다

날짜 없는 고정 run_id 때문에 매일의 적재가 통째로 막힌 사고가 있었다
(``adjfactor-KR-0000`` — 다음날 재실행이 ``DuplicateIngestRun`` 으로 영원히
막혔다). 형식:

    intraday-{market}-{interval}-{yyyymmdd}

``{yyyymmdd}`` 는 **수집을 실행한 날**(``observed_at`` 의 날짜)이다. 하루
한 번 실행을 전제로 한다 — 그날 두 번째 실행은 의도적으로 거부된다
(``DuplicateIngestRun``, append-only 멱등성). 장중에 여러 번 갱신하려면
이 형식에 시·분을 더 넣어야 한다 — 지금 범위 밖이다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from quant_rl_trading.collectors.errors import CollectorError
from quant_rl_trading.collectors.latency import LatencyRecorder
from quant_rl_trading.collectors.ls_client import LSClient
from quant_rl_trading.collectors.ls_us_source import LsUsSource, UsSymbolUnavailable
from quant_rl_trading.collectors.market_hours import SPECS, Market
from quant_rl_trading.collectors.raw import RawArchive
from quant_rl_trading.replay.clock import Clock
from quant_rl_trading.store import Store

SOURCE = "ls_intraday"
TABLE = "prices_intraday"

PATH_CHART_KR = "/stock/chart"
TR_KR = "t8412"

PATH_CHART_US = "/overseas-stock/chart"
TR_US = "g3203"

#: 화면 버튼 이름 → LS ``ncnt``(분). 다섯 다 KR·US 양쪽에서 실호출로
#: 검증했다(모듈독스트링). 순서는 화면에 노출되는 순서와 같다.
INTERVAL_NCNT: dict[str, int] = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "1H": 60,
    "4H": 240,
}

#: LS 차트 TR 의 한 번 호출 상한. 이보다 많이 달라고 해도 최신 것부터
#: 이만큼만 온다 (``ls_us_source.recent_bars`` 와 같은 전제).
MAX_ROWS_PER_CALL = 500

_KR_TZ = ZoneInfo(SPECS[Market.KR].timezone)
_US_TZ = ZoneInfo(SPECS[Market.US].timezone)


def _to_float(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _local_timestamp(date_str: str, time_str: str, tz: ZoneInfo) -> datetime | None:
    """``YYYYMMDD`` + ``HHMMSS`` (그 시간대 현지시각) → UTC.

    형식이 어긋난 행은 조용히 버린다 — 장 시작 전 더미 행 등 실측으로도
    가끔 섞여 온다(0/빈 문자열).
    """
    date_str = (date_str or "").strip()
    time_str = (time_str or "").strip()
    if len(date_str) != 8 or len(time_str) != 6:
        return None
    if not (date_str.isdigit() and time_str.isdigit()):
        return None
    try:
        naive = datetime.strptime(date_str + time_str, "%Y%m%d%H%M%S")
    except ValueError:
        return None
    # 저장은 항상 UTC 로 통일한다 (다른 수집기와 같은 관례) — 현지 tzinfo 를
    # 그대로 붙여 두면 나중에 비교·정렬에서 "몇 시" 가 지역에 따라 갈린다.
    return naive.replace(tzinfo=tz).astimezone(UTC)


def normalize_kr(
    rows: list[dict[str, Any]],
    *,
    entity_id: str,
    interval: str,
    observed_at: datetime,
) -> list[dict[str, Any]]:
    """``t8412OutBlock1`` → ``prices_intraday`` 행. ``time`` 은 이미 KST."""
    normalized: list[dict[str, Any]] = []
    for row in rows:
        bar_ts = _local_timestamp(str(row.get("date") or ""), str(row.get("time") or ""), _KR_TZ)
        if bar_ts is None:
            continue
        normalized.append(
            {
                "entity_id": entity_id,
                "valid_from": bar_ts,
                "observed_at": observed_at,
                "source": SOURCE,
                "market": str(Market.KR),
                "interval": interval,
                "open": _to_float(row.get("open")),
                "high": _to_float(row.get("high")),
                "low": _to_float(row.get("low")),
                "close": _to_float(row.get("close")),
                "volume": _to_float(row.get("jdiff_vol")),
                "value": _to_float(row.get("value")),
            }
        )
    return normalized


def normalize_us(
    rows: list[dict[str, Any]],
    *,
    entity_id: str,
    interval: str,
    observed_at: datetime,
) -> list[dict[str, Any]]:
    """``g3203OutBlock1`` → ``prices_intraday`` 행. ``loctime`` 은 거래소 현지시각.

    ``amount``(대금)는 실측 샘플에서 계속 0 이었다 — LS 가 이 TR 에서는
    채우지 않는 것으로 보인다. 그대로 저장한다(``value`` 가 0 으로만
    남으면 그게 사실이다. 임의로 다른 값을 계산해 채우지 않는다).
    """
    normalized: list[dict[str, Any]] = []
    for row in rows:
        bar_ts = _local_timestamp(str(row.get("date") or ""), str(row.get("loctime") or ""), _US_TZ)
        if bar_ts is None:
            continue
        normalized.append(
            {
                "entity_id": entity_id,
                "valid_from": bar_ts,
                "observed_at": observed_at,
                "source": SOURCE,
                "market": str(Market.US),
                "interval": interval,
                "open": _to_float(row.get("open")),
                "high": _to_float(row.get("high")),
                "low": _to_float(row.get("low")),
                "close": _to_float(row.get("close")),
                "volume": _to_float(row.get("exevol")),
                "value": _to_float(row.get("amount")),
            }
        )
    return normalized


@dataclass
class IntradayCollector:
    """분봉 수집 — 한 실행에 여러 종목을 모아 한 번만 적재한다.

    ``kr_client``/``us_source`` 는 쓸 시장의 것만 채우면 된다. 둘 다 없는
    시장으로 부르면 ``CollectorError`` 다 — 조용히 빈 결과를 돌려주면
    "그 시장은 종목이 없다" 와 "그 시장은 아예 안 물었다" 가 구분 안 된다.
    """

    store: Store
    clock: Clock
    archive: RawArchive
    kr_client: LSClient | None = None
    us_source: LsUsSource | None = None
    #: ``collect_us`` 가 거래소를 못 찾아 건너뛴 종목 수. 호출부(도구)가
    #: 로그에 남기라고 필드로 뺐다 — 예외로 던지면 나머지 종목까지 멈춘다.
    last_skipped: int = 0

    def collect_kr(
        self,
        symbols: Sequence[str],
        *,
        interval: str,
        ingest_run_id: str,
    ) -> int:
        """국장 여러 종목의 분봉을 모아 한 번에 적재한다.

        ``symbols`` 는 접두어 없는 6자리 코드("005930") 또는 "KR:005930"
        둘 다 받는다 — 호출부가 유니버스에서 그대로 넘기기 편하게.
        """
        if self.kr_client is None:
            raise CollectorError("collect_kr 은 kr_client 없이 부를 수 없다")
        if interval not in INTERVAL_NCNT:
            raise CollectorError(f"모르는 interval: {interval!r} (다섯 개 중 하나여야 한다)")

        latency = LatencyRecorder(
            store=self.store, clock=self.clock, source=SOURCE, ingest_run_id=ingest_run_id
        )
        ncnt = INTERVAL_NCNT[interval]
        rows: list[dict[str, Any]] = []

        for raw_symbol in symbols:
            code = raw_symbol.rpartition(":")[2] or raw_symbol
            entity_id = f"{Market.KR}:{code}"

            with latency.stage("fetch", entity_id):
                payload = self.kr_client.request_tr(
                    PATH_CHART_KR,
                    TR_KR,
                    {
                        f"{TR_KR}InBlock": {
                            "shcode": code.lstrip("A"),
                            "ncnt": ncnt,
                            "qrycnt": MAX_ROWS_PER_CALL,
                            "nday": "0",
                            "sdate": "",
                            "stime": "",
                            "edate": "99999999",
                            "etime": "",
                            "cts_date": "",
                            "cts_time": "",
                            "comp_yn": "N",
                        }
                    },
                )

            observed_at = self.clock.now()
            with latency.stage("archive", entity_id):
                self.archive.save(
                    SOURCE,
                    payload,
                    observed_at=observed_at,
                    ingest_run_id=ingest_run_id,
                    label=f"{TR_KR}-{code}-{interval}",
                )

            with latency.stage("normalize", entity_id):
                rows.extend(
                    normalize_kr(
                        payload.get(f"{TR_KR}OutBlock1") or [],
                        entity_id=entity_id,
                        interval=interval,
                        observed_at=observed_at,
                    )
                )

        with latency.stage("append", f"batch-{interval}"):
            written = self.store.append(TABLE, rows, ingest_run_id=ingest_run_id)

        latency.flush()
        return written

    def collect_us(
        self,
        symbols: Sequence[str],
        *,
        interval: str,
        ingest_run_id: str,
    ) -> int:
        """미장 여러 종목의 분봉을 모아 한 번에 적재한다.

        종목마다 거래소(뉴욕 81 / 나스닥 82)를 먼저 알아내야 한다 —
        ``us_symbol()`` 주문 경로와 같은 문제다. ``LsUsSource.resolve_exchange``
        를 그대로 쓴다(g3101 로 82→81 순서 시도, 이미 검증된 방식 —
        ``ls-api.md`` §0-10). 못 찾은 종목은 건너뛰고 개수를 센다 — 화면이
        "그 종목은 시세가 없다" 를 알아야 하는데, 여기서 예외를 던지면
        나머지 종목의 수집까지 통째로 멈춘다.
        """
        if self.us_source is None:
            raise CollectorError("collect_us 는 us_source 없이 부를 수 없다")
        if interval not in INTERVAL_NCNT:
            raise CollectorError(f"모르는 interval: {interval!r} (다섯 개 중 하나여야 한다)")

        latency = LatencyRecorder(
            store=self.store, clock=self.clock, source=SOURCE, ingest_run_id=ingest_run_id
        )
        ncnt = INTERVAL_NCNT[interval]
        rows: list[dict[str, Any]] = []
        skipped = 0
        self.last_skipped = 0

        for raw_symbol in symbols:
            code = (raw_symbol.rpartition(":")[2] or raw_symbol).upper()
            entity_id = f"{Market.US}:{code}"

            with latency.stage("resolve", entity_id):
                try:
                    exchange = self.us_source.resolve_exchange(code)
                except (UsSymbolUnavailable, CollectorError):
                    exchange = None
            if exchange is None:
                skipped += 1
                continue

            with latency.stage("fetch", entity_id):
                payload = self.us_source.client.request_tr(
                    PATH_CHART_US,
                    TR_US,
                    {
                        f"{TR_US}InBlock": {
                            "delaygb": "R",
                            "keysymbol": f"{exchange}{code}",
                            "exchcd": exchange,
                            "symbol": code,
                            "ncnt": ncnt,
                            "qrycnt": MAX_ROWS_PER_CALL,
                            "comp_yn": "N",
                            "sdate": "",
                            "edate": "",
                        }
                    },
                )

            observed_at = self.clock.now()
            with latency.stage("archive", entity_id):
                self.archive.save(
                    SOURCE,
                    payload,
                    observed_at=observed_at,
                    ingest_run_id=ingest_run_id,
                    label=f"{TR_US}-{code}-{interval}",
                )

            with latency.stage("normalize", entity_id):
                rows.extend(
                    normalize_us(
                        payload.get(f"{TR_US}OutBlock1") or [],
                        entity_id=entity_id,
                        interval=interval,
                        observed_at=observed_at,
                    )
                )

        with latency.stage("append", f"batch-{interval}"):
            written = self.store.append(TABLE, rows, ingest_run_id=ingest_run_id)

        latency.flush()
        # 예외를 안 던지는 대신 조용히 사라지지도 않는다 — 호출부(도구)가
        # 이 값을 로그에 남긴다.
        self.last_skipped = skipped
        return written


def ingest_run_id(market: str, interval: str, *, observed_at: datetime) -> str:
    """모듈독스트링의 형식을 코드 한 곳에서만 만든다.

    호출부가 문자열을 손으로 이어붙이면 언젠가 날짜 자리를 빠뜨린 run_id가
    나오고, 그러면 adjfactor 사고가 재발한다.
    """
    return f"intraday-{market}-{interval}-{observed_at.strftime('%Y%m%d')}"
