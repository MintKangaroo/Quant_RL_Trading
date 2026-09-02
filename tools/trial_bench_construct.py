"""벤치마크 정렬 구성 — docs/protocols/benchmark-aligned-construction-2026-09.md 대로 잰다.

    .venv/bin/python tools/trial_bench_construct.py
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import UTC, datetime, time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.store import Store  # noqa: E402
from tools.trial_overlay import (  # noqa: E402
    ANN, HOLDOUT_START, MAX_MOVE, ONE_WAY_COST, TOP_N, WARMUP, _index, _pkl, _prices, metrics,
)

PROTOCOL = Path("docs/protocols/benchmark-aligned-construction-2026-09.md")
CAP_TOP = 200
CORE_W = 0.8
ENH_N = 50
ENH_UP, ENH_DOWN = 1.5, 0.5


def _caps(store: Store, sessions) -> pd.DataFrame:
    now = datetime.combine(sessions[-1], time(16, 0), tzinfo=UTC)
    span = (sessions[-1] - sessions[0]).days + 40
    f = store.get("market_stats", as_of=now, lookback=span, market="KR", columns=["entity_id", "valid_from", "metric", "value"])
    f = f[f["metric"] == "market_cap"]
    f["day"] = pd.to_datetime(f["valid_from"]).dt.tz_convert("Asia/Seoul").dt.date
    return f.pivot_table(index="day", columns="entity_id", values="value", aggfunc="last").sort_index()


def weights_for(variant: str, f: pd.Series, cap: pd.Series) -> pd.Series:
    """당일 목표 비중(합 1). f = risk 컷 뒤 fundamental 점수, cap = 당일 시총."""
    if variant == "BASE":
        picks = f.nlargest(TOP_N).index
        return pd.Series(1.0 / len(picks), index=picks)
    cap = cap.dropna()
    top = cap.nlargest(CAP_TOP)
    core = top / top.sum()
    if variant == "CAP200":
        pool = f[f.index.isin(top.index)]
        picks = pool.nlargest(TOP_N).index
        w = top.reindex(picks)
        return w / w.sum()
    if variant == "CORE80":
        sat = weights_for("BASE", f, cap)
        w = core.mul(CORE_W).add(sat.mul(1 - CORE_W), fill_value=0.0)
        return w / w.sum()
    if variant == "ENH":
        pool = f.reindex(top.index).dropna()
        up = pool.nlargest(ENH_N).index
        down = pool.nsmallest(ENH_N).index
        w = core.copy()
        w.loc[w.index.isin(up)] *= ENH_UP
        w.loc[w.index.isin(down)] *= ENH_DOWN
        return w / w.sum()
    raise ValueError(variant)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="data")
    args = parser.parse_args(argv)
    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16]
    print(f"=== 벤치마크 정렬 구성 — {PROTOCOL} (해시 {digest}) ===")
    store = Store(root=Path(args.root))
    cal = _pkl("calendar")
    sessions = sorted(d for d in cal["session"] if d < HOLDOUT_START)
    probe = datetime.combine(sessions[-1], time(16, 0), tzinfo=UTC)
    risk_floor = float(store.config("selector.risk_floor_percentile", as_of=probe))
    fund = _pkl("scores-fundamental"); risk = _pkl("scores-risk"); trad = _pkl("tradable")
    wide = _prices(store, sessions); idx = _index(store, sessions); caps = _caps(store, sessions)
    fwd = (wide.shift(-2) / wide.shift(-1) - 1.0).where(lambda r: r.abs() <= MAX_MOVE)
    idx_fwd = idx.shift(-2) / idx.shift(-1) - 1.0

    variants = ("BASE", "CAP200", "CORE80", "ENH", "PROXY200")
    series = {v: {} for v in variants}; prev = {v: None for v in variants}; turn = {v: [] for v in variants}
    for day in sessions:
        if day not in fwd.index or day not in caps.index:
            continue
        f = fund[fund["session"] == day].set_index("entity_id")["score"]
        r = risk[risk["session"] == day].set_index("entity_id")["score"]
        ok = set(trad[trad["session"] == day]["entity_id"])
        f = f[f.index.isin(ok) & f.index.isin(r.index) & f.index.isin(fwd.columns)]
        if f.empty:
            continue
        r = r.reindex(f.index); f = f.loc[r[r >= r.quantile(risk_floor)].index]
        cap = caps.loc[day].reindex(fwd.columns).dropna()
        for v in variants:
            if v == "PROXY200":
                top = cap.nlargest(CAP_TOP); w = top / top.sum()
            else:
                w = weights_for(v, f, cap)
            dr = fwd.loc[day].reindex(w.index).fillna(0.0)
            p = prev[v]
            t = w.subtract(p, fill_value=0.0).abs().sum() if p is not None else w.sum()
            series[v][day] = float((w * dr).sum() - ONE_WAY_COST * t)
            turn[v].append(float(t))
            drift = w * (1.0 + dr); prev[v] = drift / drift.sum()
    bench = pd.Series({d: float(idx_fwd.get(d, np.nan)) for d in sessions})
    judge = sessions[WARMUP:]; half = len(judge) // 2
    down = [d for d in judge if bench.get(d, 0) < 0]
    bm = metrics(bench.loc[judge], bench)
    print(f"판정 {judge[0]}~{judge[-1]} ({len(judge)}세션) · KOSPI200 연 {bm['ann']:+.1%} · MDD {bm['mdd']:+.1%}")
    rows = {}
    for v in variants:
        s = pd.Series(series[v]).reindex(judge)
        m = metrics(s, bench)
        ex = (s - bench.reindex(judge)).dropna()
        rows[v] = {**m, "excess": float(ex.mean() * ANN), "te": float(ex.std() * np.sqrt(ANN)),
                   "ir_h1": metrics(s.iloc[:half], bench)["ir"], "ir_h2": metrics(s.iloc[half:], bench)["ir"],
                   "down_ex": float((s.reindex(down) - bench.reindex(down)).mean()), "turn": float(np.mean(turn[v]) * ANN)}
    df = pd.DataFrame(rows).T
    print(f"\n{'변형':8} {'연수익':>8} {'초과':>8} {'TE':>7} {'IR':>6} {'MDD':>8} {'실현β':>6} {'IR전':>6} {'IR후':>6} {'하락일초과/일':>12} {'연회전':>6}")
    for v, m in df.iterrows():
        print(f"{v:8} {m['ann']:+8.1%} {m['excess']:+8.1%} {m['te']:7.1%} {m['ir']:+6.2f} {m['mdd']:+8.1%} {m['beta']:6.2f} {m['ir_h1']:+6.2f} {m['ir_h2']:+6.2f} {m['down_ex']*1e4:+12.1f}bp {m['turn']:6.1f}")
    base = df.loc["BASE"]
    print()
    for v, m in df.iterrows():
        if v in ("BASE", "PROXY200"):
            continue
        c1 = m["ir"] > 0 and m["ir"] >= base["ir"] + 1.0
        c2 = m["excess"] >= base["excess"] + 0.20
        c3 = m["mdd"] >= bm["mdd"] - 0.02
        c4 = m["down_ex"] >= 0
        ok = c1 and c2 and c3 and c4
        print(f"{v:8} ① IR>0,+1.0 {'○' if c1 else '×'}  ② 초과+20%p {'○' if c2 else '×'}  ③ MDD {'○' if c3 else '×'}  ④ 하락일≥0 {'○' if c4 else '×'}  → {'채택 후보' if ok else '기록만'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
