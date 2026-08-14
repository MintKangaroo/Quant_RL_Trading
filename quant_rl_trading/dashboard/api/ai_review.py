"""AI 리뷰 탭 엔드포인트.

Data Quality 와 **같은 규약**(``api/common.py``)을 쓴다.

``tests/dashboard/test_data_quality_api.py::test_every_api_route_accepts_as_of``
가 URL map 을 훑으므로, 여기 추가한 라우트도 자동으로 불변식 9 검사를 받는다.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint

from quant_rl_trading.dashboard.api.common import envelope, scope, store
from quant_rl_trading.dashboard.services import ai_review as service

bp = Blueprint("ai_review_api", __name__, url_prefix="/api/ai-review")


@bp.get("/summary")
def summary() -> Any:
    current = scope()
    return envelope(
        current,
        service.summary(store(), as_of=current.as_of, lookback=current.lookback),
    )


@bp.get("/calls")
def calls() -> Any:
    current = scope()
    return envelope(
        current,
        {
            "agents": service.agent_activity(
                store(), as_of=current.as_of, lookback=current.lookback
            ),
            "recent": service.recent_calls(
                store(), as_of=current.as_of, lookback=current.lookback
            ),
        },
    )


@bp.get("/costs")
def costs() -> Any:
    current = scope()
    return envelope(
        current,
        service.cost_activity(store(), as_of=current.as_of, lookback=current.lookback),
    )


@bp.get("/verdicts")
def verdicts() -> Any:
    current = scope()
    return envelope(
        current,
        service.verdict_activity(store(), as_of=current.as_of, lookback=current.lookback),
    )


@bp.get("/documents")
def documents() -> Any:
    current = scope()
    return envelope(
        current,
        service.document_activity(store(), as_of=current.as_of, lookback=current.lookback),
    )
