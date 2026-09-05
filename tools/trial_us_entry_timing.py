"""시행 T — 미장 진입 시점(키+0 대 키+1). docs/protocols/us-entry-timing-2026-09.md.

    .venv/bin/python tools/trial_us_entry_timing.py [--save]
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import UTC, date, datetime, time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.analysts import ic as ic_module  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from tools.trial_overlay import ANN, MAX_MOVE, metrics  # noqa: E402
from tools.trial_selection_pair import index_of, prices_of, scores_of, tradable_of  # noqa: E402
from tools.trial_selection_smoothing import pick_mult  # noqa: E402

PROTOCOL = Path("docs/protocols/us-entry-timing-2026-09.md")
START, END = date(2025, 9, 3), date(2026, 1, 20)
N, EXIT_MULT, COST = 24, 2, 0.0030
T_GATE, MDD_SLACK, TURN_MULT = 2.0, 0.03, 1.2


def simulate(sessions, ranker, risk, trad, ret, floor, lag):
    held: list[str] = []; prev = None; out = {}
    for i, day in enumerate(sessions):
        if i - lag < 0 or day not in ret.index:
            continue
        src = sessions[i - lag]                       # 결정은 lag 세션 전 신호로, 집행은 오늘 종가
        f = ranker.loc[src].dropna(); f = f[f.index.isin(trad.get(day, set()))]
        r = risk.loc[src].reindex(f.index) if src in risk.index else pd.Series(dtype=float)
        if not r.dropna().empty:
            f = f[r >= r.quantile(floor)]
        if f.empty:
            continue
        held = pick_mult(held, f.sort_values(ascending=False).index, N, EXIT_MULT)
        w = pd.Series(1.0 / len(held), index=held); dr = ret.loc[day].reindex(w.index).fillna(0.0)
        t = float(w.subtract(prev, fill_value=0.0).abs().sum()) if prev is not None else 1.0
        out[day] = (float((w * dr).sum()) - COST * t, t)
        drift = w * (1 + dr); prev = drift / drift.sum() if drift.sum() > 0 else w
    return pd.DataFrame(out, index=["net", "turn"]).T


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="data"); parser.add_argument("--save", action="store_true")
    args = parser.parse_args(argv)
    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16]
    print(f"=== 시행 T — {PROTOCOL} (해시 {digest}) ===", flush=True)
    store = Store(root=Path(args.root))
    rk_all = scores_of(store, "ranker", [START, END], "US"); sessions = [d for d in rk_all.index if START <= d <= END]
    ranker = rk_all.reindex(sessions); risk = scores_of(store, "risk", [START, END], "US").reindex(sessions)
    trad = tradable_of(store, sessions, "US"); wide = prices_of(store, sessions, "US")
    ret = (wide.shift(-1) / wide - 1.0); ret = ret.where(ret.abs() <= MAX_MOVE)
    spx = index_of(store, sessions, "US", "US:IDX:SP500"); spx_ret = (spx.shift(-1) / spx - 1.0)
    floor = float(store.config("selector.risk_floor_percentile", as_of=datetime.combine(END, time(23), tzinfo=UTC)))
    print(f"US 세션 {len(sessions)} ({sessions[0]}~{sessions[-1]}) · 위험 하한 {floor:.2f}", flush=True)
    res = {}
    for lag in (0, 1, 2, 3):
        fr = simulate(sessions, ranker, risk, trad, ret, floor, lag); net = fr["net"]; b = spx_ret.reindex(net.index).fillna(0.0)
        m = metrics(net, b); m["turn"] = float(fr["turn"].mean() * ANN); res[lag] = (m, net)
        print(f"시차 {lag}: 비용후 연수익 {m['ann']:+.1%} 샤프 {m['sharpe']:+.2f} MDD {m['mdd']:.1%} IR(SP500) {m['ir']:+.2f} 연회전 {m['turn']:.1f} n {m['n']}", flush=True)
    (mb, nb), (mt, nt) = res[0], res[1]
    common = nt.index.intersection(nb.index); d = (nt.reindex(common) - nb.reindex(common)).dropna(); t = float(ic_module.newey_west_t(d, lag=4))
    c1 = t >= T_GATE; c2 = mt["mdd"] >= mb["mdd"] - MDD_SLACK; c3 = mt["turn"] <= mb["turn"] * TURN_MULT
    lines = [f"① Δ연수익(시차1 − 시차0) {d.mean()*ANN:+.1%} · NW t {t:+.2f} {'○' if c1 else '×'}",
             f"② MDD 시차1 {mt['mdd']:.1%} 대 시차0 {mb['mdd']:.1%} {'○' if c2 else '×'}",
             f"③ 연회전 시차1 {mt['turn']:.1f} 대 시차0 {mb['turn']:.1f} {'○' if c3 else '×'}",
             f"판정: {'채택' if (c1 and c2 and c3) else '기각'}"]
    print("\n" + "\n".join(lines), flush=True)
    if args.save:
        now = datetime.now(UTC)  # invariant-allow: wallclock — 시행 기록 시각
        store.append("research_trials", [{
            "entity_id": "us-entry-timing-2026-09:T", "valid_from": now, "observed_at": now, "source": "trial_us_entry_timing",
            "market": "US", "family": "selection", "n_trials": 1, "protocol_hash": digest, "detail": " | ".join(lines)[:900],
        }], ingest_run_id=f"trial-us-entry-T-{now:%Y%m%dT%H%M%S}")
        print(f"research_trials 기록: selection/T · protocol {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
