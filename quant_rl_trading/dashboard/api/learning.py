"""학습 탭 엔드포인트.

다른 화면과 **같은 규약**(``api/common.py``)을 쓴다. M4(RL) 가 아직 없다고
``as_of`` 를 빠뜨리면, M4 가 붙는 날 이 화면만 타임머신을 못 타게 된다.

``tests/dashboard/test_data_quality_api.py::test_every_api_route_accepts_as_of``
가 URL map 을 훑으므로, 여기 추가한 라우트도 자동으로 불변식 9 검사를 받는다.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, request
from werkzeug.exceptions import BadRequest

from quant_rl_trading.dashboard.api.common import envelope, research_store, scope, store
from quant_rl_trading.dashboard.services import learning as service
from quant_rl_trading.dashboard.services import model_story as service_story

bp = Blueprint("learning_api", __name__, url_prefix="/api/learning")


def _market() -> str:
    value = request.args.get("market", "KR").upper()
    if value not in ("KR", "US", "ALL"):
        raise BadRequest("market은 KR, US, ALL 중 하나여야 한다")
    return value


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
        service.analyst_gate(
            store(), as_of=current.as_of, lookback=current.lookback, market=_market(),
        ),
    )


@bp.get("/ic-history")
def ic_history() -> Any:
    current = scope()
    return envelope(
        current,
        service.ic_history(
            store(), as_of=current.as_of, lookback=current.lookback, market=_market(),
        ),
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


def _running_scripts(live: bool) -> set[str] | None:
    """라이브일 때만 지금 도는 연구 스크립트 이름들(/proc). 되감은 화면은 None — 그때 무엇이 돌았는지 모른다."""
    if not live:
        return None
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    return {str(p.get("script", "")).rsplit("/", 1)[-1] for p in service.research_jobs(root)["running"]}


@bp.get("/final-round")
def final_round() -> Any:
    """마지막 모델 회차(BE·BF·BG·D1·C0·C1)의 **학습 진행**. 판정 창 수익·IC 는 담기지 않는다 —
    판정이 끝난 시행은 research_trials 의 그 줄만 함께 온다.

    진행은 연구 창고에서, 화면 임계치(멈춤 배수·추세 창)는 화면의 주 장부 config 에서 읽는다 —
    다른 화면 임계치(`dashboard.*`)와 같은 곳이다."""
    current = scope()
    return envelope(
        current,
        service.final_round_progress(research_store(), as_of=current.as_of, lookback=current.lookback,
                                     config_store=store(), running_scripts=_running_scripts(current.live)),
    )


@bp.get("/live-models")
def live_models() -> Any:
    """② 지금 매매에 쓰이는 모델 — 흐름 단계(설명·채택 기록·설정 실제 값) · 랭커 IC·모델 파일 · 병행 트랙.

    설정·가중치는 화면의 주 장부에서(다른 패널과 같은 곳), 모델 파일은 연구 창고 옆 `models/ranker` 에서
    `usable_from ≤ as_of` 로 거른다 — 실전 랭커가 모델을 찾는 규칙과 같다."""
    from pathlib import Path

    from quant_rl_trading.dashboard.services import alpha_ir as alpha_ir_service
    from quant_rl_trading.store.memo import derived

    current = scope()
    research = research_store()
    main = store()
    data = service_story.live_models(main, as_of=current.as_of, lookback=current.lookback,
                                     models_root=Path(research.root))
    # 병행 트랙마다 지금 성과(dashboard.md 2026-10-13 밤 ⑥) — 트레이딩 탭 '지수 대비 IR' 과 **같은 계산·같은 캐시 키**.
    try:
        alpha = derived(main, ("alpha_ir", current.as_of.isoformat()),
                        lambda: alpha_ir_service.alpha_ir(Path(research.root), config=main, source=research,
                                                          as_of=current.as_of))
    except Exception:  # noqa: BLE001 — 지수 대비가 깨져도 장부 누적은 보인다(칸은 '모름')
        alpha = None
    service_story.track_performance(Path(research.root), data.get("tracks", []), alpha, current.as_of)
    return envelope(current, data)


@bp.get("/trial-history")
def trial_history() -> Any:
    """③ 과거 학습 내역 — 사람이 쓴 카탈로그(docs/trials-catalog.yaml, 날짜 ≤ as_of) + 연구 창고의 시행 대장."""
    current = scope()
    return envelope(current, service_story.trial_history(research_store(), as_of=current.as_of))


@bp.get("/research-jobs")
def research_jobs() -> Any:
    """지금 도는 연구 스크립트와 최근 연구 로그. 창고가 아니라 /proc·logs 라 as_of 를 안 받는다
    (시스템 탭 프로세스 목록과 같은 이유 — 되감기지 않는 '지금' 이다)."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    return envelope(scope(), service.research_jobs(root))


@bp.get("/exposure-effect")
def exposure_effect() -> Any:
    """regime(노출 지표)의 평가 — IC 가 아니라 노출 기여(modelops-ranker.md ①). 표시만, 임계치 없음. as_of 로 되감긴다."""
    from quant_rl_trading.modelops.exposure_effect import effect_line
    from quant_rl_trading.modelops.exposure_effect import exposure_effect as compute
    from quant_rl_trading.store.memo import derived

    current = scope()
    current_store = store()
    result = derived(current_store, ("exposure_effect", current.as_of.isoformat()),
                     lambda: compute(current_store, as_of=current.as_of))
    return envelope(current, {**result, "line": effect_line(result)})


@bp.get("/candidates")
def candidates() -> Any:
    """② 무엇이 시험 중이고 언제 판정되나 — 매매 과정 탭 후보 칩과 **같은 함수**(``pipeline_view.candidates``)를 부른다.
    결과는 대장·카탈로그를 옮기기만 하고(판정을 새로 내리지 않는다), 판정일이 as_of 뒤인 결과는 없다(그 함수 규칙)."""
    from pathlib import Path

    from quant_rl_trading.dashboard.services import pipeline_view

    current = scope()
    research = research_store()
    data = pipeline_view.candidates(research, store(), as_of=current.as_of, market="KR", root=Path(research.root))
    keep = ("id", "stage", "title", "change", "judge", "status", "applied", "doc", "doc_fixed")
    return envelope(current, {"candidates": [{k: c[k] for k in keep} for c in data["candidates"]]})
