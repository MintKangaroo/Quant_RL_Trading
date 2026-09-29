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

import re
from pathlib import Path

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from quant_rl_trading.allocator import budget
from quant_rl_trading.dashboard.services import agent_health
from quant_rl_trading.store import ConfigNotFound, Store

#: 화면의 시각 말('내일 05:40')은 한국시간이다 — 사용자가 읽는 시계.
KST = ZoneInfo("Asia/Seoul")

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


def analyst_gate(
    store: Store, *, as_of: datetime, lookback: int, market: str | None = None,
) -> dict[str, Any]:
    """지금 실제로 학습(선택)을 대신하고 있는 것 — Analyst IC 게이트.

    M4 상태 인코더가 조합할 입력이 바로 이 가중치다.
    """
    roster = agent_health.roster(store, as_of=as_of, lookback=lookback, market=market)
    measured = [item for item in roster if item["measured"]]
    active = [item for item in roster if float(item["weight"]) > 0]
    return {
        "roster": roster,
        "active": [item["analyst"] for item in active],
        "active_count": len(active),
        "measured_count": len(measured),
        "total": len(roster),
        "active_weight": sum(float(item["weight"]) for item in roster),
        "alerts": [
            alert for alert in ranker_decay_alerts(store, as_of=as_of, lookback=lookback)
            if market in (None, "ALL") or alert["market"] == market
        ],
    }


def ranker_decay_alerts(store: Store, *, as_of: datetime, lookback: int) -> list[dict[str, Any]]:
    """랭커 ModelOps ① — 감쇠 경보 (docs/design/modelops-ranker.md).

    - 랭커 감쇠: 시장별 랭커 IC 가 합격선 아래로 ``modelops.ranker.fail_streak`` 번 **연속**.
    - 입력 감쇠: 랭커 입력 Analyst 의 IC 가 직전 측정의 ``input_decay_ratio`` 아래로.
    측정이 모자라면(연속 횟수 미만) 경보를 만들지 않는다 — 없는 것을 0 으로 채우지 않는다.
    임계치 셋 다 store.config 에서 읽는다(불변식 10). 여기서는 아무것도 바꾸지 않는다 —
    가중치는 한계기여 규칙이 정하고, 이 경보는 사람이 볼 표지다.
    """
    from quant_rl_trading.analysts.ranker import BASE_ANALYSTS

    threshold = float(store.config("analyst.ic_threshold", as_of=as_of))
    streak = int(store.config("modelops.ranker.fail_streak", as_of=as_of))
    ratio = float(store.config("modelops.ranker.input_decay_ratio", as_of=as_of))
    frame = store.get(agent_health.WEIGHTS, as_of=as_of, lookback=lookback, columns=["entity_id", "market", "valid_from", "ic"])
    if frame.empty:
        return []
    frame = frame.sort_values("valid_from")
    alerts: list[dict[str, Any]] = []
    for market, part in frame.groupby("market"):
        by_name = {str(name): grp["ic"].astype(float).tolist() for name, grp in part.groupby("entity_id")}
        ranker = by_name.get("ranker") or []
        if len(ranker) >= streak and all(value < threshold for value in ranker[-streak:]):
            alerts.append({
                "kind": "ranker_decay", "market": str(market), "analyst": "ranker",
                "text": f"랭커 감쇠 ({market}) — IC {' → '.join(f'{v:+.3f}' for v in ranker[-streak:])}, 합격선 {threshold:.2f} 아래 {streak}회 연속",
            })
        for name in BASE_ANALYSTS:
            series = by_name.get(name) or []
            if len(series) >= 2 and series[-2] > 0 and series[-1] < ratio * series[-2]:
                alerts.append({
                    "kind": "input_decay", "market": str(market), "analyst": name,
                    "text": f"입력 감쇠 {name} ({market}) — IC {series[-2]:+.3f} → {series[-1]:+.3f} (직전의 {ratio:.0%} 아래)",
                })
    return alerts


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


def ic_history(
    store: Store, *, as_of: datetime, lookback: int, market: str | None = None,
) -> dict[str, Any]:
    """Analyst 별 IC 측정 이력 + 합격선.

    합격선은 코드에 적지 않고 매 요청 ``store.config`` 에서 읽는다 (불변식 10).
    """
    history = agent_health.ic_history(store, as_of=as_of, lookback=lookback, market=market)
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
    "final_round_progress",
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


