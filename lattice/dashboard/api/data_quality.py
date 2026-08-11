"""Data Quality 엔드포인트 6개.

핸들러는 얇다 — 규약은 ``common.scope()`` 가, 계산은 ``services/data_quality.py``
가 한다. CLI 와 화면이 같은 함수를 부르게 하려면 계산이 Flask 를 몰라야 한다.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint

from lattice.collectors.market_hours import Market
from lattice.dashboard.api.common import envelope, scope, store
from lattice.dashboard.services import data_quality as service

bp = Blueprint("data_quality_api", __name__, url_prefix="/api/data-quality")


@bp.get("/summary")
def summary() -> Any:
    current = scope()
    return envelope(
        current,
        service.summary(
            store(),
            as_of=current.as_of,
            lookback=current.lookback,
            thresholds=current.thresholds,
            market=Market.KR,
        ),
    )


@bp.get("/coverage")
def coverage() -> Any:
    current = scope()
    return envelope(
        current,
        service.coverage_series(
            store(), as_of=current.as_of, lookback=current.lookback, market=Market.KR
        ),
    )


@bp.get("/missing")
def missing() -> Any:
    current = scope()
    return envelope(
        current,
        service.missing_series(store(), as_of=current.as_of, lookback=current.lookback),
    )


@bp.get("/latency")
def latency() -> Any:
    current = scope()
    return envelope(
        current,
        service.latency_percentiles(
            store(), as_of=current.as_of, lookback=current.lookback
        ),
    )


@bp.get("/universe")
def universe() -> Any:
    current = scope()
    return envelope(
        current,
        service.universe_series(store(), as_of=current.as_of, lookback=current.lookback),
    )


@bp.get("/failures")
def failures() -> Any:
    current = scope()
    return envelope(
        current,
        service.recent_failures(
            store(),
            as_of=current.as_of,
            lookback=current.lookback,
            limit=int(current.thresholds["failure_rows"]),
        ),
    )
