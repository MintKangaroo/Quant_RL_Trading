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

from datetime import UTC, datetime
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


def m4_status(
    store: Store | None = None, *, as_of: datetime | None = None, lookback: int = 90
) -> dict[str, Any]:
    """RL 착수 여부 — **창고의 학습 기록으로 판단한다.**

    2026-08-27 까지 ``active: False`` 가 박혀 있어서 학습이 60업데이트째 도는
    동안 화면은 "미착수" 라고 했다. 이제 ``rl_updates`` 의 최신 실행을 본다:
    돌고 있으면 "학습 중", 총량에 닿았으면 "완주", 기록이 없으면 "미착수".
    """
    label = "미착수"
    active = False
    run: dict[str, Any] | None = None
    if store is not None and as_of is not None:
        # 카드는 "지금 도는 실행" 만 필요하다 — 90일치를 읽으면 4.6초다(2026-08-28 실측).
        # 최근 3일에 기록이 없을 때만 원래 창으로 넓힌다.
        runs = training_runs(store, as_of=as_of, lookback=min(lookback, 3))
        if not runs["has_data"]:
            runs = training_runs(store, as_of=as_of, lookback=lookback)
        if runs["has_data"] and runs["runs"]:
            latest = runs["runs"][0]
            run = {k: latest[k] for k in (
                "run_id", "status", "last_update", "total_updates", "eta_minutes",
                "silent_minutes",
            )}
            active = latest["status"] == "running"
            label = {
                "running": f"학습 중 {latest['last_update']}/{latest['total_updates']}",
                "completed": f"완주 {latest['total_updates']}",
                "stopped": f"멈춤 {latest['last_update']}/{latest['total_updates']}",
            }.get(latest["status"], latest["status"])
    return {
        "active": active,
        "label": label,
        "run": run,
        "milestone": "M4",
        "note": (
            "M4 — PPO 학습 루프와 rl_updates 표가 들어왔다. 아래 칸은 창고의 최신 "
            "실행을 그린다. 완주 뒤 OOS 판정(evaluate_policy)까지가 M4 다."
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
            "plain": plain_summary(rows, progress),
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
    # 최근 여섯 실행만 — 옛 판 전부를 실으면 응답이 260KB·5초였다(2026-08-28).
    return {"has_data": True, "runs": runs[:6], "guards": UPDATE_GUARDS}


def plain_summary(rows: pd.DataFrame, progress: dict[str, Any]) -> str:
    """**쉬운 말 한 줄.** 숫자를 못 읽는 사람이 "지금 배우고 있나, 어디까지 왔나, 이상한가"
    를 알게 한다. 규칙으로만 만든다 — LLM 이 아니다(결정론, 리플레이 재현).

    보는 것: 진행률, 한 판당 점수(episode_reward)의 앞 50 대 뒤 50, 현금 비중 추세(도망),
    액션 반영률(안전장치가 덮는 몫), 엔트로피 추세(조기 수렴 전조), 남은 시간.
    """
    n = len(rows)
    if n == 0:
        return "학습 기록이 없다."
    win = min(50, max(1, n // 4))
    head, tail = rows.head(win), rows.tail(win)
    def mean(frame: pd.DataFrame, col: str) -> float | None:
        return float(frame[col].mean()) if col in frame.columns and frame[col].notna().any() else None
    r0, r1 = mean(head, "episode_reward"), mean(tail, "episode_reward")
    cash1 = mean(tail, "cash_weight")
    refl1 = mean(tail, "action_reflection")
    ent0, ent1 = mean(head, "entropy"), mean(tail, "entropy")
    status = progress.get("status")
    last, total = progress.get("last_update", 0), progress.get("total_updates", 0)
    pct = (100 * last / total) if total else 0
    parts: list[str] = []
    stage = {"running": "돌고 있다", "completed": "완주했다", "stopped": "멈춰 있다"}.get(status, status or "")
    parts.append(f"학습이 {pct:.0f}% 지점({last:,}/{total:,})에서 {stage}.")
    if r0 is not None and r1 is not None:
        delta = r1 - r0
        if delta > 0.002:
            parts.append(f"한 판당 점수가 {r0:+.4f}에서 {r1:+.4f}로 올랐다 — 정책이 실제로 배우고 있다.")
        elif delta < -0.002:
            parts.append(f"한 판당 점수가 {r0:+.4f}에서 {r1:+.4f}로 내려갔다 — 나빠지는 중이다.")
        else:
            parts.append(f"한 판당 점수가 {r1:+.4f} 근처에서 평평하다 — 더 배우지 못하고 있다.")
    if cash1 is not None:
        cash0 = mean(head, "cash_weight") or cash1
        if cash1 - cash0 > 0.03:
            parts.append(f"현금이 {cash0:.0%}→{cash1:.0%}로 늘고 있다 — 종목을 못 고르겠으니 도망가는 신호다.")
        else:
            parts.append(f"현금 {cash1:.0%}로 도망은 없다.")
    if refl1 is not None:
        parts.append(f"RL 의도의 {refl1:.0%}만 집행된다(나머지는 안전장치가 깎는다{'; 30% 아래면 룰 시스템' if refl1 < 0.3 else ''}).")
    if ent0 is not None and ent1 is not None and ent0 != 0:
        drop = (ent0 - ent1) / abs(ent0)
        if drop > 0.05:
            parts.append(f"선택의 폭(엔트로피)이 {drop:.0%} 좁아졌다 — 너무 빨리 확신하는지 볼 것.")
    if status == "running" and progress.get("eta_minutes"):
        hours = progress["eta_minutes"] / 60
        parts.append(f"완주까지 약 {hours:.0f}시간, 그 뒤 홀드아웃(OOS) 판정이 진짜 시험이다.")
    elif status == "completed":
        parts.append("다음은 홀드아웃(OOS) 판정 — 학습 구간 밖에서도 버는지 본다.")
    return " ".join(parts)


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


def evaluations(store: Store, *, as_of: datetime, lookback: int = 90) -> dict[str, Any]:
    """정책 평가(`rl_evaluations`) — '기본 전략보다 나은가'·'운이었나 실력이었나'.

    최신 평가 배치(같은 run·같은 valid_from)를 표로, 같은 run 의 평가 전부를
    편차 재료로 준다. **학습 시드가 하나면 하나라고 말한다** — 시드 간 분산은
    시드를 여럿 돌려야 나오는 값이고, 평가 표본을 바꿔 다시 잰 편차는 그
    대용품이 아니라 별개의 사실이다(둘을 화면에서 다른 이름으로 부른다).
    """
    frame = store.get("rl_evaluations", as_of=as_of, lookback=lookback)
    if frame.empty:
        return {"has_data": False, "latest": None, "history": [], "train_seeds": []}
    frame = frame.sort_values(["valid_from", "eval_window", "arm"])
    run_id = str(frame.iloc[-1]["entity_id"])
    mine = frame[frame["entity_id"] == run_id]
    latest_at = mine["valid_from"].max()
    batch = mine[mine["valid_from"] == latest_at]

    def _cell(row: Any) -> dict[str, Any]:
        return {
            "reward_mean": float(row["reward_mean"]),
            "reward_sum": float(row["reward_sum"]),
            "cash_weight": float(row["cash_weight"]),
            "action_reflection": float(row["action_reflection"]),
            "cost": float(row["cost"]),
            "turnover": float(row["turnover"]),
            "drawdown": float(row["drawdown"]),
        }

    table: dict[str, dict[str, Any]] = {}
    verdict = None
    for row in batch.to_dict(orient="records"):
        table.setdefault(str(row["eval_window"]), {})[str(row["arm"])] = _cell(row)
        if row["arm"] == "policy":
            table[str(row["eval_window"])]["gap"] = (
                float(row["gap_vs_equal"]) if row["gap_vs_equal"] is not None else None
            )
            verdict = row["verdict"]
    first = batch.iloc[0]
    history = []
    for at, group in mine[mine["arm"] == "policy"].groupby("valid_from"):
        gaps = {str(r["eval_window"]): float(r["gap_vs_equal"]) for r in group.to_dict(orient="records")}
        history.append({
            "evaluated_at": at.isoformat(),
            "envs": int(group.iloc[0]["envs"]),
            "episode_days": int(group.iloc[0]["episode_days"]),
            "eval_seed": int(group.iloc[0]["eval_seed"]),
            "gap_train": gaps.get("train"),
            "gap_oos": gaps.get("oos"),
            "verdict": str(group.iloc[0]["verdict"]),
        })
    seeds = sorted(
        {int(v) for v in frame["train_seed"].dropna().unique()}
    )
    return {
        "has_data": True,
        "latest": {
            "run_id": run_id,
            "evaluated_at": latest_at.isoformat(),
            "checkpoint": str(first["checkpoint"]),
            "update": int(first["update"]) if first["update"] is not None else None,
            "episode_days": int(first["episode_days"]),
            "envs": int(first["envs"]),
            "steps": int(first["steps"]),
            "verdict": verdict,
            "table": table,
        },
        "history": history,
        "train_seeds": seeds,
        "runs_evaluated": sorted({str(v) for v in frame["entity_id"].unique()}),
    }


#: rl-training.md §6 커리큘럼. 화면·문서가 같은 표를 쓴다 — 여기 적힌 기준이 바뀌면 문서도 바뀐다.
CURRICULUM_STAGES: list[dict[str, str]] = [
    {"stage": "C0", "label": "오라클 카나리", "criterion": "필요조건 셋 — 환경·용량·신용 (§0)"},
    {"stage": "C1", "label": "국장만 · 비중 액션 · 비용 0", "criterion": "EV > 0.1 · 균등가중 초과 (OOS)"},
    {"stage": "C2", "label": "비용·라운딩 추가", "criterion": "EV 유지 · 회전율 안정"},
    {"stage": "C3", "label": "후보 30 · 진입 지연", "criterion": "EV 유지"},
    {"stage": "C4", "label": "미장 추가 · KRW/USD 분리", "criterion": "EV 유지 · 환율 피처 기여"},
    {"stage": "C5", "label": "KR/US 주간 배분", "criterion": "스코어 비례 대비 IR 우위"},
]

GATE_LOG = "gate-c1.log"


def _canary_gate() -> dict[str, Any]:
    """`tools/verify_canary_gate.py` 의 마지막 판정. 창고가 아니라 로그다 —
    게이트는 학습 전 1회성 점검이라 표를 두지 않았다. 없으면 없다고 말한다."""
    from quant_rl_trading.dashboard.services import system as system_service

    path = system_service.logs_dir() / GATE_LOG
    if not path.exists():
        return {"checked": False, "passed": None, "detail": "게이트를 돌린 기록이 없다", "at": None}
    text = path.read_text(encoding="utf-8", errors="replace")
    passes = text.count("[PASS]")
    fails = text.count("[FAIL]")
    at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()
    return {
        "checked": True,
        "passed": fails == 0 and passes >= 3,
        "detail": f"PASS {passes} · FAIL {fails} / 3",
        "at": at,
    }


def curriculum(store: Store, *, as_of: datetime, lookback: int = 90) -> dict[str, Any]:
    """훈련 단계(C0~C5) 진행도 — **어느 단계에서 깨지는지가 곧 원인이다** (§6).

    각 단계의 상태는 지어내지 않는다: C0 은 게이트 로그, C1~ 은 `rl_updates`
    의 curriculum 값을 가진 실행과 그 실행의 `rl_evaluations` 판정에서만 온다.
    실행이 없는 단계는 "미착수" 다.
    """
    runs = training_runs(store, as_of=as_of, lookback=max(lookback, 90))
    evals = evaluations(store, as_of=as_of, lookback=max(lookback, 90))
    verdict_by_run: dict[str, str] = {}
    if evals["has_data"]:
        # 최신 run 의 판정만 표에 있지만 history 는 같은 run 이라 run_id 하나로 족하다.
        verdict_by_run[evals["latest"]["run_id"]] = str(evals["latest"]["verdict"])
    gate = _canary_gate()
    stages: list[dict[str, Any]] = []
    for spec in CURRICULUM_STAGES:
        entry: dict[str, Any] = {**spec, "status": "pending", "note": "미착수", "runs": []}
        if spec["stage"] == "C0":
            if gate["checked"]:
                entry["status"] = "passed" if gate["passed"] else "failed"
                entry["note"] = f"{gate['detail']} · {gate['at'][:10] if gate['at'] else ''}"
            stages.append(entry)
            continue
        mine = [r for r in runs.get("runs", []) if r.get("curriculum") == spec["stage"]]
        if mine:
            entry["runs"] = [
                {"run_id": r["run_id"], "status": r["status"],
                 "last_update": r.get("last_update"), "total_updates": r.get("total_updates"),
                 "verdict": verdict_by_run.get(r["run_id"])}
                for r in mine
            ]
            latest = mine[0]
            verdict = verdict_by_run.get(latest["run_id"])
            if verdict == "generalizes":
                entry["status"], entry["note"] = "passed", f"{latest['run_id']} · OOS 통과"
            elif verdict in ("overfit", "untrained"):
                entry["status"] = "failed"
                entry["note"] = (
                    f"{latest['run_id']} · 완주 · OOS "
                    + ("과적합" if verdict == "overfit" else "학습 안 됨")
                )
            elif latest["status"] == "running":
                entry["status"] = "running"
                entry["note"] = f"{latest['run_id']} · {latest.get('last_update')}/{latest.get('total_updates')}"
            else:
                entry["status"] = "unevaluated"
                entry["note"] = f"{latest['run_id']} · {latest['status']} · 평가 전"
        stages.append(entry)
    current = next((s["stage"] for s in stages if s["status"] in ("running", "failed", "unevaluated")), None)
    if current is None:
        current = next((s["stage"] for s in stages if s["status"] == "pending"), None)
    return {"stages": stages, "gate": gate, "current": current}