# -- 연구 작업 (2026-09-03) ----------------------------------------------------------
#: 학습 탭의 "지금 돌고 있는 학습" 은 rl_updates(RL) 만 본다. 시행·채점·복구 같은 연구
#: 스크립트는 창고에 안 적히므로 화면에 없었다 — 사용자 지적. 프로세스와 로그로 보여준다.
RESEARCH_CMD = re.compile(
    r"(?:tools/(trial_[a-z_0-9]+|backfill_[a-z_]+|measure_[a-z_]+|repair_[a-z_]+|collect_consensus_naver|"
    r"train_[a-z_]+|build_rl_cache|select_checkpoint|evaluate_policy|promotion_gate|freeze_[a-z0-9_]+|score_be2|vault_judge)\.py"
    r"|scripts/(chain_[a-z_0-9]+|pilot_[a-z_0-9]+)\.sh)"
)
RESEARCH_LOG_PREFIXES = (
    "trial-", "ic-", "repair-", "consensus-", "backfill-", "headroom-",
    "train-", "chain-", "pilot-", "rl-cache", "select-", "evaluate-", "promotion-",
    "freeze-", "score-be2", "vault-",   # BE2 얼리기·매일 점수·금고 심사(2026-09-29)
)
RESEARCH_LOG_ROWS = 8


def research_jobs(root: Path) -> dict[str, Any]:
    """지금 도는 연구 스크립트 + 최근 연구 로그의 마지막 줄. /proc 와 logs/ 만 읽는다."""
    from quant_rl_trading.dashboard.services import system as system_service

    procs = system_service.project_processes(root).get("processes", [])
    running = []
    for proc in procs:
        m = RESEARCH_CMD.search(str(proc.get("command", "")))
        if m:
            running.append({**proc, "script": m.group(1) or m.group(2)})
    logs: list[dict[str, Any]] = []
    log_dir = root / "logs"
    if log_dir.is_dir():
        files = [f for f in log_dir.glob("*.log") if f.name.startswith(RESEARCH_LOG_PREFIXES)]
        files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        for f in files[:RESEARCH_LOG_ROWS]:
            try:
                lines = [ln.strip() for ln in f.read_text(encoding="utf-8", errors="ignore").splitlines() if ln.strip()]
            except OSError:
                lines = []
            logs.append({
                "log": f.name,
                "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds"),
                "last": (lines[-1] if lines else "")[:160],
            })
    return {"running": running, "logs": logs}


# --------------------------------------------------------------------------- 마지막 모델 회차

#: 학습 진행 표. `tools/final_round_kit.record_progress` 가 적는다.
TRIAL_PROGRESS = "trial_progress"
#: 마지막 모델 회차의 판정 행(`research_trials`)을 고르는 접두어. 시행 도구가 이 이름으로 적는다.
FINAL_ROUND_ENTITY = "final-model-round"
#: 대조군(모델이 아니다). 화면이 "모델이 이겼나" 를 물을 때 기준선이 되는 군이다.
FINAL_ROUND_CONTROLS = ("C0", "C1")
#: 진행 곡선에 실을 시행당 최근 행 수. 41블록 × 5시드 = 205행이라 전부 실어도 작지만,
#: 재실행이 쌓이면 응답이 커진다 — training_runs 가 6실행으로 자른 것과 같은 이유다.
PROGRESS_ROW_CAP = 1200

#: **응답에 실어도 되는 칸.** 판정 창 수익·IC·MDD·회전은 이 목록에 없다 — 사전등록이
#: "학습 중에는 판정 창을 보지 않는다" 를 요구하고, 진행 화면이 그것을 비추면 규칙이 깨진다.
#: 판정 결과는 끝난 뒤 `research_trials` 의 줄로만 나온다. 테스트가 이 목록을 지킨다.
PROGRESS_PUBLIC_FIELDS = (
    "seed", "block", "n_blocks", "fold", "n_folds", "n_seeds", "step", "epoch", "rounds",
    "train_loss", "val_loss", "metric", "stopped_early", "elapsed_s", "note", "market", "at",
)


