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
    return envelope(
        current,
        service.m4_status(store(), as_of=current.as_of, lookback=current.lookback),
    )


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


@bp.get("/evaluations")
def evaluations() -> Any:
    """정책 OOS 평가(rl_evaluations). 평가를 안 돌렸으면 ``has_data: false``."""
    current = scope()
    return envelope(
        current,
        service.evaluations(store(), as_of=current.as_of, lookback=current.lookback),
    )


@bp.get("/curriculum")
def curriculum() -> Any:
    """훈련 단계 C0~C5 진행도 (rl-training.md §6)."""
    current = scope()
    return envelope(
        current,
        service.curriculum(store(), as_of=current.as_of, lookback=current.lookback),
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


@bp.get("/research-jobs")
def research_jobs() -> Any:
    """지금 도는 연구 스크립트와 최근 연구 로그. 창고가 아니라 /proc·logs 라 as_of 를 안 받는다
    (시스템 탭 프로세스 목록과 같은 이유 — 되감기지 않는 '지금' 이다)."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    return envelope(scope(), service.research_jobs(root))
