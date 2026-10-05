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

**2026-10-05 국장 1m·5m 는 넓혔다** — 거래대금 상위 300 + 보유·후보(분봉 미시구조
연구, 이력이 쌓여야 쓸 수 있다). 대상은 그날 첫 회차가 골라 ``intraday_targets`` 에
적는다(``daily_targets``). 한도·시간·디스크·장중 호출당 봉 수는
``docs/design/ls-api.md`` §0-14. 국장 원본은 실행 하나에 gzip 묶음 한 파일이다.

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

    intraday-{market}-{interval}-{yyyymmddThhmm}

**분 단위까지 넣는다.** 날짜까지만 넣으면 그날 두 번째 실행이
``DuplicateIngestRun`` 으로 막히는데, 그러면 **분봉이 하루 종일 멈춰 있다** —
장 시작에 한 번 받은 봉을 장 마감까지 보게 된다. 분봉을 보는 목적과 정면으로
어긋난다.

날짜 고정 run_id 가 낸 사고(``adjfactor-KR-0000``)의 교훈은 "날짜를 넣어라"
가 아니라 **"다시 받아야 하는 주기보다 잘게 쪼개라"** 다. 일봉은 하루 한 번
확정되니 날짜면 충분하지만, 분봉은 장중 내내 새 봉이 생긴다.