def _progress_rows(frame: pd.DataFrame) -> pd.DataFrame:
    """같은 (시행·시드·블록/폴드)의 **마지막 기록만** 남긴다.

    다시 돌리면 valid_from 이 달라 새 행이다(append-only). 전부 그리면 지난 회차의 곡선이
    새 회차와 겹쳐 그려져서, 어느 선이 지금 도는 학습인지 알 수 없다.
    """
    frame = frame.sort_values("valid_from").copy()
    keys = ["entity_id"]
    for key in ("seed", "block", "fold", "market"):
        column = f"_key_{key}"
        # 없는 축(BG 의 block · BE 의 fold)은 -1 로 묶는다 — NaN 은 groupby 에서 조용히 떨어진다.
        # market 도 키다(2026-09-29): BG 는 시장마다 같은 (시드, 폴드) 를 따로 적는다 — 빼면 KR 폴드 1 을 US 폴드 1 이 덮었다.
        frame[column] = frame[key].fillna(-1) if key in frame.columns else -1
        keys.append(column)
    return frame.groupby(keys, sort=False).tail(1).drop(columns=keys[1:])


def _num(value: Any) -> float | None:
    return None if value is None or pd.isna(value) else float(value)


def _int(value: Any) -> int | None:
    return None if value is None or pd.isna(value) else int(value)


#: **등록 순서**(사전등록 `docs/protocols/final-model-round-2026-10.md` + D1). 아직 기록이 없는 시행은 이 순서로
#: "대기" 에 선다. 날짜는 약속하지 않는다 — 순서만이 등록된 사실이다. D1 은 변형 D1a·D1b 로 적힌다(접두어로 맞춘다).
FINAL_ROUND_QUEUE = ("BE", "BF", "BG", "D1")

#: 시행마다 "무엇을 배우나" 한 줄. 화면이 전문 용어 대신 이것을 먼저 보인다. 모르는 시행은 이름만 나간다.
FINAL_ROUND_ABOUT: dict[str, str] = {
    "BE": "가격 흐름을 읽는 트랜스포머 — 최근 60일 가격·거래 패턴으로 5일 뒤 순위를 맞힌다",
    "BF": "순위 전용 GBM(LambdaRank) — 상위 종목을 맞히는 데 집중",
    "BG": "잔차 RL — GBM 점수 위에서 비중만 조금 조정",
    "D1": "결정 중심 학습 — 수익이 나는 방향으로 점수를 보정",
    "D1a": "결정 중심 학습 — 수익이 나는 방향으로 점수를 보정 (상위 종목 고르기판)",
    "D1b": "결정 중심 학습 — 수익이 나는 방향으로 점수를 보정 (지수 기울이기판)",
    "C0": "비교 기준(대조군) — 지금 쓰는 GBM 랭커",
    "C1": "비교 기준(대조군) — 같은 GBM 에 새 재료를 넣은 것",
}

#: 내부 검증 칸(`val_loss`, 낮을수록 좋게 적힘)을 **사람이 읽는 방향**으로 부를 이름. metric 문자열에 든 말로 고른다.
#: 판정 창이 아니라 학습창 안쪽 검증이다 — 이름에 "내부 검증" 을 꼭 붙인다.
_SCORE_NAMES = (
    ("spearman", "내부 검증 순위상관"),
    ("ndcg", "내부 검증 NDCG(상위 순위 정확도)"),
    ("valid edge", "내부 검증 우위"),
    ("hard rule", "내부 검증 규칙 포트 연수익"),
)

#: 학습 상태 판정에 쓰는 설정 이름(불변식 10 — 화면이 숫자를 들지 않는다).
STALL_FACTOR_KEY = "dashboard.training_stall_factor"
TREND_WINDOW_KEY = "dashboard.training_trend_window"


def _about(trial: str) -> str:
    if trial in FINAL_ROUND_ABOUT:
        return FINAL_ROUND_ABOUT[trial]
    head = next((q for q in FINAL_ROUND_QUEUE if trial.startswith(q)), None)
    return FINAL_ROUND_ABOUT.get(head, "") if head else ""


