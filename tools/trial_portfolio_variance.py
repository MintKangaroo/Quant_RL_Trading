"""시행 AM — 포트 구성의 분산을 줄인다. docs/protocols/portfolio-variance-2026-10.md.

    .venv/bin/python tools/trial_portfolio_variance.py --stage loop            # 루프 GBM seed 0·1·2 → 캐시 (언제든 돌려도 된다 — 판정 아님)
    .venv/bin/python tools/trial_portfolio_variance.py --stage judge [--save]  # 실전 점수 적재 · 판정 (2026-10-01 이후만)

점수 원천 넷(실전 랭커 · 루프 seed 0·1·2) × 변형 셋(V0 상위 24 동일 · V1 상위 72 동일 · V2 상위 72 순위 가중).
두 단계로 나누는 이유: 신호 테이블을 전 Analyst 로 읽는 적재가 6GB 라 패널과 한 프로세스에 못 든다(2026-09-22 가드에 두 번 내려짐).
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from tools.trial_overlay import ANN, ONE_WAY_COST  # noqa: E402
from tools.trial_ranker_ensemble import FEATS  # noqa: E402
from tools.trial_ranker_kit import (  # noqa: E402
    SPAN,
    blocks,
    judge_panel,
    market_data,
    record,
    summarize,
    walk,
)
from tools.trial_selection_ranker import _scores  # noqa: E402
from tools.trial_selection_smoothing import pick_mult  # noqa: E402

PROTOCOL = Path("docs/protocols/portfolio-variance-2026-10.md")
MEASURE_FROM = date(2026, 9, 24)  # 당김(사용자 지시 9/24, 추석 휴장 — 예산 잠금이었지 자료 잠금이 아니다)
CACHE = Path("data/_diag/portfolio-variance")
SEEDS = (0, 1, 2)
EXIT_MULT = 3
VARIANTS = {"V0": (24, "equal"), "V1": (72, "equal"), "V2": (72, "rank")}
GATE_MEAN, GATE_COUNT, GATE_DISP, GATE_REGIME, GATE_MDD = 0.01, 3, 0.5, -0.01, 0.02


def stage_loop() -> int:
    CACHE.mkdir(parents=True, exist_ok=True)
    panel, sessions = judge_panel()
    bl = blocks(sessions)
    for s in SEEDS:
        pred = walk(panel, sessions, FEATS, "y5", bl, seeds=(s,), label=f"seed{s}")
        pred.to_pickle(CACHE / f"loop-seed{s}.pkl")
    panel[["entity_id", "session", "y5"]].to_pickle(CACHE / "y5.pkl")
    pd.Series(sessions).to_pickle(CACHE / "sessions.pkl")
    print(f"캐시 {CACHE} — seed {SEEDS} · 세션 {len(sessions)}", flush=True)
    return 0


def book(score: pd.DataFrame, ret: pd.DataFrame, trad: dict, n: int, weighting: str) -> tuple[pd.Series, dict]:
    """EMA5 · 완충 3N · 동일/순위 가중. trial_ranker_kit.portfolio 와 같은 규칙에 가중만 인자로."""
    wide = score.pivot_table(index="session", columns="entity_id", values="pred").sort_index().ewm(span=SPAN).mean()
    held: list[str] = []
    prev = None
    out, turns, effs, holds = {}, [], [], {}
    for day in wide.index:
        if day not in ret.index:
            continue
        row = wide.loc[day].dropna()
        ok = trad.get(day)
        if ok:
            row = row[row.index.isin(ok)]
        if row.empty:
            continue
        order = row.sort_values(ascending=False)
        held = pick_mult(held, order.index, n, EXIT_MULT)
        if weighting == "rank" and prev is not None and set(held) == set(prev.index):
            # 명단이 그대로면 어제 비중이 드리프트한 채로 간다(정정 1) — 매일 순위대로 다시 맞추면 순위 잡음이 곧 회전이다.
            w = prev
        elif weighting == "rank":
            rank = pd.Series(np.arange(1, len(order) + 1), index=order.index).reindex(held).fillna(len(order))
            raw = (n + 1 - rank).clip(lower=1.0)
            w = raw / raw.sum()
        else:
            w = pd.Series(1.0 / len(held), index=held)
        dr = ret.loc[day].reindex(w.index).fillna(0.0)
        t = float(w.subtract(prev, fill_value=0.0).abs().sum()) if prev is not None else 1.0
        out[day] = float((w * dr).sum() - ONE_WAY_COST * t)
        turns.append(t)
        effs.append(1.0 / float((w * w).sum()))
        holds[day] = set(held)
        drift = w * (1 + dr)
        prev = drift / drift.sum() if drift.sum() > 0 else w
    return pd.Series(out).sort_index(), {"turn": float(np.mean(turns) * ANN), "effn": float(np.mean(effs)), "holds": holds}


def stage_judge(save: bool) -> int:
    now = LiveClock().now()
    if now.date() < MEASURE_FROM:
        print(f"판정은 {MEASURE_FROM} 이후에만 돈다(사전등록). 지금은 --stage loop 만.", flush=True)
        return 0
    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16]
    print(f"=== 시행 AM — {PROTOCOL} (해시 {digest}) ===", flush=True)
    store = Store(root=Path("data"))
    sessions = list(pd.read_pickle(CACHE / "sessions.pkl"))  # invariant-allow: data-access — loop 단계 캐시
    sources = {f"loop{s}": pd.read_pickle(CACHE / f"loop-seed{s}.pkl") for s in SEEDS}  # invariant-allow: data-access — loop 단계 캐시
    start = min(p["session"].min() for p in sources.values())
    live = _scores(store, "ranker", sessions).astype("float32").stack().rename("pred").reset_index()
    live.columns = ["session", "entity_id", "pred"]
    sources = {"live": live[live["session"] >= start][["entity_id", "session", "pred"]], **sources}
    ret, bench, trad = market_data(store, sessions)
    print(f"원천 {list(sources)} · 판정 {start}~{sessions[-1]}", flush=True)

    res: dict[str, dict[str, dict]] = {v: {} for v in VARIANTS}
    for v, (n, weighting) in VARIANTS.items():
        for src, score in sources.items():
            daily, extra = book(score, ret, trad, n, weighting)
            res[v][src] = {**summarize(daily, bench), **extra}

    def agg(v: str, key: str) -> tuple[float, float]:
        vals = np.array([res[v][s][key] for s in sources])
        return float(vals.mean()), float(vals.std(ddof=1))

    def overlap(v: str) -> float:
        names = list(sources)
        pairs = []
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                ha, hb = res[v][a]["holds"], res[v][b]["holds"]
                pairs += [len(ha[d] & hb[d]) / max(1, len(ha[d])) for d in ha if d in hb]
        return float(np.mean(pairs))

    lines = ["| 변형 | 원천 | 연수익 | 박스 | 급등 | 샤프 | MDD | β | 회전 | 유효N |", "|---|---|---|---|---|---|---|---|---|---|"]
    for v in VARIANTS:
        for s in sources:
            m = res[v][s]
            lines.append(f"| {v} | {s} | {m['ann']:+.1%} | {m['box_ann']:+.1%} | {m['rally_ann']:+.1%} | {m['sharpe']:+.2f} | "
                         f"{m['mdd']:.1%} | {m['beta']:.2f} | {m['turn']:.1f} | {m['effn']:.0f} |")
    lines.append("")
    lines.append("| 변형 | 원천 평균 연수익 | 원천 간 표준편차 | 박스 평균 | 급등 평균 | 평균 MDD | 평균 회전 | 평균 샤프 | 원천 쌍 보유 겹침 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    summary = {}
    for v in VARIANTS:
        ann_m, ann_s = agg(v, "ann")
        summary[v] = {"ann": ann_m, "disp": ann_s, "box": agg(v, "box_ann")[0], "rally": agg(v, "rally_ann")[0],
                      "mdd": agg(v, "mdd")[0], "turn": agg(v, "turn")[0], "sharpe": agg(v, "sharpe")[0], "ovl": overlap(v)}
        m = summary[v]
        lines.append(f"| {v} | {m['ann']:+.1%} | {m['disp']:.1%}p | {m['box']:+.1%} | {m['rally']:+.1%} | {m['mdd']:.1%} | "
                     f"{m['turn']:.1f} | {m['sharpe']:+.2f} | {m['ovl']:.0%} |")
    lines.append("")
    v0 = summary["V0"]
    passed = []
    mark = lambda ok: "○" if ok else "×"  # noqa: E731
    for v in ("V1", "V2"):
        m = summary[v]
        wins = sum(res[v][s]["ann"] > res["V0"][s]["ann"] for s in sources)
        c = (m["ann"] >= v0["ann"] + GATE_MEAN, wins >= GATE_COUNT, m["disp"] <= GATE_DISP * v0["disp"],
             m["box"] >= v0["box"] + GATE_REGIME and m["rally"] >= v0["rally"] + GATE_REGIME,
             m["mdd"] >= v0["mdd"] - GATE_MDD and m["turn"] <= v0["turn"])
        lines.append(f"{v}: ①평균 {m['ann'] - v0['ann']:+.1%}p {mark(c[0])} · ②{wins}/4 원천 {mark(c[1])} · "
                     f"③흩어짐 {m['disp']:.1%}p vs V0 {v0['disp']:.1%}p {mark(c[2])} · "
                     f"④국면 {m['box'] - v0['box']:+.1%}p/{m['rally'] - v0['rally']:+.1%}p {mark(c[3])} · "
                     f"⑤MDD {m['mdd']:.1%}·회전 {m['turn']:.1f} {mark(c[4])} → {'통과' if all(c) else '탈락'}")
        if all(c):
            passed.append((m["sharpe"], v))
    verdict = f"채택 {max(passed)[1]}" if passed else "기각 — 현행 24 유지"
    lines.append(f"판정: {verdict}")
    print("\n" + "\n".join(lines), flush=True)
    if save:
        record(store, entity="portfolio-variance-2026-10:AM", source="trial_portfolio_variance", family="selection",
               digest=digest, verdict=verdict, lines=lines[-4:])
        print(f"research_trials 기록: selection/AM · protocol {digest}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stage", choices=("loop", "judge"), required=True)
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args(argv)
    return stage_loop() if args.stage == "loop" else stage_judge(args.save)


if __name__ == "__main__":
    raise SystemExit(main())
