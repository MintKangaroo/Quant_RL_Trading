"""Data Quality 집계.

이 화면의 목적은 보기 좋은 그래프가 아니라 **데이터가 썩는 것을 조기에 잡는
것**이다. 커버리지가 조용히 떨어지거나 결측이 늘거나 수집 지연이 길어지면 그
위의 IC·백테스트·보상이 전부 틀린 값을 낸다. 그런데 아무도 안 보면 몇 달 뒤에야
안다.

모든 조회는 ``store.get(table, as_of=...)`` 를 경유한다 (불변식 1). 모든 함수가
``as_of`` 를 받는다 — 어제 화면과 오늘 화면이 같은 과거를 그려야 한다 (불변식 9).
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from lattice.collectors.market_hours import Market, trading_days
from lattice.store import Store

PRICES = "prices"
UNIVERSE = "universe"
LATENCY = "ingest_latency"

#: 백분위. p99 까지 보는 이유는 꼬리가 실제 사고를 만들기 때문이다.
PERCENTILES = (50, 90, 99)

#: 집계 창 크기(일). 5년치를 한 번에 올리면 340만 행이라 메모리가 터진다.
WINDOW_DAYS = 32


@dataclass
class Coverage:
    """세션별 집계. 전체를 메모리에 들고 있지 않는다.

    5년치를 한 번에 올리면 340만 행이라 터진다. 창을 나눠 훑으며 세션 단위로만
    누적한다. 창이 겹치는 구간을 두 번 세지 않는 것이 유일한 주의점이다.
    """

    rows: dict[date, int] = field(default_factory=dict)
    entities_per_day: dict[date, int] = field(default_factory=dict)
    close_null: dict[date, int] = field(default_factory=dict)
    volume_null: dict[date, int] = field(default_factory=dict)
    entities: set[str] = field(default_factory=set)

    def absorb(self, frame: pd.DataFrame) -> None:
        if frame.empty:
            return
        for day, group in frame.groupby(frame["valid_from"].dt.date):
            if day in self.rows:
                continue
            self.rows[day] = len(group)
            self.entities_per_day[day] = int(group["entity_id"].nunique())
            self.close_null[day] = int(group["close"].isna().sum())
            self.volume_null[day] = int(group["volume"].isna().sum())
        self.entities.update(frame["entity_id"].astype(str))

    @property
    def total(self) -> int:
        return sum(self.rows.values())

    @property
    def sessions(self) -> list[date]:
        return sorted(self.rows)


def iter_windows(
    store: Store,
    table: str,
    *,
    as_of: datetime,
    lookback: int,
    window: int = WINDOW_DAYS,
    columns: Sequence[str] | None = None,
    market: str | None = None,
) -> Iterator[pd.DataFrame]:
    """구간을 창으로 잘라 순서대로 돌려준다. 오래된 창부터.

    창은 **``valid_from`` 축에서만** 옮긴다. ``as_of`` 는 요청받은 시점 하나로
    고정한다.

    창마다 as_of 를 옮기면 안 된다. valid_from 은 옛날이고 observed_at 이 늦게
    도착한 행(정정본)이 자기 창에서는 아직 관측되지 않았고, 다음 창에서는
    valid_from 하한 아래라 어느 창에도 안 걸린다. 그러면 게이트는 정정본을
    보는데 화면만 정정 전 값을 그리게 된다.

    화면과 CLI 가 같은 창 나누기를 쓴다. 두 벌로 두면 같은 데이터에서 서로 다른
    커버리지 숫자가 나오고, 어느 쪽이 맞는지 아무도 모르게 된다.

    ``columns`` 는 게이트에 그대로 넘긴다. 창을 나누는 이유가 메모리인데
    창마다 전 컬럼을 퍼오면 나눈 보람이 없다.
    """
    for edge in _window_edges(as_of, lookback, window):
        yield store.get(
            table,
            as_of=as_of,
            lookback=_span(as_of, edge, window),
            until=edge,
            columns=columns,
            market=market,
        )


def _span(as_of: datetime, edge: datetime, window: int) -> int:
    """창 하한을 ``as_of`` 기준 일수로 바꾼다.

    ``lookback`` 은 as_of 로부터 거슬러 세므로, 창의 왼쪽 끝을 그 축으로 옮겨야
    한다. 하루 올려 잡는 것은 경계에서 잘리지 않게 하기 위함이다 — 창이 조금
    겹치는 것은 ``Coverage`` 가 세션 단위로 중복을 걸러 준다.
    """
    left = edge - timedelta(days=window)
    return max(int((as_of - left).days) + 1, 1)


def collect_coverage(
    store: Store, *, as_of: datetime, lookback: int, window: int = WINDOW_DAYS
) -> Coverage:
    coverage = Coverage()
    for frame in iter_windows(store, PRICES, as_of=as_of, lookback=lookback, window=window):
        coverage.absorb(frame)
    return coverage


def _window_edges(as_of: datetime, lookback: int, window: int) -> list[datetime]:
    """창의 오른쪽 경계들. 오래된 것부터.

    ``get(as_of=edge, lookback=window)`` 는 ``[edge - window, edge]`` 를 준다.
    경계를 ``window`` 만큼씩 옮기면 창이 정확히 이어 붙는다.
    """
    edges: list[datetime] = []
    cursor = as_of - timedelta(days=max(lookback, 1)) + timedelta(days=window)
    while cursor < as_of:
        edges.append(cursor)
        cursor += timedelta(days=window)
    edges.append(as_of)
    return edges


# -----------------------------------------------------------------------------
# 화면이 부르는 것들
# -----------------------------------------------------------------------------


def coverage_series(
    store: Store, *, as_of: datetime, lookback: int, market: Market = Market.KR
) -> dict[str, Any]:
    """일별 종목 수와 기대 거래일 대비 커버리지."""
    coverage = collect_coverage(store, as_of=as_of, lookback=lookback)
    sessions = coverage.sessions
    if not sessions:
        return {"points": [], "expected_sessions": 0, "covered_sessions": 0, "ratio": None}

    expected = trading_days(market, sessions[0], sessions[-1])
    missing = [day for day in expected if day not in coverage.rows]
    return {
        "points": [
            {
                "session": day.isoformat(),
                "entities": coverage.entities_per_day[day],
                "rows": coverage.rows[day],
            }
            for day in sessions
        ],
        "expected_sessions": len(expected),
        "covered_sessions": len(sessions),
        "ratio": len(sessions) / len(expected) if expected else None,
        "missing_sessions": [day.isoformat() for day in missing],
        "entities_total": len(coverage.entities),
        "rows_total": coverage.total,
    }


def missing_series(store: Store, *, as_of: datetime, lookback: int) -> dict[str, Any]:
    """일별 결측률.

    결측을 비율로 본다. 절대 건수는 종목 수가 늘면 같이 늘어서 추세를 못 읽는다.
    """
    coverage = collect_coverage(store, as_of=as_of, lookback=lookback)
    points = [
        {
            "session": day.isoformat(),
            "close": coverage.close_null[day] / coverage.rows[day],
            "volume": coverage.volume_null[day] / coverage.rows[day],
        }
        for day in coverage.sessions
        if coverage.rows[day]
    ]
    total = coverage.total
    return {
        "points": points,
        "close_rate": sum(coverage.close_null.values()) / total if total else None,
        "volume_rate": sum(coverage.volume_null.values()) / total if total else None,
    }


def universe_series(store: Store, *, as_of: datetime, lookback: int) -> dict[str, Any]:
    """일별 상장 종목 수와 상장폐지 누적.

    상폐 수가 0으로 눌러앉으면 생존편향이 다시 들어왔다는 뜻이다. 그래서 이
    숫자를 상시 띄운다.
    """
    frame = store.get(UNIVERSE, as_of=as_of, lookback=lookback)
    if frame.empty:
        return {"points": [], "listed_now": 0, "delisted_total": 0}

    listed = frame["is_listed"].astype(bool)
    sessions = frame["valid_from"].dt.date
    by_day = (
        pd.DataFrame({"session": sessions, "listed": listed})
        .groupby("session")["listed"]
        .agg(listed="sum", delisted=lambda column: int((~column).sum()))
        .reset_index()
    )

    cumulative = 0
    points: list[dict[str, Any]] = []
    for row in by_day.to_dict(orient="records"):
        cumulative += int(row["delisted"])
        points.append(
            {
                "session": row["session"].isoformat(),
                "listed": int(row["listed"]),
                "delisted_cumulative": cumulative,
            }
        )

    return {
        "points": points,
        "listed_now": points[-1]["listed"] if points else 0,
        "delisted_total": cumulative,
    }


def latency_percentiles(
    store: Store, *, as_of: datetime, lookback: int
) -> dict[str, Any]:
    """단계별 지연 분포.

    백테스트가 쓰는 지연은 이 **실측의 p90** 이다. 가정한 지연과 실제 지연의
    차이가 백테스트를 거짓말로 만드는 대표적 원인이다 (data-contract §5).
    실패한 호출의 지연도 포함한다 — 느려서 실패한 호출이야말로 지연 예산이
    알아야 할 사건이다.
    """
    frame = store.get(LATENCY, as_of=as_of, lookback=lookback)
    if frame.empty:
        return {"stages": [], "overall": {}, "samples": 0}

    stages: list[dict[str, Any]] = []
    for stage, group in frame.groupby("stage"):
        elapsed = group["elapsed_ms"].astype(float)
        stages.append(
            {
                "stage": str(stage),
                "samples": len(group),
                "ok_rate": float(group["ok"].astype(bool).mean()),
                **{f"p{p}": float(elapsed.quantile(p / 100)) for p in PERCENTILES},
            }
        )

    elapsed = frame["elapsed_ms"].astype(float)
    return {
        "stages": sorted(stages, key=lambda item: -float(item["p90"])),
        "overall": {f"p{p}": float(elapsed.quantile(p / 100)) for p in PERCENTILES},
        "samples": len(frame),
        "sources": sorted({str(value) for value in frame["entity_id"]}),
    }


def recent_failures(
    store: Store, *, as_of: datetime, lookback: int, limit: int
) -> list[dict[str, Any]]:
    """최근 수집 실패. 조용히 실패하는 파이프라인이 가장 위험하다."""
    frame = store.get(LATENCY, as_of=as_of, lookback=lookback)
    if frame.empty:
        return []

    failed = frame[~frame["ok"].astype(bool)].sort_values("observed_at", ascending=False)
    return [
        {
            "observed_at": row["observed_at"].isoformat(),
            "source": str(row["entity_id"]),
            "stage": str(row["stage"]),
            "elapsed_ms": float(row["elapsed_ms"]),
            "detail": str(row["detail"] or ""),
        }
        for row in failed.head(limit).to_dict(orient="records")
    ]


def summary(
    store: Store,
    *,
    as_of: datetime,
    lookback: int,
    thresholds: dict[str, Any],
    market: Market = Market.KR,
) -> dict[str, Any]:
    """KPI 스트립. 경고 판정도 여기서 한다 — 화면이 임계치를 따로 들면
    설정과 어긋난다 (불변식 10)."""
    cov = coverage_series(store, as_of=as_of, lookback=lookback, market=market)
    miss = missing_series(store, as_of=as_of, lookback=lookback)
    lat = latency_percentiles(store, as_of=as_of, lookback=lookback)
    uni = universe_series(store, as_of=as_of, lookback=lookback)
    failures = recent_failures(
        store, as_of=as_of, lookback=lookback, limit=int(thresholds["failure_rows"])
    )

    latency_p90 = lat["overall"].get("p90")
    return {
        "coverage_ratio": cov["ratio"],
        "covered_sessions": cov["covered_sessions"],
        "expected_sessions": cov["expected_sessions"],
        "entities_total": cov.get("entities_total", 0),
        "rows_total": cov.get("rows_total", 0),
        "missing_close_rate": miss["close_rate"],
        "missing_volume_rate": miss["volume_rate"],
        "latency_p50_ms": lat["overall"].get("p50"),
        "latency_p90_ms": latency_p90,
        "latency_samples": lat["samples"],
        "listed_now": uni["listed_now"],
        "delisted_total": uni["delisted_total"],
        "failure_count": len(failures),
        "warnings": _warnings(cov, miss, latency_p90, uni, thresholds),
    }


def _warnings(
    cov: dict[str, Any],
    miss: dict[str, Any],
    latency_p90: float | None,
    uni: dict[str, Any],
    thresholds: dict[str, Any],
) -> list[str]:
    found: list[str] = []
    ratio = cov["ratio"]
    if ratio is not None and ratio < float(thresholds["coverage_warn"]):
        found.append(
            f"커버리지 {ratio:.1%} — 임계치 {float(thresholds['coverage_warn']):.0%} 미만"
        )
    for label, key in (("close", "close_rate"), ("volume", "volume_rate")):
        rate = miss[key]
        if rate is not None and rate > float(thresholds["missing_warn"]):
            found.append(f"{label} 결측률 {rate:.2%} — 임계치 초과")
    if latency_p90 is not None and latency_p90 > float(thresholds["latency_p90_warn_ms"]):
        found.append(f"수집 지연 p90 {latency_p90 / 1000:.1f}초 — 임계치 초과")
    if cov["covered_sessions"] and not uni["delisted_total"]:
        found.append("상장폐지 감지 0건 — 생존편향이 다시 들어왔을 수 있다")
    return found


__all__ = [
    "Coverage",
    "collect_coverage",
    "coverage_series",
    "latency_percentiles",
    "missing_series",
    "recent_failures",
    "summary",
    "universe_series",
]