def _score_axis(metric: str) -> tuple[str, bool]:
    """(축 이름, 뒤집나). 기록 규약은 "val_loss 는 낮을수록 좋다 — 높을수록 좋은 지표는 부호를 뒤집어 넣고
    metric 에 (−) 나 - 를 적는다" 다. 뒤집힌 것만 되돌려 **높을수록 좋게** 그린다. 모르는 지표는 그대로 둔다."""
    val_part = metric.split("/")[-1].strip()
    flipped = "(−)" in val_part or "(-)" in val_part or val_part.startswith(("-", "−"))
    if not flipped:
        return "내부 검증 손실 (낮을수록 좋음)", False
    lowered = val_part.lower()
    name = next((label for key, label in _SCORE_NAMES if key in lowered), "내부 검증 점수")
    return f"{name} (높을수록 좋음)", True


def _ts(value: Any) -> pd.Timestamp:
    """UTC 로 맞춘 시각. 창고는 시간대를 붙여 돌려주지만(KST 로 올 때도 있다) 응답은 한 시간대로 낸다."""
    stamp = pd.Timestamp(value)
    return stamp.tz_localize(UTC) if stamp.tzinfo is None else stamp.tz_convert(UTC)


def _kst_label(moment: pd.Timestamp, as_of: datetime) -> str:
    """절대 시각을 한국시간·**as_of 기준** 말로 — '오늘 16:34' · '내일 05:40' · '10/08 05:40'."""
    local = moment.tz_convert(KST)
    ref = _ts(as_of).tz_convert(KST)
    days = (local.date() - ref.date()).days
    prefix = {-1: "어제", 0: "오늘", 1: "내일", 2: "모레"}.get(days, f"{local.month}/{local.day}")
    return f"{prefix} {local:%H:%M}"


def _duration(seconds: float) -> str:
    if seconds < 90:
        return f"{round(seconds)}초"
    if seconds < 90 * 60:
        return f"{round(seconds / 60)}분"
    if seconds < 36 * 3600:
        return f"{seconds / 3600:.1f}시간"
    return f"{seconds / 86400:.1f}일"


def _slope(values: list[float]) -> float:
    """최소제곱 기울기(x = 0..n−1). 끝점 둘만 보면 한 블록의 튐이 추세로 읽힌다."""
    n = len(values)
    mean_x = (n - 1) / 2
    mean_y = sum(values) / n
    den = sum((i - mean_x) ** 2 for i in range(n))
    return sum((i - mean_x) * (v - mean_y) for i, v in enumerate(values)) / den if den else 0.0


def _training_settings(store: Store, as_of: datetime) -> dict[str, Any] | None:
    """멈춤 배수·추세 창. 창고에 없으면 None — 그때는 상태를 **판단하지 않는다**(기본값을 지어내지 않는다)."""
    try:
        return {
            "stall_factor": float(store.config(STALL_FACTOR_KEY, as_of=as_of)),
            "trend_window": int(store.config(TREND_WINDOW_KEY, as_of=as_of)),
        }
    except ConfigNotFound:
        return None


