"""시스템 탭 엔드포인트.

Data Quality 와 **같은 규약**(``api/common.py``)을 쓴다. 핸들러는 얇다 —
규약은 ``common.scope()`` 가, 계산은 ``services/system.py`` 가 한다.

``api/common.py`` 의 ``THRESHOLD_KEYS`` 는 data_quality 것만 든다. 공용
파일은 건드리지 않는다 — 이 화면이 쓰는 임계치(``system`` 섹션)는 여기서
``store().config("system", ...)`` 로 직접 읽는다. ``services/trading.py`` 가
``killswitch`` 섹션을 같은 방식으로 읽는 것과 같은 패턴이다.

수집 지연은 새로 만들지 않는다. ``services/data_quality.latency_percentiles``
가 이미 ``ingest_latency`` 를 실측하므로 그대로 재사용한다 — 같은 숫자를
두 곳에서 따로 계산하면 반드시 어긋난다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from flask import Blueprint

from quant_rl_trading.dashboard.api.common import envelope, scope, store
from quant_rl_trading.dashboard.services import data_quality
from quant_rl_trading.dashboard.services import system as service

bp = Blueprint("system", __name__, url_prefix="/api/system")


def _system_thresholds(as_of: datetime) -> dict[str, Any]:
    return store().config("system", as_of=as_of)  # type: ignore[no-any-return]


@bp.get("/summary")
def summary() -> Any:
    current = scope()
    return envelope(
        current,
        service.summary(
            store(),
            as_of=current.as_of,
            lookback=current.lookback,
            thresholds=_system_thresholds(current.as_of),
        ),
    )


@bp.get("/jobs")
def jobs() -> Any:
    """crontab 작업 이력. **as_of 로 되감기지 않는다** — 운영 로그이지 창고가
    아니다 (services/system.py 모듈 docstring). 규약대로 받아 되돌려만 준다."""
    current = scope()
    return envelope(current, service.job_history(store()))


@bp.get("/tables")
def tables() -> Any:
    current = scope()
    return envelope(current, service.table_freshness(store(), as_of=current.as_of))


@bp.get("/latency")
def latency() -> Any:
    current = scope()
    return envelope(
        current,
        data_quality.latency_percentiles(store(), as_of=current.as_of, lookback=current.lookback),
    )


@bp.get("/cache")
def cache() -> Any:
    current = scope()
    thresholds = _system_thresholds(current.as_of)
    return envelope(
        current,
        service.cache_stats(
            store(), as_of=current.as_of, lookback=int(thresholds["cache_lookback_days"])
        ),
    )


@bp.get("/safety")
def safety() -> Any:
    current = scope()
    return envelope(current, service.safety_summary(store(), as_of=current.as_of))


@bp.get("/resources")
def resources() -> Any:
    """CPU·메모리·디스크. **as_of 로 되감기지 않는다** — 이 기계 자체의 지금
    상태이지 창고의 과거 사실이 아니다(services/system.py 모듈 docstring)."""
    current = scope()
    return envelope(current, service.server_resources(store().root))


@bp.get("/processes")
def processes() -> Any:
    """이 프로젝트 아래에서 도는 프로세스. **as_of 로 되감기지 않는다.**"""
    current = scope()
    return envelope(current, service.project_processes(store().root))
