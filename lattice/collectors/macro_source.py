"""거시지표 발표 일정·실측값 — FRED(미국) / ECOS(한국).

## 시간 두 개, 그리고 세 번째

이 테이블만 시각이 셋이다.

- ``scheduled_at`` — **발표가 일어나는 시각.** 미래일 수 있다
- ``valid_from`` — 우리가 그 사실을 안 시각. 일정을 받아온 시각이거나,
  실측값이 공표된 시각
- ``observed_at`` — 우리가 받아온 시각

발표 시각을 ``valid_from`` 에 넣고 싶어지지만 그러면 안 된다. 게이트의
lookback 창은 과거로만 열려서, 미래 ``valid_from`` 을 가진 행은 어느 창에도
안 걸린다 — "다가오는 일정" 을 영영 못 찾게 된다. 그래서 발표 시각은 별도
컬럼이고, 창은 언제나 과거로만 연다.

## 실측값의 observed_at 은 발표 시각이다

CPI 가 8:30 ET 에 나왔는데 우리가 9:00 에 받아왔다면 ``observed_at`` 은
8:30 이 아니라 **9:00** 이다 — 우리가 알 수 있었던 시각이 그때다. 발표
시각을 관측시각으로 찍으면 30분을 미래로 사는 셈이 된다.

## ECOS 의 503 은 서버 장애가 아니다

등록되지 않은 인증키로 부르면 ECOS 는 JSON 오류가 아니라 ``503 - Service
Unavailable`` **HTML 페이지**를 HTTP 404 로 돌려준다. 이 프로젝트는 그걸
서버 장애로 오판해 반나절 KOSIS 우회로를 팠다. 유효한 키로 바꾸니 같은
URL 이 즉시 200 을 냈다.

**응답이 ``<`` 로 시작하면 키부터 의심할 것.** 그래서 :meth:`EcosSource.search`
는 HTML 을 받으면 그 뜻을 담은 예외를 낸다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from lattice.collectors.errors import CollectorError, MissingCredentials
from lattice.collectors.market_hours import Market
from lattice.replay.clock import Clock

MACRO_RELEASES = "macro_releases"
FRED_SOURCE = "fred"
ECOS_SOURCE = "ecos"

FRED_BASE = "https://api.stlouisfed.org/fred"
ECOS_BASE = "https://ecos.bok.or.kr/api"
FRED_KEY_ENV = "FRED_API_KEY"
ECOS_KEY_ENV = "ECOS_API_KEY"

#: 미국 지표 발표는 거의 전부 08:30 ET 다. 분 단위가 틀리면 그날 장 시작 전
#: 인지 후인지가 뒤집히므로 시장 시각으로 붙인다.
US_RELEASE_TZ = ZoneInfo("America/New_York")
US_RELEASE_TIME = time(8, 30)

#: 국장 공표 시각. kosis_source 와 같은 규칙이다 — 두 소스가 다른 시각을 쓰면
#: 같은 지표가 화면에서 두 시각에 뜬다.
KST = ZoneInfo("Asia/Seoul")
KR_RELEASE_TIME = time(8, 0)

#: 볼 지표. FRED release_id → (지표 코드, 값을 읽을 시리즈, 단위).
#: **전부 담지 않는다** — FRED 는 한 달에 800건을 발표하고, 그중 화면에
#: 띄울 값어치가 있는 것은 손에 꼽는다.
#: **ID 는 이름으로 확인한 값이다.** 짐작한 두 개(18·21)가 실제로는 전혀 다른
#: 발표(H.15 금리)여서, 화면에 "소매판매"라는 이름으로 금리 일정이 떴다.
#: 새 지표를 넣을 때는 ``/fred/releases`` 를 페이지네이션해 이름으로 대조할 것.
FRED_RELEASES: dict[int, tuple[str, str, str]] = {
    # 물가
    10: ("CPI", "CPIAUCSL", "index"),
    46: ("PPI", "PPIACO", "index"),
    54: ("PCE", "PCEPI", "index"),
    # 금리 — 연준 정책의 결과가 여기 먼저 나타난다
    18: ("FED_FUNDS", "DFF", "percent"),
    # 고용
    50: ("EMPLOYMENT", "UNRATE", "percent"),
    192: ("JOLTS", "JTSJOL", "thousands"),
    180: ("JOBLESS_CLAIMS", "ICSA", "persons"),
    11: ("EMPLOYMENT_COST", "ECIALLCIV", "index"),
    # 성장·생산
    53: ("GDP", "GDPC1", "bn_chained"),
    13: ("INDUSTRIAL_PRODUCTION", "INDPRO", "index"),
    # 소비·주택·무역
    9: ("RETAIL_ADVANCE", "RSAFS", "mn_usd"),
    436: ("RETAIL_SALES", "RSAFS", "mn_usd"),
    27: ("HOUSING_STARTS", "HOUST", "thousands"),
    51: ("TRADE_BALANCE", "BOPGSTB", "mn_usd"),
}


#: 시장 반응을 재려면 지수가 필요하다. 미장 지수는 창고에 없어서(국장 KRX 만
#: 있다) FRED 에서 같이 받는다. ``indices`` 에 넣는 이유는 ``prices`` 에 지수를
#: 섞으면 종목 유니버스가 오염되기 때문이다 (tables.py).
FRED_INDICES: dict[str, tuple[str, str]] = {
    "SP500": ("US:IDX:SP500", "S&P 500"),
}


class MacroUnavailable(CollectorError):
    """소스가 응답하지 않는다."""


def _number(value: Any) -> float | None:
    if value is None or value in ("", "."):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _kr_publication_moment(period: str, *, lag_days: int) -> datetime | None:
    """국장 기준시점(``YYYYMM``) → 공표 시각(UTC)."""
    from lattice.collectors.kosis_source import publication_moment

    return publication_moment(period, lag_days=lag_days)


def _us_release_moment(day: date) -> datetime:
    """발표일 → 발표 시각(UTC). 지역 시각을 거치므로 서머타임은 tz 가 처리한다."""
    local = datetime.combine(day, US_RELEASE_TIME, tzinfo=US_RELEASE_TZ)
    return local.astimezone(UTC)


@dataclass
class FredSource:
    """FRED 조회. 발표 일정과 실측값을 각각 다른 엔드포인트에서 받는다."""

    api_key: str
    name: str = FRED_SOURCE
    timeout: float = 25.0
    client: httpx.Client | None = None

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> FredSource:
        source = env if env is not None else dict(os.environ)
        return cls(api_key=(source.get(FRED_KEY_ENV) or "").strip())

    def usable(self) -> bool:
        return bool(self.api_key)

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.usable():
            raise MissingCredentials(f"{FRED_KEY_ENV} 미설정")
        owned = self.client is None
        http = self.client or httpx.Client(timeout=self.timeout)
        try:
            response = http.get(
                f"{FRED_BASE}{path}",
                params={**params, "api_key": self.api_key, "file_type": "json"},
            )
        finally:
            if owned:
                http.close()
        if response.status_code != 200:
            raise MacroUnavailable(f"FRED {response.status_code}: {response.text[:200]}")
        return dict(response.json())

    def release_dates(self, *, start: date, end: date) -> list[dict[str, Any]]:
        """구간 안의 발표 일정. 우리가 보는 지표만 남긴다."""
        payload = self._get(
            "/releases/dates",
            {
                "realtime_start": start.isoformat(),
                "realtime_end": end.isoformat(),
                "include_release_dates_with_no_data": "true",
                "limit": 1000,
            },
        )
        out: list[dict[str, Any]] = []
        for row in payload.get("release_dates") or []:
            release_id = int(row.get("release_id", 0))
            if release_id in FRED_RELEASES:
                out.append(row)
        return out

    def past_release_dates(self, release_id: int, *, limit: int = 3) -> list[dict[str, Any]]:
        """그 릴리스의 **지난** 발표일. 최신순.

        통합 엔드포인트(``/releases/dates``)는 다가올 날짜만 준다 — 실측했다.
        지난 발표를 화면에 띄우려면 릴리스별 엔드포인트를 따로 불러야 한다.
        """
        payload = self._get(
            "/release/dates",
            {"release_id": release_id, "sort_order": "desc", "limit": limit},
        )
        rows = []
        for row in payload.get("release_dates") or []:
            # 이 엔드포인트는 release_name 을 주지 않는다. 호출부가 아는
            # 값이므로 여기서 채워 아래 정규화가 한 가지 모양만 보게 한다.
            rows.append({**row, "release_id": release_id})
        return rows

    def release_name(self, release_id: int) -> str:
        """릴리스 이름 한 건. 통합 응답에 안 걸린 릴리스를 위한 보조 경로다."""
        payload = self._get("/release", {"release_id": release_id})
        rows = payload.get("releases") or []
        return str(rows[0].get("name")) if rows else ""

    def latest_observations(self, series_id: str, *, limit: int = 2) -> list[dict[str, Any]]:
        """최근 관측값. 직전 값까지 받아야 화면에 변화를 보여줄 수 있다."""
        payload = self._get(
            "/series/observations",
            {"series_id": series_id, "sort_order": "desc", "limit": limit},
        )
        return list(payload.get("observations") or [])


#: ECOS 통계. **눈으로 확인한 코드만 넣는다** (2026-08-13).
#: 형식: 지표 → (통계표코드, 주기, 항목코드1, 단위, 공표지연일)
#: KOSIS 에 없는 **정책금리·시장금리**가 여기 있다.
ECOS_STATS: dict[str, dict[str, str]] = {
    "BASE_RATE": {
        "stat_code": "722Y001",  # 한국은행 기준금리 및 여수신금리
        "item_code": "0101000",  # 한국은행 기준금리
        "cycle": "M",
        "unit": "percent",
        "publication_lag_days": "1",
    },
    "TREASURY_3Y": {
        "stat_code": "721Y001",  # 시장금리(월,분기,년)
        "item_code": "5020000",  # 국고채(3년)
        "cycle": "M",
        "unit": "percent",
        "publication_lag_days": "1",
    },
}


@dataclass
class EcosSource:
    """한국은행 ECOS.

    **503 은 서버 장애가 아니라 무효한 키다.** 등록되지 않은 인증키로 부르면
    ECOS 는 JSON 오류가 아니라 ``503 - Service Unavailable`` HTML 페이지를
    HTTP 404 로 돌려준다. 그래서 키 문제를 서버 장애로 오판하기 쉽다 —
    실제로 이 프로젝트가 반나절 그렇게 오판했다. 응답이 HTML 이면 키부터
    의심할 것.
    """

    api_key: str
    name: str = ECOS_SOURCE
    timeout: float = 25.0
    client: httpx.Client | None = None

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> EcosSource:
        source = env if env is not None else dict(os.environ)
        return cls(api_key=(source.get(ECOS_KEY_ENV) or "").strip())

    def usable(self) -> bool:
        return bool(self.api_key)

    def search(self, spec: dict[str, str], *, start: str, end: str) -> list[dict[str, Any]]:
        """통계 조회. URL 경로에 파라미터를 **순서대로** 붙이는 규격이다."""
        if not self.usable():
            raise MissingCredentials(f"{ECOS_KEY_ENV} 미설정")
        path = "/".join(
            [
                f"{ECOS_BASE}/StatisticSearch",
                self.api_key, "json", "kr", "1", "10",
                spec["stat_code"], spec.get("cycle", "M"), start, end, spec["item_code"],
            ]
        )
        owned = self.client is None
        http = self.client or httpx.Client(timeout=self.timeout)
        try:
            response = http.get(path)
        finally:
            if owned:
                http.close()
        text = response.text.lstrip()
        if text.startswith("<"):
            raise MacroUnavailable(
                "ECOS 가 HTML 을 돌려줬다 — 인증키가 등록되지 않았을 가능성이 크다"
            )
        payload = response.json()
        if "RESULT" in payload:
            raise MacroUnavailable(f"ECOS: {payload['RESULT']}")
        return list(payload.get("StatisticSearch", {}).get("row", []))


# -----------------------------------------------------------------------------
# 정규화
# -----------------------------------------------------------------------------


def release_rows(
    dates: list[dict[str, Any]],
    *,
    observed_at: datetime,
    values: dict[str, list[dict[str, Any]]] | None = None,
    source: str = FRED_SOURCE,
) -> list[dict[str, Any]]:
    """발표 일정 → macro_releases 행.

    ``values`` 가 있으면 이미 지난 발표에 실측값을 붙인다. 아직 안 지난
    발표는 ``actual`` 을 비운다 — **없는 값을 0 으로 채우지 않는다.** 0 은
    "발표됐고 0이었다" 라는 뜻이라 완전히 다른 사실이다.
    """
    values = values or {}
    rows: list[dict[str, Any]] = []

    for item in dates:
        release_id = int(item.get("release_id", 0))
        spec = FRED_RELEASES.get(release_id)
        if spec is None:
            continue
        indicator, series_id, unit = spec

        try:
            day = date.fromisoformat(str(item.get("date")))
        except (TypeError, ValueError):
            continue
        scheduled_at = _us_release_moment(day)

        observations = values.get(series_id) or []
        actual = previous = None
        status = "scheduled"
        if scheduled_at <= observed_at and observations:
            actual = _number(observations[0].get("value"))
            if len(observations) > 1:
                previous = _number(observations[1].get("value"))
            if actual is not None:
                status = "released"

        rows.append(
            {
                "entity_id": f"{Market.US}:{indicator}",
                # 우리가 안 시각. 발표 시각이 아니다 (모듈 docstring).
                "valid_from": observed_at,
                "observed_at": observed_at,
                "source": source,
                "market": str(Market.US),
                "indicator": indicator,
                "release_name": str(item.get("release_name") or indicator),
                "scheduled_at": scheduled_at,
                "actual": actual,
                "previous": previous,
                "unit": unit,
                "status": status,
            }
        )
    return rows


def macro_run_id(market: Market, moment: datetime) -> str:
    return f"macro-{market}-{moment:%Y%m%dT%H%M%S}"


@dataclass
class MacroCollector:
    """일정과 실측값을 모아 ``macro_releases`` 에 넣는다."""

    store: Any
    source: FredSource
    clock: Clock
    archive: Any
    market: Market = Market.US
    #: 앞뒤로 훑을 날수. 지난 발표는 실측값을, 다가올 발표는 일정을 얻는다.
    past_days: int = 30
    future_days: int = 45

    def collect(self) -> int:
        observed_at = self.clock.now().astimezone(UTC)
        run_id = macro_run_id(self.market, observed_at)
        if self.store.ingest_run_recorded(MACRO_RELEASES, run_id):
            return 0

        today = observed_at.date()
        # 다가올 일정.
        dates = self.source.release_dates(
            start=today - timedelta(days=self.past_days),
            end=today + timedelta(days=self.future_days),
        )
        # 지난 발표. 통합 엔드포인트가 안 주므로 릴리스별로 받는다.
        # 릴리스별 엔드포인트는 release_name 을 안 주므로, 통합 응답에서 본
        # **진짜 이름**을 옮겨 붙인다. 지표 코드를 이름 자리에 넣으면 화면에
        # "CPI (미국) CPI" 처럼 같은 말이 두 번 나온다.
        names = {
            int(item["release_id"]): str(item.get("release_name") or "")
            for item in dates
            if item.get("release_name")
        }
        floor = today - timedelta(days=self.past_days)
        for release_id in FRED_RELEASES:
            for row in self.source.past_release_dates(release_id):
                try:
                    day = date.fromisoformat(str(row.get("date")))
                except (TypeError, ValueError):
                    continue
                if day >= floor:
                    label = names.get(release_id) or self.source.release_name(release_id)
                    dates.append({**row, "release_name": label})

        if not dates:
            return 0

        # 같은 (릴리스, 날짜) 가 두 경로로 들어올 수 있다.
        unique: dict[tuple[int, str], dict[str, Any]] = {}
        for item in dates:
            unique.setdefault((int(item.get("release_id", 0)), str(item.get("date"))), item)
        dates = list(unique.values())

        # 지난 발표에만 값을 붙인다. 시리즈당 한 번만 조회한다.
        wanted = {
            FRED_RELEASES[int(item["release_id"])][1]
            for item in dates
            if _us_release_moment(date.fromisoformat(str(item["date"]))) <= observed_at
        }
        values = {
            series_id: self.source.latest_observations(series_id) for series_id in sorted(wanted)
        }

        self.archive.save(
            self.source.name,
            {"dates": dates, "values": values},
            observed_at=observed_at,
            ingest_run_id=run_id,
            label=f"macro-{self.market}-{observed_at:%Y%m%d}",
        )
        rows = release_rows(dates, observed_at=observed_at, values=values)
        if not rows:
            return 0
        return int(self.store.append(MACRO_RELEASES, rows, ingest_run_id=run_id))


def ecos_rows(
    indicator: str,
    spec: dict[str, str],
    rows: list[dict[str, Any]],
    *,
    observed_at: datetime,
    source: str = ECOS_SOURCE,
) -> list[dict[str, Any]]:
    """ECOS 관측값 → macro_releases 행. 최신 한 건만 남긴다.

    ``TIME`` 은 기준시점(``YYYYMM``)이지 발표 시각이 아니다. 그대로 쓰면
    7월 금리를 7월 1일부터 알고 있던 것이 되므로 공표 지연을 더한다.
    """
    ordered = sorted(rows, key=lambda row: str(row.get("TIME", "")))
    if not ordered:
        return []

    latest = ordered[-1]
    scheduled_at = _kr_publication_moment(
        str(latest.get("TIME", "")), lag_days=int(spec.get("publication_lag_days", 0))
    )
    if scheduled_at is None or scheduled_at > observed_at:
        return []

    actual = _number(latest.get("DATA_VALUE"))
    previous = _number(ordered[-2].get("DATA_VALUE")) if len(ordered) > 1 else None
    if actual is None:
        return []

    return [
        {
            "entity_id": f"{Market.KR}:{indicator}",
            "valid_from": observed_at,
            "observed_at": observed_at,
            "source": source,
            "market": str(Market.KR),
            "indicator": indicator,
            "release_name": str(latest.get("ITEM_NAME1") or latest.get("STAT_NAME") or indicator),
            "scheduled_at": scheduled_at,
            "actual": actual,
            "previous": previous,
            "unit": str(latest.get("UNIT_NAME") or spec.get("unit", "")),
            # ECOS 도 일정을 주지 않는다. 받은 것은 전부 공표된 값이다.
            "status": "released",
        }
    ]


@dataclass
class EcosCollector:
    """ECOS 통계를 모아 ``macro_releases`` 에 넣는다."""

    store: Any
    source: EcosSource
    clock: Clock
    archive: Any
    market: Market = Market.KR
    lookback_months: int = 8

    def collect(self) -> int:
        if not self.source.usable():
            return 0

        observed_at = self.clock.now().astimezone(UTC)
        run_id = f"macro-{self.market}-ecos-{observed_at:%Y%m%dT%H%M%S}"
        if self.store.ingest_run_recorded(MACRO_RELEASES, run_id):
            return 0

        end = observed_at.astimezone(KST)
        start = end - timedelta(days=31 * self.lookback_months)

        rows: list[dict[str, Any]] = []
        payloads: dict[str, Any] = {}
        for indicator, spec in ECOS_STATS.items():
            try:
                found = self.source.search(
                    spec, start=start.strftime("%Y%m"), end=end.strftime("%Y%m")
                )
            except CollectorError:
                # 한 지표 실패로 나머지를 버리지 않는다.
                continue
            payloads[indicator] = found
            rows.extend(ecos_rows(indicator, spec, found, observed_at=observed_at))

        if not rows:
            return 0

        self.archive.save(
            self.source.name, payloads, observed_at=observed_at,
            ingest_run_id=run_id, label=f"ecos-{observed_at:%Y%m%d}",
        )
        return int(self.store.append(MACRO_RELEASES, rows, ingest_run_id=run_id))


def index_rows(
    series_id: str,
    observations: list[dict[str, Any]],
    *,
    observed_at: datetime,
    source: str = FRED_SOURCE,
) -> list[dict[str, Any]]:
    """지수 관측값 → indices 행.

    ``observed_at`` 을 **그 세션의 마감 후**로 찍는다. 오늘 시각을 전부에
    찍으면 과거 종가를 오늘에야 알게 된 것이 되어, 발표 당일 반응을 재는
    질의가 그 종가를 못 본다.
    """
    entity_id, _ = FRED_INDICES[series_id]
    rows: list[dict[str, Any]] = []
    for item in observations:
        try:
            day = date.fromisoformat(str(item.get("date")))
        except (TypeError, ValueError):
            continue
        close = _number(item.get("value"))
        if close is None:
            continue
        session_close = _us_release_moment(day).replace(hour=21, minute=0)
        if session_close > observed_at:
            # 아직 마감하지 않은 세션. 저장하면 미래를 보는 것이 된다.
            continue
        rows.append(
            {
                "entity_id": entity_id,
                "valid_from": datetime(day.year, day.month, day.day, tzinfo=UTC),
                "observed_at": session_close,
                "source": source,
                "market": str(Market.US),
                "board": "index",
                "open": None,
                "high": None,
                "low": None,
                "close": close,
                "volume": None,
                "value": None,
            }
        )
    return rows


@dataclass
class IndexCollector:
    """시장 반응 측정용 지수. 국장은 KRX 가 이미 있고, 미장만 여기서 받는다."""

    store: Any
    source: FredSource
    clock: Clock
    archive: Any
    days: int = 400

    def collect(self) -> int:
        observed_at = self.clock.now().astimezone(UTC)
        run_id = f"idx-US-{observed_at:%Y%m%dT%H%M%S}"
        if self.store.ingest_run_recorded("indices", run_id):
            return 0

        rows: list[dict[str, Any]] = []
        payloads: dict[str, Any] = {}
        for series_id in FRED_INDICES:
            try:
                observations = self.source.latest_observations(series_id, limit=self.days)
            except CollectorError:
                continue
            payloads[series_id] = observations
            rows.extend(index_rows(series_id, observations, observed_at=observed_at))

        if not rows:
            return 0
        self.archive.save(
            self.source.name, payloads, observed_at=observed_at,
            ingest_run_id=run_id, label=f"idx-US-{observed_at:%Y%m%d}",
        )
        return int(self.store.append("indices", rows, ingest_run_id=run_id))