def _health(*, done: bool, since_last_s: float, mean_unit_s: float | None, unit: str,
            slowest_unit_s: float | None = None,
            latest_curve: dict[str, Any] | None, settings: dict[str, Any] | None,
            last_at: pd.Timestamp, as_of: datetime) -> tuple[str, str]:
    """상태 하나와 이유 한 줄. done · stalled(느림/멈춤 의심) · overfit(과적합 의심) · ok · unknown.

    과적합은 **학습창 안쪽 검증**(`val_loss`)만 본다 — 판정 창은 이 표에 애초에 없다(사전등록).
    추세는 가장 최근에 기록한 시드의 마지막 `trend_window` 단위로 잰다 — 시드를 섞으면 시드 사이의
    계단이 추세로 읽힌다.
    """
    if done:
        return "done", f"다 돌았다 — {_kst_label(last_at, as_of)} 끝남"
    if settings is None:
        return "unknown", (f"설정 {STALL_FACTOR_KEY}·{TREND_WINDOW_KEY} 가 창고에 없어 판단하지 않는다 "
                           "— tools/seed_config.py 로 심는다")
    if not mean_unit_s:
        return "unknown", "아직 한 단위도 걸린 시간이 안 적혀 속도를 모른다"
    factor = settings["stall_factor"]
    # 기준은 **최근에 가장 오래 걸린 단위**다(2026-09-29). 재학습이 끼는 블록은 평균의 네 배쯤 걸려(BE: 평균 3분,
    # 재학습 블록 12분) 평균 × 3 으로 재면 재학습 때마다 "멈춤 의심" 이 떴다.
    slow = max(mean_unit_s, slowest_unit_s or 0.0)
    if since_last_s > factor * slow:
        return "stalled", (f"마지막 기록이 {_duration(since_last_s)} 전 — 최근 가장 오래 걸린 {unit}도 "
                           f"{_duration(slow)} 였는데 그 {factor:g}배를 넘었다")
    window = max(int(settings["trend_window"]), 2)
    pairs: list[tuple[float, float]] = []
    if latest_curve is not None:
        pairs = [(t, v) for t, v in zip(latest_curve["train"], latest_curve["val"], strict=False)
                 if t is not None and v is not None]
    # 같은 값이 이어지는 행은 **한 번의 학습**이다(2026-09-29 BE: 재학습 사이 블록은 예측만 해서 손실이 그대로
    # 반복된다). 접지 않으면 5블록 창에 재학습이 한 번만 들어가 한 걸음의 흔들림을 "추세" 로 읽었다.
    pairs = [p for i, p in enumerate(pairs) if i == 0 or p != pairs[i - 1]]
    tail = pairs[-window:]
    base = f"마지막 기록 {_duration(max(since_last_s, 0.0))} 전 · {unit} 하나에 보통 {_duration(mean_unit_s)}"
    if len(tail) < window:
        return "ok", f"{base} · 내부 검증 추세는 학습 {window}번이 쌓이면 본다"
    train_slope = _slope([t for t, _ in tail])
    val_slope = _slope([v for _, v in tail])      # val_loss — 오를수록 나빠진다
    if train_slope < 0 and val_slope > 0:
        return "overfit", (f"최근 학습 {window}번: 학습 손실은 줄어드는데 내부 검증은 나빠진다 "
                           "(학습 자료를 외우기 시작했을 수 있다)")
    return "ok", f"{base} · 최근 학습 {window}번 내부 검증이 나빠지지 않는다"


#: 시행 → 그 시행 도구의 스크립트 이름. 도는 중인데 아직 진행 기록이 없는 시행을 "대기" 가 아니라
#: "시작됨(첫 기록 전)" 으로 보이려고 쓴다(2026-09-29 사용자: "왜 BG 가 대시보드에 안 뜨지" — BG 는 폴드가
#: 끝날 때만 적어 첫 기록까지 수십 분 걸린다).
#: 시장마다 **따로** 학습·기록하는 시행과 그 시장 수(러너가 --markets 로 준다). 이런 시행은 기록 한 줄이
#: (시드, 시장, 폴드) 하나라, 시장을 세지 않으면 분모가 절반이 되고 두 시장의 같은 폴드가 한 점에 겹친다(2026-09-29 BG).
FINAL_ROUND_SPLIT_MARKETS = {"BG": 2}

FINAL_ROUND_SCRIPTS = {
    "BE": "trial_final_transformer", "BF": "trial_final_lambdarank",
    "BG": "trial_final_residual_rl", "D1": "trial_final_dfl",
}


