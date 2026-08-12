"""과거 5년치 백필 엔진.

작업 단위는 종목이 아니라 **거래일 하나**다. 소스가 날짜 축으로 전종목을
한 번에 주기 때문이고, 그 덕분에 그날 상장돼 있던 종목이 자동으로 전부 잡힌다
— 지금은 상장폐지된 종목까지. 종목 축으로 돌면 "오늘 존재하는 종목 목록"에서
출발하게 되고, 그 순간 생존편향이 데이터에 새겨진다.

**재개**의 진실의 원천은 체크포인트 파일이 아니라 창고의 매니페스트다.
``store.ingest_run_recorded(table, run_id)`` 가 True 면 그 세션은 이미 들어간
것이다. run id 가 ``bf-prices-KR-20240304`` 처럼 결정론적이라 성립한다.
체크포인트 파일이 통째로 날아가도 재개는 정확하다 — 파일은 진행률과 실패
사유를 사람이 보기 위한 것이지 정합성을 위한 것이 아니다.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from lattice.collectors.errors import CollectorError
from lattice.collectors.krx_source import HistoricalSource
from lattice.collectors.latency import LatencyRecorder
from lattice.collectors.market_hours import Market, trading_days
from lattice.collectors.publication import NotYetPublished, ObservedAtPolicy
from lattice.collectors.raw import RawArchive
from lattice.replay.clock import Clock
from lattice.store import DuplicateIngestRun, Store

PRICES = "prices"
UNIVERSE = "universe"

#: 진행 로그. append-only — 실패한 시도의 기록도 지우지 않는다.
PROGRESS_DIR = "backfill"
PROGRESS_FILE = "progress.jsonl"


def session_run_id(table: str, market: Market, day: date) -> str:
    """결정론적 run id. 같은 세션을 두 번 넣으려 하면 창고가 거부한다."""
    return f"bf-{table}-{market}-{day.strftime('%Y%m%d')}"


def _entity(market: Market, code: str) -> str:
    return f"{market}:{code}"


def _session_timestamp(day: date) -> datetime:
    """거래일을 valid_from 으로. UTC 자정 고정 — 지역 자정을 쓰면 하루가 밀린다."""
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


@dataclass(frozen=True)
class SessionResult:
    day: date
    prices: int
    universe: int
    skipped: bool
    error: str | None = None
    #: 아직 공표되지 않아 넘어간 세션. **실패가 아니다.**
    #: 오늘 장이 안 끝났거나 공매도 T+2 가 안 지난 것뿐이고, 내일 다시 돌리면
    #: 들어온다. 이걸 실패로 세면 매 실행이 거짓 경보로 끝나고, 거짓 경보가
    #: 일상이 되면 진짜 실패를 아무도 안 본다.
    deferred: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def counts(self) -> dict[str, int]:
        """테이블별 적재 행수. 진행 출력과 집계가 이것만 본다.

        패널 백필과 모양을 맞추기 위한 것이다 — 출력 코드가 결과 종류마다
        갈라지면 새 테이블을 추가할 때마다 CLI 를 고쳐야 한다.
        """
        return {PRICES: self.prices, UNIVERSE: self.universe}

    @property
    def unit(self) -> str:
        """작업 단위의 이름. 세션 축은 날짜, 종목 축은 종목코드다."""
        return self.day.isoformat()


@dataclass
class BackfillReport:
    market: Market
    sessions: int = 0
    skipped: int = 0
    deferred: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    failures: list[tuple[str, str]] = field(default_factory=list)

    def absorb(self, result: Any) -> None:
        self.sessions += 1
        if result.skipped:
            self.skipped += 1
        if getattr(result, "deferred", False):
            self.deferred += 1
            return
        for table, rows in result.counts.items():
            self.counts[table] = self.counts.get(table, 0) + rows
        if result.error is not None:
            # ``unit`` 을 쓴다. 세션 축은 날짜, 종목 축은 종목코드라 결과 종류에
            # 따라 필드가 다르다 — 여기서 날짜를 가정하면 종목 백필이 **실패를
            # 기록하려다** 죽는다. 실제로 84번째 종목에서 그렇게 죽었다.
            self.failures.append((result.unit, result.error))

    def render_counts(self) -> str:
        return ", ".join(f"{table} {rows:,}행" for table, rows in sorted(self.counts.items()))


@dataclass
class Backfiller:
    """세션 하나를 읽어 curated 에 적재한다. 그 이상은 하지 않는다."""

    store: Store
    source: HistoricalSource
    clock: Clock
    archive: RawArchive
    policy: ObservedAtPolicy
    market: Market = Market.KR
    #: 시험 실행용. None 이면 전종목.
    only_codes: frozenset[str] | None = None
    #: 직전 세션까지 상장돼 있던 종목. 상폐 감지에 쓴다.
    _listed: set[str] | None = field(default=None, repr=False)
    #: 종목명. 상폐 행에는 그날 명단에 없는 종목의 이름이 필요하다.
    _names: dict[str, str] = field(default_factory=dict, repr=False)

    # -- 계획 -----------------------------------------------------------------

    def plan(self, start: date, end: date) -> list[date]:
        return trading_days(self.market, start, end)

    def pending(self, sessions: Sequence[date]) -> list[date]:
        """아직 안 들어간 세션만. 재개는 이 목록에서 시작한다."""
        return [day for day in sessions if not self._recorded(day)]

    def _recorded(self, day: date) -> bool:
        return self.store.ingest_run_recorded(
            PRICES, session_run_id(PRICES, self.market, day)
        ) and self.store.ingest_run_recorded(
            UNIVERSE, session_run_id(UNIVERSE, self.market, day)
        )

    # -- 실행 -----------------------------------------------------------------

    def run(self, sessions: Sequence[date]) -> Iterator[SessionResult]:
        for day in sessions:
            yield self.run_session(day)

    def run_session(self, day: date) -> SessionResult:
        if self._recorded(day):
            return SessionResult(day=day, prices=0, universe=0, skipped=True)

        run_id = session_run_id(PRICES, self.market, day)
        latency = LatencyRecorder(
            store=self.store,
            clock=self.clock,
            source=self.source.name,
            ingest_run_id=run_id,
        )
        try:
            # 관측시각을 가장 먼저 정한다. 아직 공표되지 않은 세션이면
            # 여기서 끝나고 네트워크를 건드리지도 않는다.
            observed_at = self.policy.for_session(day)

            with latency.stage("fetch", f"{self.market}:{day.isoformat()}"):
                listed = self.source.listed_on(day)
                bars = self.source.ohlcv_on(day)

            with latency.stage("archive", f"{self.market}:{day.isoformat()}"):
                self.archive.save(
                    self.source.name,
                    {"listed": listed, "ohlcv": bars},
                    observed_at=observed_at,
                    ingest_run_id=run_id,
                    label=f"session-{day.isoformat()}",
                )

            with latency.stage("normalize", f"{self.market}:{day.isoformat()}"):
                price_rows = self._price_rows(bars, day, observed_at)
                universe_rows = self._universe_rows(listed, day, observed_at)

            with latency.stage("append", f"{self.market}:{day.isoformat()}"):
                prices = self._append(PRICES, price_rows, day)
                universe = self._append(UNIVERSE, universe_rows, day)

            return SessionResult(day=day, prices=prices, universe=universe, skipped=False)
        except NotYetPublished as pending:
            return SessionResult(
                day=day, prices=0, universe=0, skipped=False,
                error=str(pending), deferred=True,
            )
        except CollectorError as error:
            return SessionResult(day=day, prices=0, universe=0, skipped=False, error=str(error))
        finally:
            latency.flush()

    def _append(self, table: str, rows: list[dict[str, Any]], day: date) -> int:
        run_id = session_run_id(table, self.market, day)
        try:
            return self.store.append(table, rows, ingest_run_id=run_id)
        except DuplicateIngestRun:
            # prices 는 들어갔는데 universe 직전에 죽은 경우가 여기 걸린다.
            # 이미 있는 것은 이미 있는 것이다. 덮어쓰지 않는다 (불변식 4).
            return 0

    # -- 정규화 ---------------------------------------------------------------

    def _keep(self, code: str) -> bool:
        return self.only_codes is None or code in self.only_codes

    def _price_rows(
        self, bars: list[dict[str, Any]], day: date, observed_at: datetime
    ) -> list[dict[str, Any]]:
        valid_from = _session_timestamp(day)
        rows: list[dict[str, Any]] = []
        for bar in bars:
            code = str(bar.get("code") or "").strip()
            if not code or not self._keep(code):
                continue
            rows.append(
                {
                    "entity_id": _entity(self.market, code),
                    "valid_from": valid_from,
                    "observed_at": observed_at,
                    "source": self.source.name,
                    "market": str(self.market),
                    "open": _number(bar.get("open")),
                    "high": _number(bar.get("high")),
                    "low": _number(bar.get("low")),
                    "close": _number(bar.get("close")),
                    "volume": _number(bar.get("volume")),
                    "value": _number(bar.get("value")),
                    # 조정계수는 발효일과 함께 별도로 관리한다. 수정주가를
                    # 저장하지 않는 이유와 같다 — 미래의 분할이 섞이면 안 된다.
                    "adj_factor": None,
                }
            )
        return rows

    def _universe_rows(
        self, listed: list[dict[str, Any]], day: date, observed_at: datetime
    ) -> list[dict[str, Any]]:
        """그날 명단 + **직전 세션 대비 사라진 종목의 상폐 행**.

        상폐일 목록을 따로 받아오지 않는다. 어제 명단에 있고 오늘 없으면 그것이
        상장폐지다. 스냅샷 자체에서 유도하므로 외부 소스와 어긋날 여지가 없다.
        """
        valid_from = _session_timestamp(day)
        rows: list[dict[str, Any]] = []
        today: set[str] = set()

        for item in listed:
            code = str(item.get("code") or "").strip()
            if not code or not self._keep(code):
                continue
            entity = _entity(self.market, code)
            today.add(entity)
            name = str(item.get("name") or code)
            rows.append(
                {
                    "entity_id": entity,
                    "valid_from": valid_from,
                    "observed_at": observed_at,
                    "source": self.source.name,
                    "market": str(self.market),
                    "name": name,
                    "is_listed": True,
                    # 스팩은 상장돼 있어도 우리가 살 대상이 아니다. 데이터
                    # 유니버스와 매매 유니버스는 다르다 (data-contract §6).
                    "is_tradable": "스팩" not in name,
                    "delisted_on": None,
                }
            )

        previous = self._known_listed(observed_at)
        for entity in sorted(previous - today):
            rows.append(
                {
                    "entity_id": entity,
                    "valid_from": valid_from,
                    "observed_at": observed_at,
                    "source": self.source.name,
                    "market": str(self.market),
                    "name": self._names.get(entity, entity),
                    "is_listed": False,
                    "is_tradable": False,
                    "delisted_on": valid_from,
                }
            )

        self._listed = today
        self._names.update(
            {row["entity_id"]: str(row["name"]) for row in rows if row["is_listed"]}
        )
        return rows

    def _known_listed(self, observed_at: datetime) -> set[str]:
        """직전 세션까지 상장 상태이던 종목.

        런 안에서는 메모리로 이어받고, 런의 첫 세션에서만 창고를 읽는다.
        세션마다 조회하면 파티션이 늘어날수록 느려진다.
        """
        if self._listed is not None:
            return self._listed

        frame = self.store.get(UNIVERSE, as_of=observed_at)
        if frame.empty:
            self._listed = set()
            return self._listed

        # 창고는 세션마다 한 행씩 돌려준다. 종목의 **현재 상태**는 그중 가장
        # 늦은 valid_from 의 행이다. 정렬 없이 읽으면 상폐된 종목이 옛 행 때문에
        # 상장중으로 되살아난다.
        latest = frame.sort_values("valid_from").groupby("entity_id").tail(1)
        records = latest.to_dict(orient="records")
        self._names = {str(row["entity_id"]): str(row["name"]) for row in records}
        self._listed = {
            str(row["entity_id"]) for row in records if bool(row["is_listed"])
        }
        return self._listed


# -----------------------------------------------------------------------------
# 진행 기록
# -----------------------------------------------------------------------------


@dataclass
class ProgressLog:
    """append-only 진행 로그. 정합성이 아니라 사람이 읽기 위한 것이다."""

    root: Path
    plan_id: str

    @property
    def path(self) -> Path:
        return self.root / PROGRESS_DIR / self.plan_id / PROGRESS_FILE

    def record(self, result: Any, *, at: datetime) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            {
                "at": at.isoformat(),
                # 세션 축 백필은 날짜, 종목 축(수급)은 종목코드다.
                "unit": result.unit,
                "counts": result.counts,
                "skipped": result.skipped,
                "error": result.error,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(f"{line}\n")


def eta(done: int, total: int, elapsed: timedelta) -> timedelta | None:
    """남은 시간 추정. 표본이 없으면 추정하지 않는다."""
    if done <= 0 or done >= total:
        return None
    return (elapsed / done) * (total - done)


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
