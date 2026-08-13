"""Agent Health 엔드포인트.

Data Quality 와 **같은 규약**(``api/common.py``)을 쓴다. 화면마다 규약을 새로
만들면 as_of 를 빠뜨린 엔드포인트가 반드시 생기고, 그 사실은 몇 달 뒤
이상한 백테스트로만 드러난다.

``tests/dashboard/test_data_quality_api.py::test_every_api_route_accepts_as_of``
가 URL map 을 훑으므로, 여기 추가한 라우트도 자동으로 불변식 9 검사를 받는다.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint

from quant_rl_trading.dashboard.api.common import envelope, scope, store
from quant_rl_trading.dashboard.services import agent_health as service

bp = Blueprint("agent_health_api", __name__, url_prefix="/api/agent-health")


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
        ),
    )


@bp.get("/roster")
def roster() -> Any:
    current = scope()
    return envelope(
        current, service.roster(store(), as_of=current.as_of, lookback=current.lookback)
    )


@bp.get("/ic-history")
def ic_history() -> Any:
    current = scope()
    return envelope(
        current,
        service.ic_history(store(), as_of=current.as_of, lookback=current.lookback),
    )


@bp.get("/signals")
def signals() -> Any:
    current = scope()
    return envelope(
        current,
        service.signal_activity(store(), as_of=current.as_of, lookback=current.lookback),
    )


@bp.get("/verdicts")
def verdicts() -> Any:
    current = scope()
    return envelope(
        current,
        service.verdict_scorecard(store(), as_of=current.as_of, lookback=current.lookback),
    )
