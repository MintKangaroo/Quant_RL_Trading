"""학습 탭 엔드포인트.

다른 화면과 **같은 규약**(``api/common.py``)을 쓴다. M4(RL) 가 아직 없다고
``as_of`` 를 빠뜨리면, M4 가 붙는 날 이 화면만 타임머신을 못 타게 된다.

``tests/dashboard/test_data_quality_api.py::test_every_api_route_accepts_as_of``
가 URL map 을 훑으므로, 여기 추가한 라우트도 자동으로 불변식 9 검사를 받는다.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint

from quant_rl_trading.dashboard.api.common import envelope, scope, store
from quant_rl_trading.dashboard.services import learning as service

bp = Blueprint("learning_api", __name__, url_prefix="/api/learning")


@bp.get("/status")
def status() -> Any:
    current = scope()
    return envelope(current, service.m4_status())


@bp.get("/gate")
def gate() -> Any:
    current = scope()
    return envelope(
        current,
        service.analyst_gate(store(), as_of=current.as_of, lookback=current.lookback),
    )


@bp.get("/ic-history")
def ic_history() -> Any:
    current = scope()
    return envelope(
        current,
        service.ic_history(store(), as_of=current.as_of, lookback=current.lookback),
    )


@bp.get("/training-runs")
def training_runs() -> Any:
    """PPO 학습 지표. 학습을 안 돌렸으면 ``has_data: false`` 로 온다."""
    current = scope()
    return envelope(
        current,
        service.training_runs(store(), as_of=current.as_of, lookback=current.lookback),
    )


@bp.get("/research-ledger")
def research_ledger() -> Any:
    """자기개선 시행 대장 (self-improvement.md §7) — 누적 시행·예산·금고·DSR."""
    current = scope()
    return envelope(
        current,
        service.research_ledger(store(), as_of=current.as_of, lookback=current.lookback),
    )


@bp.get("/walk-forward")
def walk_forward() -> Any:
    current = scope()
    return envelope(
        current,
        service.walk_forward_comparison(store(), as_of=current.as_of, lookback=current.lookback),
    )
