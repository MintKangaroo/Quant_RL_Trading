"""학습 탭 집계 — RL(M4)은 아직 없다.

`dashboard-kickoff.md` D-4 는 `dashboard.md` §5 의 자리(explained_variance·
커리큘럼·학습 곡선·approx KL·베이스라인 대비 IR·시드 분산·Optuna trial)를
그대로 만들라고 하지만, `docs/milestones.md` 기준 지금은 **M3** 다.
`allocator/` 는 존재하지 않고, 그 산출물을 담을 테이블(episode·trial 류)도
창고 스키마에 없다 (`store/tables.py`). 자리는 예약하되 값을 지어내지 않는다
— 0 으로 채우면 "쟀는데 0" 이 되어 화면이 거짓말한다 (불변식 3, app.py
``SafeJSONProvider`` 참고).

지금 실제로 있는 것은 **Analyst IC 게이트**다. M4 상태 인코더가 조합할 입력이
바로 이것이고, RL 이 없어도 "무엇이 학습을 대신하고 있는가" 를 보여줄 수
있다. 가중치·이력 계산은 ``agent_health`` 서비스를 그대로 쓴다 — 같은 사실을
두 번 집계하면 언젠가 두 화면이 다른 숫자를 보여준다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from quant_rl_trading.dashboard.services import agent_health
from quant_rl_trading.store import Store

#: M4 가 만들 산출물의 자리. 테이블이 아예 없으므로 조회하지 않는다 —
#: 조회해서 빈 결과를 받는 것과 애초에 잴 수 없는 것은 다른 사실이다.
M4_WIDGETS: list[dict[str, str]] = [
    {
        "key": "explained_variance",
        "label": "explained_variance 추이",
        "detail": "0.1 기준선. 0 근처에 붙어 있으면 학습 실패다 (rl-training.md §3).",
    },
    {
        "key": "curriculum",
        "label": "커리큘럼 C0~C5 진행도",
        "detail": "오라클 카나리 통과 여부 포함.",
    },
    {
        "key": "episode_reward",
        "label": "학습 곡선 (에피소드 보상)",
        "detail": "",
    },
    {
        "key": "optimizer_diag",
        "label": "approx KL · entropy · gradient norm",
        "detail": "",
    },
    {
        "key": "ir_vs_baseline",
        "label": "베이스라인 대비 IR",
        "detail": "벤치마크 / 동일가중 / 스코어 비례(M3 룰). 못 이기면 RL 을 쓸 이유가 없다.",
    },
    {
        "key": "seed_variance",
        "label": "시드 간 성과 분산 · policy churn",
        "detail": "하이퍼파라미터 간 차이보다 크면 그 결과는 노이즈다.",
    },
]


def m4_status() -> dict[str, Any]:
    """RL 착수 여부. 시각을 되감아도 바뀌지 않는 마일스톤 사실이다.

    ``as_of`` 를 받긴 하지만(불변식 9, 규약 준수) 답은 시점에 의존하지 않는다
    — data_quality 의 "진행 중인 작업" 패널과 같은 이유다: 이건 관측된
    사실이 아니라 창고 스키마의 현재 상태다.
    """
    return {
        "active": False,
        "milestone": "M4",
        "note": (
            "RL 학습은 M4 다. docs/milestones.md 기준 아직 착수 전이라 "
            "allocator/ 도, 그 산출물을 담을 테이블도 창고에 없다. "
            "이 탭의 절반은 M4 가 오면 채워질 자리다."
        ),
        "widgets": M4_WIDGETS,
    }


def analyst_gate(store: Store, *, as_of: datetime, lookback: int) -> dict[str, Any]:
    """지금 실제로 학습(선택)을 대신하고 있는 것 — Analyst IC 게이트.

    M4 상태 인코더가 조합할 입력이 바로 이 가중치다.
    """
    roster = agent_health.roster(store, as_of=as_of, lookback=lookback)
    measured = [item for item in roster if item["measured"]]
    active = [item for item in roster if float(item["weight"]) > 0]
    return {
        "roster": roster,
        "active": [item["analyst"] for item in active],
        "active_count": len(active),
        "measured_count": len(measured),
        "total": len(roster),
        "active_weight": sum(float(item["weight"]) for item in roster),
    }


def ic_history(store: Store, *, as_of: datetime, lookback: int) -> dict[str, Any]:
    """Analyst 별 IC 측정 이력 + 합격선.

    합격선은 코드에 적지 않고 매 요청 ``store.config`` 에서 읽는다 (불변식 10).
    """
    history = agent_health.ic_history(store, as_of=as_of, lookback=lookback)
    history["threshold"] = float(store.config("analyst.ic_threshold", as_of=as_of))
    return history


#: 2026-01-02 워크포워드 최종 판정. `data/_backtest` 샌드박스(purged K-fold +
#: embargo, 300세션)에서 나온 값이지 **실전 창고가 아니다** — 그래서 store 로
#: 조회하지 않고 여기에 고정한다. 이미 끝난 과거 측정이라 as_of 로 되감아도
#: 바뀌지 않는 사실이다 (m4_status 와 같은 이유). 출처:
#: `data/_backtest/curated/analyst_weights`.
WALK_FORWARD_2026_01_02: dict[str, dict[str, Any]] = {
    "risk": {"ic": 0.0833, "passed": True, "weight": 1.0},
    "fundamental": {"ic": 0.0699, "passed": True, "weight": 1.0},
    "event": {"ic": 0.0427, "passed": True, "weight": 1.0},
    "regime": {"ic": 0.0101, "passed": False, "weight": 0.0},
    "flow_kr": {"ic": 0.0019, "passed": False, "weight": 0.0},
    "chart": {"ic": -0.0166, "passed": False, "weight": 0.0},
}


def walk_forward_comparison(store: Store, *, as_of: datetime, lookback: int) -> dict[str, Any]:
    """워크포워드(샌드박스, 2026-01-02) 대 라이브(실전 창고) IC 비교.

    같은 Analyst 라도 측정 시점·표본이 다르면 IC 가 달라진다 — 그 차이 자체가
    "IC 는 한 번 재고 끝나는 값이 아니다" 라는 근거다. 라이브 쪽만 ``store``
    에서 읽는다(``agent_health.roster`` 재사용). 워크포워드 값은 위 상수다.
    """
    roster = agent_health.roster(store, as_of=as_of, lookback=lookback)
    live_by_name = {item["analyst"]: item for item in roster}

    rows: list[dict[str, Any]] = []
    for name, wf in sorted(WALK_FORWARD_2026_01_02.items()):
        live = live_by_name.get(name)
        measured = bool(live is not None and live["measured"])
        live_ic = float(live["ic"]) if measured and live is not None else None
        live_passed = bool(live["passed"]) if measured and live is not None else None
        rows.append(
            {
                "analyst": name,
                "wf_ic": wf["ic"],
                "wf_passed": wf["passed"],
                "wf_weight": wf["weight"],
                "live_ic": live_ic,
                "live_passed": live_passed,
                "live_measured": measured,
                "delta_ic": (live_ic - wf["ic"]) if live_ic is not None else None,
            }
        )
    source = (
        "data/_backtest 샌드박스 워크포워드 (purged K-fold + embargo, 300세션)"
        " — 실전 창고 아님"
    )
    return {
        "measured_at": "2026-01-02",
        "source": source,
        "threshold": float(store.config("analyst.ic_threshold", as_of=as_of)),
        "rows": rows,
    }


__all__ = [
    "M4_WIDGETS",
    "WALK_FORWARD_2026_01_02",
    "analyst_gate",
    "ic_history",
    "m4_status",
    "walk_forward_comparison",
]
