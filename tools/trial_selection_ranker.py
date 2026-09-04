"""시행 M — ranker 점수 위의 선정(상위 N·완충·시총가중 10% 상한). docs/protocols/selection-ranker-2026-09.md.

    .venv/bin/python tools/trial_selection_ranker.py [--save]
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
from tools.trial_bench_construct import _caps  # noqa: E402
from tools.trial_overlay import ANN, HOLDOUT_START, MAX_MOVE, ONE_WAY_COST, _index, _pkl, _prices, metrics  # noqa: E402

PROTOCOL = Path("docs/protocols/selection-ranker-2026-09.md")
CAP_LIMIT = 0.10
VARIANTS = ("EW24", "EW36", "EW48", "CW24", "CW36", "EWALL")
T_GATE, MDD_SLACK, TURN_MULT = 2.0, 0.03, 1.5


def _scores(store: Store, analyst: str, sessions: list[date]) -> pd.DataFrame:
    now = datetime.combine(sessions[-1], time(16, 0), tzinfo=UTC)
    span = (sessions[-1] - sessions[0]).days + 10
    f = store.get("signals", as_of=now, lookback=span, market="KR", columns=["entity_id", "valid_from", "observed_at", "analyst", "score"])
    f = f[f["analyst"] == analyst].sort_values("observed_at").groupby(["entity_id", "valid_from"], as_index=False).tail(1)
    f["session"] = f["valid_from"].dt.date
    return f.pivot_table(index="session", columns="entity_id", values="score", aggfunc="last").sort_index()


def capped_cap_weights(cap: pd.Series, limit: float) -> pd.Series:
    w = cap / cap.sum()
    for _ in range(5):
        over = w > limit
        if not over.any():
            break
        excess = float((w[over] - limit).sum()); w[over] = limit
        under = ~over
        if w[under].sum() > 0:
            w[under] += excess * w[under] / w[under].sum()
    return w / w.sum()


def pick(prev: list[str], ranked: pd.Index, n: int) -> list[str]:
    """상위 N 진입 · 보유는 순위 ≤ 2N 이면 유지(최대 N) — 현행 완충 규칙."""
    rank = {e: i for i, e in enumerate(ranked)}
    keep = sorted([e for e in prev if e in rank and rank[e] < 2 * n], key=lambda e: rank[e])[:n]
    fill = [e for e in ranked if e not in set(keep)][: n - len(keep)]
    return keep + fill


def simulate(variant: str, sessions: list[date], ranker: pd.DataFrame, risk: pd.DataFrame, trad: pd.DataFrame,
             ret: pd.DataFrame, caps: pd.DataFrame, floor: float) -> tuple[pd.Series, pd.Series]:
    held: list[str] = []; prev_w: pd.Series | None = None; out, turn = {}, {}
    n = int(variant[2:]) if variant[2:].isdigit() else 0
    for day in sessions:
        if day not in ranker.index or day not in ret.index:
            continue
        f = ranker.loc[day].dropna(); ok = set(trad[trad["session"] == day]["entity_id"])
        f = f[f.index.isin(ok)]
        r = risk.loc[day].reindex(f.index) if day in risk.index else pd.Series(dtype=float)
        if not r.dropna().empty:
            f = f[r >= r.quantile(floor)]
        if f.empty:
            continue
        if variant == "EWALL":
            held = list(f.index)
        else:
            held = pick(held, f.sort_values(ascending=False).index, n)
        if variant.startswith("CW"):
            cap = caps.loc[day].reindex(held).dropna() if day in caps.index else pd.Series(dtype=float)
            w = capped_cap_weights(cap, CAP_LIMIT) if len(cap) >= max(3, n // 2) else pd.Series(1.0 / len(held), index=held)
        else:
            w = pd.Series(1.0 / len(held), index=held)
        day_ret = ret.loc[day].reindex(w.index).fillna(0.0)
        turnover = float(w.subtract(prev_w, fill_value=0.0).abs().sum()) if prev_w is not None else float(w.sum())
        out[day] = float((w * day_ret).sum() - ONE_WAY_COST * turnover); turn[day] = turnover
        drift = w * (1.0 + day_ret); prev_w = drift / drift.sum() if drift.sum() > 0 else w
    return pd.Series(out).sort_index(), pd.Series(turn).sort_index()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="data"); parser.add_argument("--save", action="store_true")
    args = parser.parse_args(argv)
    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16]
    print(f"=== 시행 M — {PROTOCOL} (해시 {digest}) ===", flush=True)
    store = Store(root=Path(args.root))
    trad = _pkl("tradable"); sessions = sorted(d for d in trad["session"].unique() if d < HOLDOUT_START)
    floor = float(store.config("selector.risk_floor_percentile", as_of=datetime.combine(sessions[-1], time(16), tzinfo=UTC)))
    ranker = _scores(store, "ranker", sessions); risk = _scores(store, "risk", sessions)
    wide = _prices(store, sessions); ret = (wide.shift(-2) / wide.shift(-1) - 1.0); ret = ret.where(ret.abs() <= MAX_MOVE)
    idx = _index(store, sessions); idx_ret = (idx.shift(-2) / idx.shift(-1) - 1.0)
    caps = _caps(store, sessions)
    print(f"세션 {len(sessions)} ({sessions[0]}~{sessions[-1]}) · ranker 세션 {ranker.index.nunique()} · 위험 하한 {floor:.2f}", flush=True)
    rows, series = {}, {}
    for v in VARIANTS:
        s, t = simulate(v, sessions, ranker, risk, trad, ret, caps, floor)
        b = idx_ret.reindex(s.index).fillna(0.0); m = metrics(s, b); m["turn"] = float(t.mean() * ANN); rows[v] = m; series[v] = s
        print(f"{v:6s} 연수익 {m['ann']:+.1%} 변동 {m['vol']:.1%} 샤프 {m['sharpe']:+.2f} MDD {m['mdd']:.1%} IR {m['ir']:+.2f} 연회전 {m['turn']:.1f} n {m['n']}", flush=True)
    base = series["EW24"]; verdict = []; lines = []
    for v in VARIANTS:
        if v in ("EW24", "EWALL"):
            continue
        d = (series[v] - base).dropna(); t = float(ic_module.newey_west_t(d, lag=4))
        c1 = t >= T_GATE; c2 = rows[v]["mdd"] >= rows["EW24"]["mdd"] - MDD_SLACK; c3 = rows[v]["turn"] <= rows["EW24"]["turn"] * TURN_MULT
        ok = c1 and c2 and c3
        lines.append(f"{v}: Δ연수익 {(d.mean()*ANN):+.1%} NW t {t:+.2f} {'○' if c1 else '×'} · MDD {'○' if c2 else '×'} · 회전 {'○' if c3 else '×'} → {'통과' if ok else '탈락'}")
        if ok:
            verdict.append((rows[v]["sharpe"], v))
    chosen = max(verdict)[1] if verdict else None
    lines.append(f"판정: {'채택 ' + chosen if chosen else '기각 — 현행 EW24 유지'}")
    print("\n" + "\n".join(lines), flush=True)
    if args.save:
        now = datetime.now(UTC)  # invariant-allow: wallclock — 시행 기록 시각
        store.append("research_trials", [{
            "entity_id": "selection-ranker-2026-09:M", "valid_from": now, "observed_at": now, "source": "trial_selection_ranker",
            "market": "KR", "family": "selection", "n_trials": 1, "protocol_hash": digest,
            "detail": (" | ".join(f"{v} 샤프 {rows[v]['sharpe']:+.2f} IR {rows[v]['ir']:+.2f} MDD {rows[v]['mdd']:.1%}" for v in VARIANTS) + " | " + " | ".join(lines))[:900],
        }], ingest_run_id=f"trial-selection-M-{now:%Y%m%dT%H%M%S}")
        print(f"research_trials 기록: selection/M · protocol {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
