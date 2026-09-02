"""베타 정렬 선정 — docs/protocols/beta-aligned-selection-2026-09.md 대로 잰다.

    .venv/bin/python tools/trial_beta_select.py
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

PROTOCOL = Path("docs/protocols/beta-aligned-selection-2026-09.md")
BETA_WINDOW = 120
BETA_MIN_OBS = 60
BAND = (0.9, 1.1)
TERCILE_PICK = 8


def rolling_beta(ret_all: pd.DataFrame, idx_ret_all: pd.Series) -> pd.DataFrame:
    """직전 BETA_WINDOW 세션 OLS β (t−1 까지). 행 = 날, 열 = 종목."""
    idx = idx_ret_all.reindex(ret_all.index)
    cov = ret_all.rolling(BETA_WINDOW, min_periods=BETA_MIN_OBS).cov(idx)
    var = idx.rolling(BETA_WINDOW, min_periods=BETA_MIN_OBS).var()
    return (cov.div(var, axis=0)).shift(1)


def select(variant: str, f: pd.Series, beta: pd.Series) -> pd.Index:
    if variant == "BASE":
        return f.nlargest(TOP_N).index
    b = beta.reindex(f.index).dropna()
    f = f.loc[b.index]
    if f.empty:
        return pd.Index([])
    if variant == "B1":
        q = pd.qcut(b.rank(method="first"), 3, labels=False)
        picks = [f[q == k].nlargest(TERCILE_PICK).index for k in range(3)]
        return pd.Index(np.concatenate([p.to_numpy() for p in picks]))
    if variant == "B2":
        chosen: list[str] = []
        total = 0.0
        for name, _score in f.sort_values(ascending=False).items():
            n = len(chosen) + 1
            new_beta = (total + b[name]) / n
            # 24 를 채우기 전엔 밴드 위/아래로 잠시 벗어날 수 있다 — 채워진 뒤 평균이 밴드 안이면 된다.
            # 그래서 "지금 넣으면 남은 자리로도 밴드로 못 돌아오는" 종목만 건너뛴다: 후보 β 범위 [0.3, 2.0] 가정.
            remaining = TOP_N - n
            lo = (new_beta * n + 0.3 * remaining) / TOP_N
            hi = (new_beta * n + 2.0 * remaining) / TOP_N
            if hi < BAND[0] or lo > BAND[1]:
                continue
            chosen.append(name); total += b[name]
            if n == TOP_N:
                break
        return pd.Index(chosen)
    if variant == "B3":
        x = b.to_numpy(); y = f.to_numpy()
        X = np.column_stack([np.ones_like(x), x])
        coef, *_ = np.linalg.lstsq(X, y, rcond=None)
        resid = pd.Series(y - X @ coef, index=f.index)
        return resid.nlargest(TOP_N).index
    raise ValueError(variant)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="data")
    args = parser.parse_args(argv)
    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16]
    print(f"=== 베타 정렬 선정 — {PROTOCOL} (해시 {digest}) ===")
    store = Store(root=Path(args.root))
    cal = _pkl("calendar")
    sessions = sorted(d for d in cal["session"] if d < HOLDOUT_START)
    probe = datetime.combine(sessions[-1], time(16, 0), tzinfo=UTC)
    risk_floor = float(store.config("selector.risk_floor_percentile", as_of=probe))

    fund = _pkl("scores-fundamental"); risk = _pkl("scores-risk"); trad = _pkl("tradable")
    wide = _prices(store, sessions)
    idx = _index(store, sessions)
    ret_all = (wide / wide.shift(1) - 1.0).where(lambda r: r.abs() <= MAX_MOVE)
    idx_ret_all = idx / idx.shift(1) - 1.0
    beta = rolling_beta(ret_all, idx_ret_all)
    fwd = (wide.shift(-2) / wide.shift(-1) - 1.0).where(lambda r: r.abs() <= MAX_MOVE)
    idx_fwd = idx.shift(-2) / idx.shift(-1) - 1.0
    print(f"세션 {len(sessions)} · risk 컷 {risk_floor:.0%} · β 창 {BETA_WINDOW} · 가격 {wide.index.min()}~")

    variants = ("BASE", "B1", "B2", "B3")
    series = {v: {} for v in variants}
    port_beta = {v: [] for v in variants}
    n_pick = {v: [] for v in variants}
    prev = {v: None for v in variants}
    for day in sessions:
        f = fund[fund["session"] == day].set_index("entity_id")["score"]
        r = risk[risk["session"] == day].set_index("entity_id")["score"]
        ok = set(trad[trad["session"] == day]["entity_id"])
        f = f[f.index.isin(ok) & f.index.isin(r.index) & f.index.isin(fwd.columns)]
        if f.empty or day not in fwd.index or day not in beta.index:
            continue
        r = r.reindex(f.index)
        f = f.loc[r[r >= r.quantile(risk_floor)].index]
        b_day = beta.loc[day]
        for v in variants:
            picks = select(v, f, b_day)
            if len(picks) == 0:
                continue
            w = pd.Series(1.0 / len(picks), index=picks)
            dr = fwd.loc[day].reindex(picks).fillna(0.0)
            p = prev[v]
            turnover = w.subtract(p, fill_value=0.0).abs().sum() if p is not None else w.sum()
            series[v][day] = float((w * dr).sum() - ONE_WAY_COST * turnover)
            port_beta[v].append(float(b_day.reindex(picks).mean()))
            n_pick[v].append(len(picks))
            drift = w * (1.0 + dr); prev[v] = drift / drift.sum() if drift.sum() > 0 else w
    bench = pd.Series({d: float(idx_fwd.get(d, np.nan)) for d in sessions})

    judge = sessions[WARMUP:]
    half = len(judge) // 2
    down_days = [d for d in judge if bench.get(d, 0) < 0]
    print(f"판정 {judge[0]}~{judge[-1]} ({len(judge)}세션) · 지수 하락일 {len(down_days)}")
    bm = metrics(bench.loc[judge], bench)
    print(f"KOSPI200: 연 {bm['ann']:+.1%} · MDD {bm['mdd']:+.1%}")
    rows = {}
    for v in variants:
        s = pd.Series(series[v]).reindex(judge)
        m = metrics(s, bench)
        ex = (s - bench.reindex(judge)).dropna()
        h1 = metrics(s.iloc[:half], bench)["ir"]; h2 = metrics(s.iloc[half:], bench)["ir"]
        dd = float((s.reindex(down_days) - bench.reindex(down_days)).mean())
        rows[v] = {**m, "excess": float(ex.mean() * ANN), "ir_h1": h1, "ir_h2": h2, "down_ex": dd,
                   "pbeta": float(np.nanmean(port_beta[v])), "n": float(np.mean(n_pick[v]))}
    df = pd.DataFrame(rows).T
    print(f"\n{'변형':5} {'연수익':>8} {'초과':>8} {'IR':>6} {'MDD':>8} {'β(포트)':>7} {'실현β':>6} {'IR전':>6} {'IR후':>6} {'하락일초과/일':>12} {'종목수':>6}")
    for v, m in df.iterrows():
        print(f"{v:5} {m['ann']:+8.1%} {m['excess']:+8.1%} {m['ir']:+6.2f} {m['mdd']:+8.1%} {m['pbeta']:7.2f} {m['beta']:6.2f} {m['ir_h1']:+6.2f} {m['ir_h2']:+6.2f} {m['down_ex']*1e4:+12.1f}bp {m['n']:6.1f}")
    base = df.loc["BASE"]
    print()
    for v, m in df.iterrows():
        if v == "BASE":
            continue
        c1 = m["ir"] >= base["ir"] + 0.5
        c2 = m["mdd"] > bm["mdd"]
        c3 = m["ir_h1"] >= base["ir_h1"] and m["ir_h2"] >= base["ir_h2"]
        c4 = m["down_ex"] >= base["down_ex"] - 0.0005
        ok = c1 and c2 and c3 and c4
        print(f"{v:5} ① IR+0.5 {'○' if c1 else '×'}  ② MDD<지수 {'○' if c2 else '×'}  ③ 전·후반 {'○' if c3 else '×'}  ④ 하락일 {'○' if c4 else '×'}  → {'채택 후보' if ok else '기록만'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
