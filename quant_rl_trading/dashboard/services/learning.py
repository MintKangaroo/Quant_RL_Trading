"""학습 탭 집계 — RL(M4)은 **환경까지 왔고 학습 기록이 아직 없다.**

`dashboard-kickoff.md` D-4 는 `dashboard.md` §5 의 자리(explained_variance·
커리큘럼·학습 곡선·approx KL·베이스라인 대비 IR·시드 분산·Optuna trial)를
그대로 만들라고 한다. 자리는 예약하되 값을 지어내지 않는다 — 0 으로 채우면
"쟀는데 0" 이 되어 화면이 거짓말한다 (불변식 3, app.py ``SafeJSONProvider``).

**막고 있는 것은 학습 코드가 아니라 담을 표다.** `allocator/` 는 들어왔고
(env·policy·cache·reward·baseline) 오라클 카나리도 돌았지만, 그 산출물을
담을 테이블(episode·trial 류)이 창고 스키마에 아직 없다
(`store/tables.py` 에 0건). 그래서 학습을 돌려도 적을 데가 없고 이 탭은
그대로 빈다 — 4-5(PPO 루프)를 만들 때 **표를 같이** 만들어야 하는 이유다.

2026-08-19: 이 독스트링과 아래 note 가 "allocator/ 는 존재하지 않고" 라고
적힌 채로 남아 있었다. `allocator/` 가 들어온 뒤에도 안 고쳐져서 화면이
사실과 다른 말을 하고 있었다. **자리표시자의 설명문도 화면에 나가는 사실이다.**

지금 실제로 있는 것은 **Analyst IC 게이트**다. M4 상태 인코더가 조합할 입력이
바로 이것이고, RL 이 없어도 "무엇이 학습을 대신하고 있는가" 를 보여줄 수
있다. 가중치·이력 계산은 ``agent_health`` 서비스를 그대로 쓴다 — 같은 사실을
두 번 집계하면 언젠가 두 화면이 다른 숫자를 보여준다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from quant_rl_trading.allocator import budget
from quant_rl_trading.dashboard.services import agent_health
from quant_rl_trading.store import Store

#: 학습 지표 표 (M4). 이름을 문자열로 흩뿌리지 않는다.
RL_UPDATES = "rl_updates"

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
            "M4 — RL 환경은 들어왔다(allocator/ — env·policy·cache·reward·baseline). "
            "아직 비어 있는 이유는 학습 코드가 없어서가 아니라 **학습 기록을 "
            "담을 테이블이 창고에 없기** 때문이다. PPO 학습 루프와 그 표가 "
            "같이 들어오면 아래 칸들이 채워진다."
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


#: 경고선. `docs/design/rl-training.md` §10 의 표를 그대로 옮긴 것이고,
#: **여기서 값을 정하지 않는다** — 화면이 문서와 다른 선을 그으면 어느
#: 쪽이 맞는지 아무도 모르게 된다.
UPDATE_GUARDS: dict[str, dict[str, Any]] = {
    "explained_variance": {"floor": 0.1, "label": "0.1 이상. 0 근처 고착 = 실패"},
    "approx_kl": {"band": [0.01, 0.02], "label": "0.01~0.02. 급등 = 학습률 과다"},
    "action_reflection": {"floor": 0.30, "label": "30% 미만 경고"},
}


def training_runs(store: Store, *, as_of: datetime, lookback: int) -> dict[str, Any]:
    """PPO 학습 지표 (`rl_updates`). **0행이면 0행이라고 말한다.**

    학습을 안 돌렸을 때와 돌렸는데 0 이 나왔을 때는 다른 사실이다. 0 으로
    채워 그리면 둘이 같은 그림이 되고, 그때부터 화면은 거짓말을 한다
    (불변식 3). 그래서 ``has_data`` 를 따로 준다.
    """
    frame = store.get(RL_UPDATES, as_of=as_of, lookback=lookback)
    if frame.empty:
        return {"has_data": False, "runs": [], "guards": UPDATE_GUARDS}

    runs: list[dict[str, Any]] = []
    total = budget.total_updates()
    for run_id, rows in frame.groupby("entity_id", sort=False):
        rows = rows.sort_values("update")
        progress = run_progress(rows, as_of=as_of, total_updates=total)
        series = {
            key: [None if pd.isna(v) else float(v) for v in rows[key]]
            for key in UPDATE_GUARDS
            if key in rows.columns
        }
        for extra in ("entropy", "grad_norm", "episode_reward", "cash_weight"):
            if extra in rows.columns:
                series[extra] = [
                    None if pd.isna(v) else float(v) for v in rows[extra]
                ]
        last = rows.iloc[-1]
        runs.append({
            "run_id": str(run_id),
            "updates": [int(v) for v in rows["update"]],
            "series": series,
            "seed": int(last["seed"]) if not pd.isna(last.get("seed")) else None,
            "market": str(last.get("market") or ""),
            "curriculum": str(last.get("curriculum") or ""),
            "git_commit": str(last.get("git_commit") or ""),
            **progress,
        })
    # **마지막으로 기록을 남긴 실행이 위로.** run_id 문자열로 정렬하면
    # "rl-2026…" 이 "m4-…" 보다 앞서서 옛 판이 화면을 차지한다 (2026-08-27 실측
    # — 돌고 있는 r6 대신 8/19 판이 그려졌다).
    runs.sort(key=lambda r: r["last_observed_at"], reverse=True)
    return {"has_data": True, "runs": runs, "guards": UPDATE_GUARDS}


#: 마지막 기록 뒤 이만큼 조용하면 "멈춤" 으로 본다 — 페이스의 3배, 최소 15분.
#: 업데이트 하나가 2~3분이라 스텝 하나 밀린 것을 죽었다고 하지 않는다.
STALL_PACE_MULTIPLE = 3.0
STALL_FLOOR_MINUTES = 15.0


def run_progress(rows: pd.DataFrame, *, as_of: datetime, total_updates: int) -> dict[str, Any]:
    """한 실행의 진행 상태. **살아 있는지는 시계(as_of)와 마지막 기록의 거리로 안다.**

    프로세스를 들여다보지 않는다 — 화면은 창고만 본다(불변식 1). 창고에
    ``rl_updates`` 가 계속 쌓이면 살아 있는 것이고, 끊기면 죽은 것이다.
    페이스는 기록 간격의 중앙값이라 재시작 공백 하나에 흔들리지 않는다.
    """
    stamps = pd.to_datetime(rows["observed_at"], utc=True).sort_values()
    last_stamp = stamps.iloc[-1]
    gaps = stamps.diff().dropna().dt.total_seconds() / 60.0
    pace = float(gaps.median()) if len(gaps) else None
    last_update = int(rows["update"].max())
    silent = (pd.Timestamp(as_of).tz_convert("UTC") - last_stamp).total_seconds() / 60.0

    if last_update >= total_updates:
        status = "completed"
    elif silent <= max(STALL_FLOOR_MINUTES, STALL_PACE_MULTIPLE * (pace or 0.0)):
        status = "running"
    else:
        status = "stopped"
    remaining = max(0, total_updates - last_update)
    return {
        "status": status,
        "last_update": last_update,
        "total_updates": total_updates,
        "last_observed_at": last_stamp.isoformat(),
        "pace_minutes": pace,
        "silent_minutes": round(silent, 1),
        "eta_minutes": (pace * remaining) if (pace and status == "running") else None,
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


def research_ledger(store: Store, *, as_of: datetime, lookback: int) -> dict[str, Any]:
    """자기개선 시행 대장 — self-improvement.md §7.

    자기개선을 켜면 누적 시행 횟수가 성과 지표만큼 중요해진다. 예산은
    ``store.config`` 에서 읽고(불변식 10), DSR 은 nav_daily 의 일별 수익률로
    계산한다 — 표본이 30일 미만이면 숫자를 지어내지 않고 없다고 말한다.
    승격 비율·사후 성과는 승격 파이프라인이 아직 없어 **집계 대상이 0건**이다
    — 0 으로 그리지 않고 없다고 말한다 (training_runs 와 같은 이유).
    """
    from quant_rl_trading.modelops import trials as trials_module

    total = trials_module.cumulative_trials(store, as_of=as_of)
    families = trials_module.trials_by_family(store, as_of=as_of)
    quarter_used = trials_module.quarter_trials(store, as_of=as_of)
    budget = int(store.config("research.trial_budget_quarter", as_of=as_of))

    vault = store.get(trials_module.HOLDOUT_TABLE, as_of=as_of)
    openings = []
    if not vault.empty:
        for _, row in vault.sort_values("valid_from").iterrows():
            openings.append({
                "opened_at": str(row["valid_from"]),
                "reason": str(row.get("reason") or ""),
                "window": f"{row.get('window_start') or ''}~{row.get('window_end') or ''}",
                "detail": str(row.get("detail") or ""),
            })

    dsr: dict[str, float] | None = None
    nav = store.get("nav_daily", as_of=as_of, lookback=max(lookback, 400))
    if not nav.empty and "twr_return" in nav.columns:
        returns = nav.sort_values("valid_from")["twr_return"].dropna()
        dsr = trials_module.deflated_sharpe(returns.to_numpy(), n_trials=max(total, 1))

    return {
        "cumulative_trials": total,
        "families": families,
        "quarter_used": quarter_used,
        "quarter_budget": budget,
        "holdout_start": str(store.config("research.holdout.start", as_of=as_of)),
        "holdout_openings": openings,
        "dsr": dsr,
        "nav_sample_days": int(nav["twr_return"].notna().sum()) if not nav.empty else 0,
        # 승격 파이프라인 산출물. 표가 생기면 여기서 집계한다 — 그때까지 null.
        "promotion": None,
    }


__all__ = [
    "M4_WIDGETS",
    "WALK_FORWARD_2026_01_02",
    "analyst_gate",
    "ic_history",
    "m4_status",
    "research_ledger",
    "walk_forward_comparison",
]