같은 봉을 두 번 받는 것은 중복이 아니라 **재관측**이다. 창고가 bitemporal
이라 나중 ``observed_at`` 이 이긴다 — 늦게 정정된 봉도 자연스럽게 반영된다.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from quant_rl_trading.collectors.errors import CollectorError, LSAPIError
from quant_rl_trading.collectors.latency import LatencyRecorder
from quant_rl_trading.collectors.ls_client import LSClient
from quant_rl_trading.collectors.ls_us_source import LsUsSource, UsSymbolUnavailable
from quant_rl_trading.collectors.market_hours import SPECS, Market, local_time
from quant_rl_trading.collectors.raw import RawArchive
from quant_rl_trading.replay.clock import Clock
from quant_rl_trading.store import Store
from quant_rl_trading.store.errors import DuplicateIngestRun
from quant_rl_trading.store.prices import read_prices

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
    #: ``collect_kr`` 의 마지막 실행 결과 — 다시 불러도 실패한 종목, 시간 예산·장애 중단으로
    #: 부르지 못한 종목, 연속 실패로 멈췄는지. 도구가 rc 를 가르는 데 쓴다(조용한 실패 금지).
    last_failed: list[str] = field(default_factory=list)
    last_unfetched: list[str] = field(default_factory=list)
    last_aborted: bool = False

    def collect_kr(
        self,
        symbols: Sequence[str],
        *,
        interval: str,
        ingest_run_id: str,
        qrycnt: int = MAX_ROWS_PER_CALL,
        budget_sec: float | None = None,
        max_consecutive_failures: int | None = None,
    ) -> int:
        """국장 여러 종목의 분봉을 모아 한 번에 적재한다.

        ``symbols`` 는 접두어 없는 6자리 코드("005930") 또는 "KR:005930"
        둘 다 받는다 — 호출부가 유니버스에서 그대로 넘기기 편하게.

        **종목 하나의 실패가 실행을 죽이지 않는다**(ls-api.md §0-14). 9월 21종목
        회차에서 읽기 시간 초과가 42번 났고 그때마다 그 구간 전체가 0행이었다 —
        300종목이면 거의 매 회차가 죽는다. 실패는 세어 ``last_failed`` 에 남기고
        루프 끝에 **한 번** 다시 부른다. 그래도 **전 종목이 실패하면 예외다** —
        "API 가 죽었다" 가 "분봉이 없다(0행)" 로 둔갑하지 않는다.

        ``budget_sec`` 를 넘기면 남은 종목은 부르지 않고(``last_unfetched``) 받은
        것만 적재한다. 연속 실패가 ``max_consecutive_failures`` 면 장애로 보고
        멈춘다(``last_aborted``). 둘 다 시간은 Clock 으로 잰다(불변식 2).
        """
        if self.kr_client is None:
            raise CollectorError("collect_kr 은 kr_client 없이 부를 수 없다")
        if interval not in INTERVAL_NCNT:
            raise CollectorError(f"모르는 interval: {interval!r} (다섯 개 중 하나여야 한다)")

        latency = LatencyRecorder(
            store=self.store, clock=self.clock, source=SOURCE, ingest_run_id=ingest_run_id
        )
        client = self.kr_client
        ncnt = INTERVAL_NCNT[interval]
        started = self.clock.now()
        codes = list(dict.fromkeys((s.rpartition(":")[2] or s) for s in symbols))
        rows: list[dict[str, Any]] = []
        #: 원본 묶음 — 종목마다 응답 그대로를 JSON 한 줄로. 실행 하나에 파일 하나(§0-14).
        #: dict 가 아니라 문자열로 든다: 500봉 × 300종목의 응답 dict 를 끝까지 들면 그것만 120MB 다.
        bundle: dict[str, str] = {}
        failed: dict[str, LSAPIError] = {}
        unfetched: list[str] = []
        self.last_failed = []
        self.last_unfetched = []
        self.last_aborted = False
        consecutive = 0

        def over_budget() -> bool:
            return budget_sec is not None and (self.clock.now() - started).total_seconds() >= budget_sec

        def fetch(code: str) -> None:
            entity_id = f"{Market.KR}:{code}"
            with latency.stage("fetch", entity_id):
                payload = client.request_tr(
                    PATH_CHART_KR,
                    TR_KR,
                    {
                        f"{TR_KR}InBlock": {
                            "shcode": code.lstrip("A"),
                            "ncnt": ncnt,
                            "qrycnt": qrycnt,
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
            bundle[code] = json.dumps(
                {"code": code, "observed_at": observed_at.isoformat(), "payload": payload},
                ensure_ascii=False,
                separators=(",", ":"),
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

        for index, code in enumerate(codes):
            if over_budget():
                unfetched = codes[index:]
                break
            try:
                fetch(code)
            except LSAPIError as error:
                failed[code] = error
                consecutive += 1
                if max_consecutive_failures is not None and consecutive >= max_consecutive_failures:
                    self.last_aborted = True
                    unfetched = codes[index + 1 :]
                    break
            else:
                consecutive = 0

        # 실패한 종목을 한 번만 다시 — 시간 초과는 대개 순간이다. 장애로 멈췄으면 안 한다.
        if not self.last_aborted:
            for code in list(failed):
                if over_budget():
                    break
                try:
                    fetch(code)
                except LSAPIError as error:
                    failed[code] = error
                else:
                    del failed[code]

        if codes and not bundle:
            # 하나도 못 받았다 — 0행으로 적지 않고 마지막 오류를 그대로 올린다.
            if failed:
                raise list(failed.values())[-1]
            raise CollectorError(
                f"분봉 {interval}: {len(codes)}종목 중 하나도 못 받았다(시간 예산 {budget_sec}초)"
            )

        self.last_failed = sorted(failed)
        self.last_unfetched = unfetched
        if bundle:
            with latency.stage("archive", f"batch-{interval}"):
                self.archive.save_bundle(
                    SOURCE,
                    list(bundle.values()),
                    observed_at=started,
                    ingest_run_id=ingest_run_id,
                    label=f"{TR_KR}-{interval}-q{qrycnt}",
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
    return f"intraday-{market}-{interval}-{observed_at.strftime('%Y%m%dT%H%M')}"


# -- 수집 대상: 거래대금 상위 N (docs/design/ls-api.md §0-14) ---------------------

TARGETS_TABLE = "intraday_targets"
TARGETS_SOURCE = "intraday_targets"
BASIS_BASE = "base"
BASIS_TOP_VALUE = "top_value"


def select_top_value(prices: pd.DataFrame, *, top_n: int, sessions: int) -> pd.DataFrame:
    """일봉(``entity_id``·``valid_from``·``value``) → 최근 ``sessions`` 세션 평균 거래대금 상위 ``top_n``.

    **마지막 세션에 행이 없는 종목은 뺀다** — 거래정지·상폐 종목은 분봉도 안 나온다.
    평균은 창 안에 있는 행만으로(신규 상장은 짧은 평균). 0·결측 평균은 순위에 안 넣는다.
    동점은 종목코드 순 — 같은 입력이면 같은 명단(결정론).
    돌려주는 열: ``entity_id``·``rank``(1부터)·``adv``.
    """
    empty = pd.DataFrame({"entity_id": pd.Series(dtype=str), "rank": pd.Series(dtype="int64"),
                          "adv": pd.Series(dtype=float)})
    if prices.empty or top_n <= 0 or sessions <= 0:
        return empty
    days = sorted(prices["valid_from"].unique())[-sessions:]
    window = prices[prices["valid_from"].isin(days)]
    alive = set(window.loc[window["valid_from"] == days[-1], "entity_id"])
    adv = window[window["entity_id"].isin(alive)].groupby("entity_id")["value"].mean()
    adv = adv[adv > 0].dropna()
    if adv.empty:
        return empty
    ranked = (
        adv.rename("adv").reset_index()
        .sort_values(["adv", "entity_id"], ascending=[False, True], kind="mergesort")
        .head(top_n)
        .reset_index(drop=True)
    )
    ranked["rank"] = range(1, len(ranked) + 1)
    return ranked[["entity_id", "rank", "adv"]]


def daily_targets(
    store: Store,
    clock: Clock,
    *,
    market: Market,
    base: Sequence[str],
    top_n: int,
    sessions: int,
    record: bool = True,
) -> tuple[list[str], bool]:
    """오늘(그 시장 현지 날짜)의 분봉 대상 — (순서 있는 종목 목록, 이번에 계산했는지).

    **그날 처음 부른 쪽이 계산해 ``intraday_targets`` 에 적고**, 뒤 회차는 그 기록을
    읽는다(point-in-time). 계산은 그 시각(``as_of=지금``)까지 관측된 일봉뿐이다 —
    09:00 회차면 전날 마감까지. 순서는 ``base``(보유·후보, 매 회차 새로) 먼저, 그다음
    거래대금 순 — 시간 예산에 잘리면 덜 유동적인 쪽이 잘린다.

    ``record=False`` 면 계산만 하고 적지 않는다(``--dry-run``).
    """

    now = clock.now()
    session = local_time(market, now).date().isoformat()

    def recorded() -> list[str] | None:
        seen = store.get(TARGETS_TABLE, as_of=now, market=str(market), lookback=3)
        if seen.empty:
            return None
        seen = seen[seen["session"] == session]
        if seen.empty:
            return None
        latest = seen[seen["valid_from"] == seen["valid_from"].max()]
        top = latest[latest["basis"] == BASIS_TOP_VALUE].sort_values("rank")
        return list(top["entity_id"])

    top_ids = recorded()
    computed = False
    if top_ids is None:
        # read_prices — 휴장일 종가 0 세션이 "마지막 세션" 을 빈 날로 만들면 전 종목이 빠진다.
        prices = read_prices(
            store,
            as_of=now,
            market=str(market),
            # 세션 수 → 달력일. 추석 같은 긴 휴장을 넘어도 창이 차도록 넉넉히.
            lookback=sessions * 2 + 15,
            columns=["entity_id", "valid_from", "value"],
        )
        ranked = select_top_value(prices, top_n=top_n, sessions=sessions)
        if ranked.empty and top_n > 0:
            # 빈 명단을 그날의 기록으로 적으면 하루 내내 "추가 0" 이 정상처럼 돈다.
            raise CollectorError(f"{market} 일봉이 비어 거래대금 상위 {top_n} 를 못 골랐다(as_of {now:%F %T})")
        top_ids = list(ranked["entity_id"])
        ranked_ids = set(top_ids)
        common = {
            "valid_from": now,
            "observed_at": now,
            "source": TARGETS_SOURCE,
            "market": str(market),
            "session": session,
            "adv_sessions": sessions,
        }
        rows = [
            {**common, "entity_id": entity, "basis": BASIS_BASE, "rank": None, "adv": None}
            for entity in dict.fromkeys(base)
            if entity not in ranked_ids
        ] + [
            {**common, "entity_id": row.entity_id, "basis": BASIS_TOP_VALUE, "rank": int(row.rank),
             "adv": float(row.adv)}
            for row in ranked.itertuples(index=False)
        ]
        if not record:
            return list(dict.fromkeys([*base, *top_ids])), False
        try:
            store.append(
                TARGETS_TABLE, rows,
                ingest_run_id=f"intraday-targets-{market}-{session.replace('-', '')}",
            )
            computed = True
        except DuplicateIngestRun:
            # 다른 프로세스가 먼저 적었다 — 그쪽 기록을 따른다(그날 명단은 하나).
            top_ids = recorded() or top_ids
    return list(dict.fromkeys([*base, *top_ids])), computed
