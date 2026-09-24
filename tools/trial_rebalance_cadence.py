"""시행 AO — 재조정 주기를 예측 지평에 맞춘다. docs/protocols/rebalance-cadence-2026-10.md.

    .venv/bin/python tools/trial_rebalance_cadence.py [--save]

점수 원천 넷(실전 랭커 · 루프 seed 0·1·2 — 시행 AM 의 loop 캐시) × 주기 셋(C1 매일 · C5 · C10). 구성은 현행(상위 24 동일가중·완충 3N·EMA5).
GBM 학습이 없어 가볍다. **AM 판정 뒤·2026-10-02 이후에만** 돈다(캐시와 예산).
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
from tools.trial_portfolio_variance import CACHE, SEEDS  # noqa: E402
from tools.trial_ranker_kit import SPAN, market_data, record, summarize  # noqa: E402
from tools.trial_selection_ranker import _scores  # noqa: E402
from tools.trial_selection_smoothing import pick_mult  # noqa: E402

PROTOCOL = Path("docs/protocols/rebalance-cadence-2026-10.md")
MEASURE_FROM = date(2026, 9, 24)  # 당김(사용자 지시 9/24, 추석 휴장 — 예산 잠금이었지 자료 잠금이 아니다)
AM_LOG = Path("logs/trial-portfolio-variance-AM.log")
N, EXIT_MULT = 24, 3
CADENCES = {"C1": 1, "C5": 5, "C10": 10}
GATE_MEAN, GATE_COUNT, GATE_REGIME, GATE_MDD = 0.01, 3, -0.01, 0.02


def book(score: pd.DataFrame, ret: pd.DataFrame, trad: dict, every: int) -> tuple[pd.Series, dict]:
    """every 세션마다 고르고 그 사이엔 어제 비중이 드리프트한 채로 든다."""
    wide = score.pivot_table(index="session", columns="entity_id", values="pred").sort_index().ewm(span=SPAN).mean()
    held: list[str] = []
    prev = None
    out, turns, holds = {}, [], {}
    for i, day in enumerate(d for d in wide.index if d in ret.index):
        if prev is None or i % every == 0:
            row = wide.loc[day].dropna()
            ok = trad.get(day)
            if ok:
                row = row[row.index.isin(ok)]
            if row.empty:
                continue
            held = pick_mult(held, row.sort_values(ascending=False).index, N, EXIT_MULT)
            w = pd.Series(1.0 / len(held), index=held)
        else:
            w = prev
        dr = ret.loc[day].reindex(w.index).fillna(0.0)
        t = float(w.subtract(prev, fill_value=0.0).abs().sum()) if prev is not None else 1.0
        out[day] = float((w * dr).sum() - ONE_WAY_COST * t)
        turns.append(t)
        holds[day] = set(w.index)
        drift = w * (1 + dr)
        prev = drift / drift.sum() if drift.sum() > 0 else w
    return pd.Series(out).sort_index(), {"turn": float(np.mean(turns) * ANN), "holds": holds}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args(argv)
    if LiveClock().now().date() < MEASURE_FROM or "판정:" not in (AM_LOG.read_text() if AM_LOG.exists() else ""):
        print(f"AM 판정 전이거나 {MEASURE_FROM} 전 — 기다린다.", flush=True)
        return 2
    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16]
    print(f"=== 시행 AO — {PROTOCOL} (해시 {digest}) ===", flush=True)
    store = Store(root=Path("data"))
    sessions = list(pd.read_pickle(CACHE / "sessions.pkl"))  # invariant-allow: data-access — AM loop 캐시
    sources = {f"loop{s}": pd.read_pickle(CACHE / f"loop-seed{s}.pkl") for s in SEEDS}  # invariant-allow: data-access — AM loop 캐시
    start = min(p["session"].min() for p in sources.values())
    live = _scores(store, "ranker", sessions).astype("float32").stack().rename("pred").reset_index()
    live.columns = ["session", "entity_id", "pred"]
    sources = {"live": live[live["session"] >= start][["entity_id", "session", "pred"]], **sources}
    ret, bench, trad = market_data(store, sessions)

    res: dict[str, dict[str, dict]] = {c: {} for c in CADENCES}
    for c, every in CADENCES.items():
        for src, score in sources.items():
            daily, extra = book(score, ret, trad, every)
            res[c][src] = {**summarize(daily, bench), **extra}

    def agg(c: str, key: str) -> tuple[float, float]:
        v = np.array([res[c][s][key] for s in sources])
        return float(v.mean()), float(v.std(ddof=1))

    lines = ["| 주기 | 원천 | 연수익 | 박스 | 급등 | 샤프 | MDD | 회전 |", "|---|---|---|---|---|---|---|---|"]
    for c in CADENCES:
        for s in sources:
            m = res[c][s]
            lines.append(f"| {c} | {s} | {m['ann']:+.1%} | {m['box_ann']:+.1%} | {m['rally_ann']:+.1%} | {m['sharpe']:+.2f} | {m['mdd']:.1%} | {m['turn']:.1f} |")
    lines += ["", "| 주기 | 원천 평균 | 원천 간 표준편차 | 박스 | 급등 | 평균 MDD | 평균 회전 | 평균 샤프 |", "|---|---|---|---|---|---|---|---|"]
    summary = {}
    for c in CADENCES:
        a, d = agg(c, "ann")
        summary[c] = {"ann": a, "disp": d, "box": agg(c, "box_ann")[0], "rally": agg(c, "rally_ann")[0],
                      "mdd": agg(c, "mdd")[0], "turn": agg(c, "turn")[0], "sharpe": agg(c, "sharpe")[0]}
        m = summary[c]
        lines.append(f"| {c} | {m['ann']:+.1%} | {m['disp']:.1%}p | {m['box']:+.1%} | {m['rally']:+.1%} | {m['mdd']:.1%} | {m['turn']:.1f} | {m['sharpe']:+.2f} |")
    lines.append("")
    c1, passed = summary["C1"], []
    mark = lambda ok: "○" if ok else "×"  # noqa: E731
    for c in ("C5", "C10"):
        m = summary[c]
        wins = sum(res[c][s]["ann"] > res["C1"][s]["ann"] for s in sources)
        k = (m["ann"] >= c1["ann"] + GATE_MEAN, wins >= GATE_COUNT, m["disp"] <= c1["disp"],
             m["box"] >= c1["box"] + GATE_REGIME and m["rally"] >= c1["rally"] + GATE_REGIME, m["mdd"] >= c1["mdd"] - GATE_MDD)
        lines.append(f"{c}: ①평균 {m['ann'] - c1['ann']:+.1%}p {mark(k[0])} · ②{wins}/4 {mark(k[1])} · ③흩어짐 {m['disp']:.1%}p vs {c1['disp']:.1%}p {mark(k[2])} · "
                     f"④국면 {m['box'] - c1['box']:+.1%}p/{m['rally'] - c1['rally']:+.1%}p {mark(k[3])} · ⑤MDD {m['mdd']:.1%} {mark(k[4])} → {'통과' if all(k) else '탈락'}")
        if all(k):
            passed.append((m["sharpe"], c))
    verdict = f"채택 {max(passed)[1]}" if passed else "기각 — 매일 재조정 유지"
    lines.append(f"판정: {verdict}")
    print("\n" + "\n".join(lines), flush=True)
    if args.save:
        record(store, entity="rebalance-cadence-2026-10:AO", source="trial_rebalance_cadence", family="selection",
               digest=digest, verdict=verdict, lines=lines[-3:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
