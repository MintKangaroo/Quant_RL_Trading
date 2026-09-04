"""시행 N — ranker EMA5 + 완충 3N, 안 본 앞 구간. docs/protocols/selection-smoothing-2026-09.md.

    .venv/bin/python tools/trial_selection_smoothing.py [--save]
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

import pandas as pd  # noqa: E402

from quant_rl_trading.analysts import ic as ic_module  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market, trading_days  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from tools.trial_overlay import ANN, MAX_MOVE, ONE_WAY_COST, _index, _prices, metrics  # noqa: E402
from tools.trial_selection_ranker import _scores, pick  # noqa: E402

PROTOCOL = Path("docs/protocols/selection-smoothing-2026-09.md")
START, END = date(2024, 3, 4), date(2025, 5, 20)
N, BASE_EXIT, TREAT_EXIT, SPAN = 24, 2, 3, 5
T_GATE, MDD_SLACK, TURN_RATIO = 2.0, 0.03, 2 / 3


def _tradable(store: Store, sessions: list[date]) -> dict[date, set[str]]:
    now = datetime.combine(sessions[-1], time(16, 0), tzinfo=UTC)
    u = store.get("universe", as_of=now, lookback=(sessions[-1] - sessions[0]).days + 60, market="KR",
                  columns=["entity_id", "valid_from", "is_listed", "is_tradable"])
    u["d"] = u["valid_from"].dt.date; u = u.sort_values("valid_from")
    out: dict[date, set[str]] = {}
    for day in sessions:
        sub = u[u["d"] <= day].groupby("entity_id").tail(1)
        out[day] = set(sub[sub["is_listed"].astype(bool) & sub["is_tradable"].astype(bool)]["entity_id"].astype(str))
    return out


def pick_mult(prev: list[str], ranked: pd.Index, n: int, mult: int) -> list[str]:
    rank = {e: i for i, e in enumerate(ranked)}
    keep = sorted([e for e in prev if e in rank and rank[e] < mult * n], key=lambda e: rank[e])[:n]
    return keep + [e for e in ranked if e not in set(keep)][: n - len(keep)]


def run(sessions, scores, risk, trad, ret, floor, exit_mult):
    held: list[str] = []; prev = None; out = {}
    for day in sessions:
        if day not in scores.index or day not in ret.index:
            continue
        f = scores.loc[day].dropna(); f = f[f.index.isin(trad.get(day, set()))]
        r = risk.loc[day].reindex(f.index) if day in risk.index else pd.Series(dtype=float)
        if not r.dropna().empty:
            f = f[r >= r.quantile(floor)]
        if f.empty:
            continue
        held = pick_mult(held, f.sort_values(ascending=False).index, N, exit_mult)
        w = pd.Series(1.0 / len(held), index=held); dr = ret.loc[day].reindex(w.index).fillna(0.0)
        t = float(w.subtract(prev, fill_value=0.0).abs().sum()) if prev is not None else 1.0
        out[day] = (float((w * dr).sum()), t); drift = w * (1 + dr); prev = drift / drift.sum() if drift.sum() > 0 else w
    return pd.DataFrame(out, index=["gross", "turn"]).T


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="data"); parser.add_argument("--save", action="store_true")
    args = parser.parse_args(argv)
    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16]
    print(f"=== 시행 N — {PROTOCOL} (해시 {digest}) ===", flush=True)
    store = Store(root=Path(args.root))
    sessions = trading_days(Market.KR, START, END)
    floor = float(store.config("selector.risk_floor_percentile", as_of=datetime.combine(END, time(16), tzinfo=UTC)))
    ranker = _scores(store, "ranker", sessions); risk = _scores(store, "risk", sessions)
    smooth = ranker.ewm(span=SPAN).mean()   # 직전 세션까지의 자기 점수 — 미래 없음
    trad = _tradable(store, sessions)
    wide = _prices(store, sessions); ret = (wide.shift(-2) / wide.shift(-1) - 1.0); ret = ret.where(ret.abs() <= MAX_MOVE)
    idx = _index(store, sessions); idx_ret = (idx.shift(-2) / idx.shift(-1) - 1.0)
    print(f"세션 {len(sessions)} ({sessions[0]}~{sessions[-1]}) · ranker 세션 {ranker.index.nunique()} · 위험 하한 {floor:.2f}", flush=True)
    base = run(sessions, ranker, risk, trad, ret, floor, BASE_EXIT); treat = run(sessions, smooth, risk, trad, ret, floor, TREAT_EXIT)
    rows = {}
    for name, fr in (("대조 EW24·2N", base), ("처리 EMA5·3N", treat)):
        net = fr["gross"] - ONE_WAY_COST * fr["turn"]; b = idx_ret.reindex(net.index).fillna(0.0)
        m = metrics(net, b); g = metrics(fr["gross"], b); m["turn"] = float(fr["turn"].mean() * ANN); m["gross"] = g["ann"]; rows[name] = (m, net)
        print(f"{name:12s} 비용전 {g['ann']:+.1%} · 비용후 연수익 {m['ann']:+.1%} 샤프 {m['sharpe']:+.2f} MDD {m['mdd']:.1%} IR {m['ir']:+.2f} 연회전 {m['turn']:.1f} n {m['n']}", flush=True)
    mb, nb = rows["대조 EW24·2N"]; mt, nt = rows["처리 EMA5·3N"]
    d = (nt - nb).dropna(); t = float(ic_module.newey_west_t(d, lag=4))
    c1 = t >= T_GATE; c2 = mt["mdd"] >= mb["mdd"] - MDD_SLACK; c3 = mt["turn"] <= mb["turn"] * TURN_RATIO
    lines = [f"① Δ연수익 {d.mean()*ANN:+.1%} · NW t {t:+.2f} {'○' if c1 else '×'}",
             f"② MDD 처리 {mt['mdd']:.1%} 대 대조 {mb['mdd']:.1%} {'○' if c2 else '×'}",
             f"③ 연회전 처리 {mt['turn']:.1f} 대 대조 {mb['turn']:.1f} (≤2/3) {'○' if c3 else '×'}",
             f"판정: {'채택' if (c1 and c2 and c3) else '기각'}"]
    print("\n" + "\n".join(lines), flush=True)
    if args.save:
        now = datetime.now(UTC)  # invariant-allow: wallclock — 시행 기록 시각
        store.append("research_trials", [{
            "entity_id": "selection-smoothing-2026-09:N", "valid_from": now, "observed_at": now, "source": "trial_selection_smoothing",
            "market": "KR", "family": "selection", "n_trials": 1, "protocol_hash": digest, "detail": " | ".join(lines)[:900],
        }], ingest_run_id=f"trial-selection-N-{now:%Y%m%dT%H%M%S}")
        print(f"research_trials 기록: selection/N · protocol {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
