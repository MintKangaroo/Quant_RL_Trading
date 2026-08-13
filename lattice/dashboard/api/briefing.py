"""뉴스·일정 엔드포인트.

핸들러는 얇다 — 규약은 ``common.scope()`` 가, 계산은 ``services/briefing.py``
가 한다. **모든 엔드포인트가 ``as_of`` 를 받는다** (불변식 9): "실시간" 은
``as_of`` 를 안 준 경우이지, 규약의 예외가 아니다.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint

from lattice.analysts.macro_brief import MacroBrief
from lattice.dashboard.api.common import clock, envelope, scope, store
from lattice.dashboard.services import briefing as service

bp = Blueprint("briefing_api", __name__, url_prefix="/api/briefing")


@bp.get("/summary")
def summary() -> Any:
    current = scope()
    data = service.collect_briefing(
        store(), as_of=current.as_of, lookback=current.lookback
    ).as_dict()
    data["next"] = service.next_release(data["upcoming"], as_of=current.as_of)
    return envelope(current, data)


@bp.get("/explain")
def explain() -> Any:
    """발표 완료 건에 서프라이즈·시장반응·해설을 붙여 돌려준다.

    해설이 실패해도 **숫자는 살아남는다** — Claude 가 없다고 화면이 비면
    안 되기 때문이다 (macro_brief 모듈 docstring).
    """
    current = scope()
    released, _ = service.macro_calendar(store(), as_of=current.as_of)
    brief = MacroBrief.from_env(store(), clock())
    return envelope(
        current,
        {
            "releases": brief.explain(released, as_of=current.as_of),
            "warnings": brief.failures,
        },
    )


@bp.get("/news")
def news() -> Any:
    current = scope()
    return envelope(
        current,
        service.latest_news(store(), as_of=current.as_of, lookback=current.lookback),
    )


@bp.get("/calendar")
def calendar() -> Any:
    current = scope()
    released, upcoming = service.macro_calendar(store(), as_of=current.as_of)
    return envelope(
        current,
        {
            "released": released,
            "upcoming": upcoming,
            "next": service.next_release(upcoming, as_of=current.as_of),
        },
    )
