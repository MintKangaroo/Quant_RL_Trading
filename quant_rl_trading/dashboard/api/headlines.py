"""중요 시황 API — `/api/headlines`.

**모든 엔드포인트가 ``as_of`` 를 받는다** (불변식 9). 규약은 ``common.scope()``
가 지키고, 여기서는 다시 적지 않는다.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, request

from quant_rl_trading.dashboard.api.common import envelope, scope, store
from quant_rl_trading.dashboard.services import headlines as service
from quant_rl_trading.dashboard.services import schedule as schedule_service

bp = Blueprint("headlines_api", __name__, url_prefix="/api/headlines")


@bp.get("")
def overview() -> Any:
    current = scope()
    return envelope(
        current,
        service.payload(store(), as_of=current.as_of, lookback=current.lookback),
    )


@bp.get("/schedule")
def schedule() -> Any:
    """월별 일정 — 지표 발표 · 실적 발표. ``month=YYYY-MM`` (없으면 as_of 의 달)."""
    current = scope()
    days = request.args.get("days")
    if days:
        return envelope(
            current, schedule_service.upcoming(store(), as_of=current.as_of, days=int(days))
        )
    month = request.args.get("month") or None
    return envelope(
        current,
        schedule_service.month_schedule(store(), as_of=current.as_of, month=month),
    )
