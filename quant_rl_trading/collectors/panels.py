"""패널 백필 — 세션 하나에서 한 테이블을 채우는 것들.

``backfill.Backfiller`` 는 prices + universe 를 함께 다룬다. 둘이 상폐 감지로
얽혀 있기 때문이다. 수급·공매도·펀더멘털·지수는 그런 얽힘이 없다 — 세션당
한 프레임을 받아 한 테이블에 넣으면 끝이다. 그 단순한 것을 위해 상폐 상태
기계를 끌고 다닐 이유가 없다.

공유하는 것은 **세션 파이프라인의 뼈대**다. 관측시각 결정 → 원본 보존 →
정규화 → 적재, 그리고 재개·지연실측. 그건 ``session_pipeline`` 하나로 묶어
두 경로가 같은 코드를 쓴다.

가장 중요한 것은 ``lag_days`` 다. 같은 세션의 사실이라도 일봉은 마감 직후에,
**공매도는 T+1~2 에** 공표된다. 하나의 지연으로 뭉뚱그리면 늦게 나오는 쪽이
통째로 미래를 본다.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from quant_rl_trading.collectors.errors import CollectorError
from quant_rl_trading.collectors.krx_source import PanelSource
from quant_rl_trading.collectors.latency import LatencyRecorder
from quant_rl_trading.collectors.market_hours import Market, trading_days
from quant_rl_trading.collectors.publication import NotYetPublished, ObservedAtPolicy
from quant_rl_trading.collectors.raw import RawArchive
from quant_rl_trading.replay.clock import Clock
from quant_rl_trading.store import DuplicateIngestRun, Store

#: 세션 하나에 대한 원본 응답을 받아오는 것.
Fetch = Callable[[PanelSource, date], list[dict[str, Any]]]

#: 원본 행 하나 → 저장 행 하나. None 이면 버린다.
Normalize = Callable[[dict[str, Any], Market, datetime, datetime], dict[str, Any] | None]

FUNDAMENTALS = "fundamentals"
SHORTING = "shorting"


def session_timestamp(day: date) -> datetime:
    """거래일을 valid_from 으로. UTC 자정 고정 — 지역 자정을 쓰면 하루가 밀린다."""
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def run_id_for(table: str, market: Market, day: date) -> str:
    """결정론적 run id. 재개가 이것으로 성립한다."""
    return f"bf-{table}-{market}-{day.strftime('%Y%m%d')}"


def number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


#: 원본 행 하나 → 저장 행 **여럿**. 지표가 열로 오는 응답(상장주식수·시가총액,
#: 펀더멘털)을 long 포맷으로 펴는 데 쓴다.
Expand = Callable[[dict[str, Any], Market, datetime, datetime], list[dict[str, Any]]]


@dataclass(frozen=True)
class Panel:
    """세션당 한 테이블을 채우는 데이터셋 하나."""

    table: str
    fetch: Fetch
    #: 1:1 정규화. ``expand`` 가 있으면 쓰이지 않는다.
    normalize: Normalize | None = None
    #: 1:N 정규화. 지표 하나가 행 하나인 long 포맷을 만들 때 쓴다.
    expand: Expand | None = None
    #: 공표 지연(거래일). 공매도처럼 T+N 에 나오는 것에 쓴다.
    lag_days: int = 0


# -----------------------------------------------------------------------------
# 정규화 — KRX 원본 행 → 저장 행
# -----------------------------------------------------------------------------


def _base(
    code: str, market: Market, valid_from: datetime, observed_at: datetime
) -> dict[str, Any]:
    return {
        "entity_id": f"{market}:{code}",
        "valid_from": valid_from,
        "observed_at": observed_at,
        "source": "krx",
        "market": str(market),
    }


def normalize_flow(
    row: dict[str, Any], market: Market, valid_from: datetime, observed_at: datetime
) -> dict[str, Any] | None:
    code = str(row.get("code") or "").strip()
    if not code:
        return None
    return {
        **_base(code, market, valid_from, observed_at),
        "investor": str(row.get("investor") or ""),
        "net_value": number(row.get("net_value")),
        "net_volume": number(row.get("net_volume")),
        # 백필은 마감 후 확정치다. 장중 잠정치는 라이브 경로에서 들어온다.
        "is_final": True,
    }


def normalize_shorting(
    row: dict[str, Any], market: Market, valid_from: datetime, observed_at: datetime
) -> dict[str, Any] | None:
    code = str(row.get("code") or "").strip()
    if not code:
        return None
    return {
        **_base(code, market, valid_from, observed_at),
        "board": str(row.get("board") or ""),
        "short_volume": number(row.get("short_volume")),
        "total_volume": number(row.get("total_volume")),
        "short_ratio": number(row.get("short_ratio")),
    }


OHLCV_FIELDS = ("open", "high", "low", "close", "volume", "value")


def normalize_index(
    row: dict[str, Any], market: Market, valid_from: datetime, observed_at: datetime
) -> dict[str, Any] | None:
    code = str(row.get("code") or "").strip()
    if not code:
        return None
    return {
        **_base(code, market, valid_from, observed_at),
        "board": str(row.get("board") or ""),
        **{name: number(row.get(name)) for name in OHLCV_FIELDS},
    }


#: 펀더멘털은 long 포맷이다 — 지표 하나가 행 하나. 지표가 늘어도 스키마를
#: 안 고쳐도 되고, 어떤 지표가 언제부터 존재했는지가 데이터에 남는다.
FUNDAMENTAL_METRICS = ("BPS", "PER", "PBR", "EPS", "DIV", "DPS", "market_cap", "shares")


def fundamental_rows(
    row: dict[str, Any], market: Market, valid_from: datetime, observed_at: datetime
) -> list[dict[str, Any]]:
    code = str(row.get("code") or "").strip()
    if not code:
        return []
    rows = []
    for metric in FUNDAMENTAL_METRICS:
        value = number(row.get(metric))
        if value is None:
            continue
        rows.append(
            {
                **_base(code, market, valid_from, observed_at),
                "metric": metric,
                "value": value,
                # 일별 스냅샷이라 회계기간이 없다. DART 재무가 들어오면
                # 같은 테이블에 fiscal_period 를 채운 행으로 들어온다.
                "fiscal_period": None,
                "report_type": "krx_daily",
            }
        )
    return rows


# -----------------------------------------------------------------------------
# 실행
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class PanelResult:
    day: date
    table: str
    rows: int
    skipped: bool
    error: str | None = None
    #: 아직 공표되지 않아 넘어간 세션. 실패가 아니라 "내일 다시" 다.
    deferred: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def counts(self) -> dict[str, int]:
        return {self.table: self.rows}

    @property
    def unit(self) -> str:
        return self.day.isoformat()


@dataclass
class PanelBackfiller:
    store: Store
    source: PanelSource
    clock: Clock
    archive: RawArchive
    policy: ObservedAtPolicy
    panel: Panel
    market: Market = Market.KR
    only_codes: frozenset[str] | None = None
    _fanout: dict[str, Any] = field(default_factory=dict, repr=False)

    def plan(self, start: date, end: date) -> list[date]:
        return trading_days(self.market, start, end)

    def pending(self, sessions: Sequence[date]) -> list[date]:
        return [day for day in sessions if not self._recorded(day)]

    def _recorded(self, day: date) -> bool:
        return self.store.ingest_run_recorded(
            self.panel.table, run_id_for(self.panel.table, self.market, day)
        )

    def run_session(self, day: date) -> PanelResult:
        table = self.panel.table
        if self._recorded(day):
            return PanelResult(day=day, table=table, rows=0, skipped=True)

        run_id = run_id_for(table, self.market, day)
        latency = LatencyRecorder(
            store=self.store, clock=self.clock, source=self.source.name, ingest_run_id=run_id
        )
        detail = f"{self.market}:{table}:{day.isoformat()}"
        try:
            # 관측시각을 가장 먼저 정한다. 아직 공표되지 않은 세션이면 여기서
            # 끝나고 네트워크를 건드리지도 않는다.
            observed_at = self.policy.for_session(day, extra_lag_days=self.panel.lag_days)
            valid_from = session_timestamp(day)

            with latency.stage("fetch", detail):
                payload = self.panel.fetch(self.source, day)

            with latency.stage("archive", detail):
                self.archive.save(
                    self.source.name, payload, observed_at=observed_at,
                    ingest_run_id=run_id, label=f"{table}-{day.isoformat()}",
                )

            with latency.stage("normalize", detail):
                rows = self._normalize(payload, valid_from, observed_at)

            with latency.stage("append", detail):
                if not rows:
                    # 빈 것을 완료로 기록하면 나중에 데이터가 생겨도 영영
                    # 건너뛴다. 매니페스트를 남기지 않고 그냥 0을 돌려준다.
                    return PanelResult(day=day, table=table, rows=0, skipped=False)
                try:
                    written = self.store.append(table, rows, ingest_run_id=run_id)
                except DuplicateIngestRun:
                    written = 0

            return PanelResult(day=day, table=table, rows=written, skipped=False)
        except NotYetPublished as pending:
            return PanelResult(
                day=day, table=table, rows=0, skipped=False,
                error=str(pending), deferred=True,
            )
        except CollectorError as error:
            return PanelResult(day=day, table=table, rows=0, skipped=False, error=str(error))
        finally:
            latency.flush()

    def _normalize(
        self, payload: list[dict[str, Any]], valid_from: datetime, observed_at: datetime
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for raw in payload:
            code = str(raw.get("code") or "").strip()
            if self.only_codes is not None and code not in self.only_codes:
                continue
            if self.panel.expand is not None:
                rows.extend(self.panel.expand(raw, self.market, valid_from, observed_at))
                continue
            if self.panel.normalize is None:
                raise ValueError(f"{self.panel.table} 에 normalize 도 expand 도 없다")
            row = self.panel.normalize(raw, self.market, valid_from, observed_at)
            if row is not None:
                rows.append(row)
        return rows


def openapi_shares(
    row: dict[str, Any], market: Market, valid_from: datetime, observed_at: datetime
) -> list[dict[str, Any]]:
    """일별매매 응답 → 상장주식수·시가총액 (long 포맷).

    이 둘이 없어서 fundamental 의 밸류 팩터(PER·PBR·PSR)를 못 만들고 있었다.
    시세와 같은 콜에서 나오므로 추가 호출이 없다.
    """
    from quant_rl_trading.collectors.krx_openapi import normalize_shares

    return normalize_shares(
        [row], market=str(market), valid_from=valid_from, observed_at=observed_at
    )


def openapi_index(
    row: dict[str, Any], market: Market, valid_from: datetime, observed_at: datetime
) -> list[dict[str, Any]]:
    from quant_rl_trading.collectors.krx_openapi import normalize_indices

    return normalize_indices(
        [row], market=str(market), valid_from=valid_from, observed_at=observed_at
    )


#: 사용 가능한 패널. CLI 의 --table 이 이 이름을 받는다.
PANELS: dict[str, Panel] = {
    "flows": Panel(
        table="flows",
        fetch=lambda source, day: source.flows_on(day),  # type: ignore[attr-defined]
        normalize=normalize_flow,
    ),
    "shorting": Panel(
        table="shorting",
        fetch=lambda source, day: source.shorting_on(day),  # type: ignore[attr-defined]
        normalize=normalize_shorting,
        # 실제 지연은 config 에서 덮어쓴다. 여기 0 을 두면 조용히 미래를 본다.
        lag_days=2,
    ),
    "fundamentals": Panel(
        table=FUNDAMENTALS,
        fetch=lambda source, day: source.fundamentals_on(day),  # type: ignore[attr-defined]
        expand=lambda row, market, valid_from, observed_at: fundamental_rows(
            row, market, valid_from, observed_at
        ),
    ),
    "indices": Panel(
        table="indices",
        fetch=lambda source, day: source.indices_on(day),  # type: ignore[attr-defined]
        normalize=normalize_index,
    ),
}

#: KRX **정식** Open API 로 받는 패널. pykrx 는 약관상 자동화 수집이 금지돼
#: 있으므로 새로 받는 것은 전부 이쪽을 쓴다.
OPENAPI_PANELS: dict[str, Panel] = {
    "shares": Panel(
        table="market_stats",
        fetch=lambda source, day: source.trades_on(day),  # type: ignore[attr-defined]
        expand=openapi_shares,
    ),
    "indices-krx": Panel(
        table="indices",
        fetch=lambda source, day: source.indices_on(day),  # type: ignore[attr-defined]
        expand=openapi_index,
    ),
}