def final_round_progress(store: Store, *, as_of: datetime, lookback: int = 30,
                         config_store: Store | None = None,
                         running_scripts: set[str] | None = None) -> dict[str, Any]:
    """마지막 모델 회차(BE·BF·BG·D1·C0·C1)의 **학습 진행**. 판정 창 지표는 담지 않는다.

    담는 것: 무엇을 배우나(한 줄), 진행 위치(시드 x/n · 블록 y/n), 예상 끝 시각(한국시간·as_of 기준 말),
    경과 시간, 상태(정상·느림/멈춤 의심·과적합 의심·끝남)와 이유 한 줄, 학습 손실·내부 검증(`kit.inner_split` 쪽).
    아직 기록이 없는 시행은 등록 순서대로 `queued` 에 선다(날짜 약속 없음).
    판정이 끝난 시행은 `research_trials` 의 그 줄을 **그대로** 붙인다 — 화면이 판정을 다시 계산하지 않는다.

    임계치(멈춤 배수·추세 창)는 `config_store`(없으면 `store`) 의 config 에서 읽는다(불변식 10).
    모든 시각은 as_of 로 잰다 — 벽시계를 읽지 않으므로 되감으면 그때의 상태가 나온다(불변식 2·9).

    0행은 "학습을 안 돌렸다" 이고 "돌렸는데 진행이 없다" 와 다른 사실이다(`has_data`).
    """
    frame = store.get(TRIAL_PROGRESS, as_of=as_of, lookback=lookback)
    verdicts = _final_round_verdicts(store, as_of=as_of, lookback=max(lookback, 120))
    settings = _training_settings(config_store or store, as_of)
    now = _ts(as_of)
    judged = {v["entity_id"].split(":")[-1] for v in verdicts}

    def queue(seen: set[str]) -> list[dict[str, Any]]:
        # 기록이 있거나 판정이 적힌 시행은 대기가 아니다(기록이 조회 창 밖으로 밀려도 판정 줄이 남는다).
        # `running_scripts` 는 **라이브 화면에서만** 준다(지금 /proc 에 뜬 스크립트) — 되감은 화면은 None 이라
        # 그때의 도는 여부를 지어내지 않는다.
        running = running_scripts or set()
        return [{"trial": name, "about": _about(name),
                 "started": FINAL_ROUND_SCRIPTS.get(name, "") in running}
                for name in FINAL_ROUND_QUEUE
                if not any(t.startswith(name) for t in seen | judged)]

    if frame.empty:
        return {"has_data": False, "trials": [], "queued": queue(set()), "verdicts": verdicts,
                "settings": settings}

    rows = _progress_rows(frame)
    trials: list[dict[str, Any]] = []
    for trial, part in rows.groupby("entity_id", sort=False):
        part = part.sort_values("valid_from").tail(PROGRESS_ROW_CAP)
        fold_axis = "block" not in part.columns or part["block"].isna().all()
        axis = "fold" if fold_axis else "block"
        unit = "폴드" if fold_axis else "블록"
        total_col = "n_folds" if fold_axis else "n_blocks"
        per_seed = int(part[total_col].dropna().max()) if total_col in part.columns and part[total_col].notna().any() else None
        # 분모는 **기록이 말하는 수**다(n_seeds). 관측된 시드 수로 세면 3시드만 시작한 회차가 완주로 보인다.
        n_seeds = _int(part["n_seeds"].dropna().max()) if "n_seeds" in part.columns and part["n_seeds"].notna().any() else None
        seeds_seen = sorted({v for s in part["seed"] if (v := _int(s)) is not None})
        done = len(part)
        split = FINAL_ROUND_SPLIT_MARKETS.get(str(trial)[:2], 1)
        total = (per_seed * n_seeds * split) if (per_seed and n_seeds) else None
        elapsed = [v for v in (_num(v) for v in part["elapsed_s"]) if v is not None and v > 0]
        mean_unit_s = sum(elapsed[-20:]) / len(elapsed[-20:]) if elapsed else None
        slowest_unit_s = max(elapsed[-20:]) if elapsed else None
        last_ts = _ts(part["valid_from"].max())
        # 시작 = 첫 기록 시각 − 그 단위가 걸린 시간. 단위는 기록 **전에** 돌았다.
        started = min(_ts(at) - timedelta(seconds=(_num(el) or 0.0))
                      for at, el in zip(part["valid_from"], part["elapsed_s"], strict=False))
        early = [bool(v) for v in part["stopped_early"] if not pd.isna(v)]
        metric = next((str(m) for m in reversed(list(part["metric"])) if m and not pd.isna(m)), "")
        score_label, flip = _score_axis(metric)
        curves: list[dict[str, Any]] = []
        latest_curve: dict[str, Any] | None = None
        latest_at: pd.Timestamp | None = None
        keys = ["seed", "market"] if split > 1 else ["seed"]
        for key, grp in part.groupby(keys, dropna=False, sort=True):
            seed = key[0] if isinstance(key, tuple) else key
            grp = grp.sort_values(axis if axis in grp.columns else "valid_from")
            val = [_num(v) for v in grp["val_loss"]]
            curve = {
                "seed": _int(seed),
                # 시장별로 따로 배우는 시행은 선 이름에 시장을 붙인다(같은 폴드 번호가 두 시장에 있다).
                "market": (str(key[1]) if split > 1 and isinstance(key, tuple) else None),
                "x": [_int(v) for v in grp[axis]] if axis in grp.columns else list(range(len(grp))),
                "train": [_num(v) for v in grp["train_loss"]],
                "val": val,
                # 사람이 읽는 방향(높을수록 좋음)으로 되돌린 내부 검증. 뒤집힌 지표가 아니면 val 그대로.
                "score": [(-v if (flip and v is not None) else v) for v in val],
            }
            curves.append(curve)
            seed_last = _ts(grp["valid_from"].max())
            if latest_at is None or seed_last > latest_at:
                latest_curve, latest_at = curve, seed_last
        # 남은 단위 × 평균 단위 시간. 총량을 모르면 예상 완료를 **말하지 않는다**(짐작한 분모로 낸
        # 완료 시각은 화면에서 사실과 구분되지 않는다).
        remaining = (total - done) if total is not None else None
        eta_s = (remaining * mean_unit_s) if (remaining is not None and remaining > 0 and mean_unit_s) else None
        is_done = total is not None and done >= total
        since_last = (now - last_ts).total_seconds()
        status, reason = _health(done=is_done, since_last_s=since_last, mean_unit_s=mean_unit_s, unit=unit,
                                 slowest_unit_s=slowest_unit_s, latest_curve=latest_curve, settings=settings, last_at=last_ts, as_of=as_of)
        # 끝 시각 = 마지막 기록 + 남은 양(지금 도는 단위는 마지막 기록 직후 시작했다). 멈춤 의심이면
        # 그 시각은 이미 지났거나 믿을 수 없으니 말하지 않는다.
        eta_at = (last_ts + timedelta(seconds=eta_s)) if (eta_s is not None and status != "stalled") else None
        trials.append({
            "trial": str(trial),
            "kind": "control" if str(trial) in FINAL_ROUND_CONTROLS else "model",
            "about": _about(str(trial)),
            "axis": axis,
            "markets": sorted({str(m) for m in part["market"] if m}),
            "metric": metric,
            "score_label": score_label,
            "seeds": seeds_seen,
            "n_seeds": n_seeds,
            "units_per_seed": per_seed,
            "units_done": done,
            "units_total": total,
            "progress": (done / total) if total else None,
            "mean_unit_s": mean_unit_s,
            "eta_seconds": eta_s,
            "eta_at": eta_at.isoformat() if eta_at is not None else None,
            "eta_label": _kst_label(eta_at, as_of) if eta_at is not None else None,
            "started_at": started.isoformat(),
            "elapsed_wall_s": max(((last_ts if is_done else now) - started).total_seconds(), 0.0),
            "last_at": last_ts.isoformat(),
            "last_label": _kst_label(last_ts, as_of),
            "since_last_s": since_last,
            "status": status,
            "status_reason": reason,
            "early_share": (sum(early) / len(early)) if early else None,
            "early_n": len(early),
            "curves": curves,
            "last_note": next((str(n) for n in reversed(list(part["note"])) if n and not pd.isna(n)), ""),
        })
    # 지금 도는 것 → 끝난 것. 같은 무리 안에서는 마지막으로 기록을 남긴 시행이 위다(training_runs 와 같은 규칙).
    trials.sort(key=lambda t: t["last_at"], reverse=True)
    trials.sort(key=lambda t: t["status"] == "done")
    return {"has_data": True, "trials": trials, "queued": queue({t["trial"] for t in trials}),
            "verdicts": verdicts, "settings": settings}


def _final_round_verdicts(store: Store, *, as_of: datetime, lookback: int) -> list[dict[str, Any]]:
    """`research_trials` 에 적힌 이 회차의 판정 줄. **여기서 판정을 계산하지 않는다** — 그대로 옮긴다."""
    frame = store.get("research_trials", as_of=as_of, lookback=lookback)
    if frame.empty:
        return []
    frame = frame[frame["entity_id"].astype(str).str.startswith(FINAL_ROUND_ENTITY)]
    out: list[dict[str, Any]] = []
    for _, row in frame.sort_values("valid_from").iterrows():
        out.append({
            "entity_id": str(row["entity_id"]),
            "family": str(row.get("family") or ""),
            "protocol_hash": str(row.get("protocol_hash") or ""),
            "at": str(row["valid_from"]),
            "detail": str(row.get("detail") or ""),
        })
    return out
