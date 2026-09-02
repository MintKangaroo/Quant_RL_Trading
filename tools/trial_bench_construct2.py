"""벤치마크 정렬 구성 2b — docs/protocols/benchmark-aligned-construction-2b-2026-09.md 대로 잰다."""
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
from tools.trial_bench_construct import CAP_TOP, ENH_DOWN, ENH_N, ENH_UP, _caps  # noqa: E402
from tools.trial_overlay import (  # noqa: E402
    ANN, HOLDOUT_START, MAX_MOVE, ONE_WAY_COST, TOP_N, WARMUP, _index, _pkl, _prices, metrics,
)

PROTOCOL = Path("docs/protocols/benchmark-aligned-construction-2b-2026-09.md")
BETA_WINDOW = 120


def _kospi_board(store: Store, *, as_of: datetime) -> set[str]:
    """판 매핑은 정적이다(등록 문서). as_of 는 홀드아웃 직전 — 벽시계를 쓰지 않는다."""
    sh = store.get("shorting", as_of=as_of, lookback=400, market="KR", columns=["entity_id", "board"])
    return set(sh[sh["board"] == "KOSPI"]["entity_id"].unique())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="data")
    args = parser.parse_args(argv)
    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16]
    print(f"=== 벤치마크 정렬 구성 2b — {PROTOCOL} (해시 {digest}) ===")
    store = Store(root=Path(args.root))
    cal = _pkl("calendar")
    sessions = sorted(d for d in cal["session"] if d < HOLDOUT_START)
    probe = datetime.combine(sessions[-1], time(16, 0), tzinfo=UTC)
    risk_floor = float(store.config("selector.risk_floor_percentile", as_of=probe))
    fund = _pkl("scores-fundamental"); risk = _pkl("scores-risk"); trad = _pkl("tradable")
    wide = _prices(store, sessions); idx = _index(store, sessions); caps = _caps(store, sessions)
    kospi = _kospi_board(store, as_of=probe)
    print(f"KOSPI 판 매핑 {len(kospi)}종목 · 시총 열 {caps.shape[1]}")
    fwd = (wide.shift(-2) / wide.shift(-1) - 1.0).where(lambda r: r.abs() <= MAX_MOVE)
    idx_fwd = idx.shift(-2) / idx.shift(-1) - 1.0

    variants = ("BASE", "PROXY", "CAP200K", "CAPB1", "ENHK")
    series = {v: {} for v in variants}; prev = {v: None for v in variants}; turn = {v: [] for v in variants}
    lam_hist = []
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
        cap_k = cap[cap.index.isin(kospi)]
        top = cap_k.nlargest(CAP_TOP); proxy_w = top / top.sum()
        # CAP200K
        pool = f[f.index.isin(top.index)]
        picks = pool.nlargest(TOP_N).index
        capk_w = top.reindex(picks); capk_w = capk_w / capk_w.sum()
        # β̂ (t−1 까지의 CAP200K 시계열 vs 지수)
        hist = pd.Series(series["CAP200K"]); lam = 1.0
        if len(hist) >= BETA_WINDOW:
            h = hist.iloc[-BETA_WINDOW:]; b = idx_fwd.reindex(h.index)
            beta_hat = float(np.cov(h, b)[0, 1] / b.var()) if b.var() > 0 else 1.0
            lam = float(min(1.0, 1.0 / beta_hat)) if beta_hat > 0 else 1.0
        lam_hist.append(lam)
        capb1_w = capk_w.mul(lam).add(proxy_w.mul(1 - lam), fill_value=0.0)
        # ENHK
        pool2 = f.reindex(top.index).dropna()
        w = proxy_w.copy()
        w.loc[w.index.isin(pool2.nlargest(ENH_N).index)] *= ENH_UP
        w.loc[w.index.isin(pool2.nsmallest(ENH_N).index)] *= ENH_DOWN
        enhk_w = w / w.sum()
        base_p = f.nlargest(TOP_N).index; base_w = pd.Series(1.0 / len(base_p), index=base_p)
        for v, wv in (("BASE", base_w), ("PROXY", proxy_w), ("CAP200K", capk_w), ("CAPB1", capb1_w), ("ENHK", enhk_w)):
            dr = fwd.loc[day].reindex(wv.index).fillna(0.0)
            p = prev[v]
            t = wv.subtract(p, fill_value=0.0).abs().sum() if p is not None else wv.sum()
            series[v][day] = float((wv * dr).sum() - ONE_WAY_COST * t)
            turn[v].append(float(t))
            drift = wv * (1.0 + dr); prev[v] = drift / drift.sum()
    bench = pd.Series({d: float(idx_fwd.get(d, np.nan)) for d in sessions})
    judge = sessions[WARMUP:]; half = len(judge) // 2
    down = [d for d in judge if bench.get(d, 0) < 0]
    bm = metrics(bench.loc[judge], bench)
    print(f"판정 {judge[0]}~{judge[-1]} ({len(judge)}세션) · KOSPI200 연 {bm['ann']:+.1%} · MDD {bm['mdd']:+.1%} · λ 평균 {np.mean(lam_hist[WARMUP:]):.2f}")
    rows = {}
    for v in variants:
        s = pd.Series(series[v]).reindex(judge); m = metrics(s, bench)
        ex = (s - bench.reindex(judge)).dropna()
        rows[v] = {**m, "excess": float(ex.mean() * ANN), "te": float(ex.std() * np.sqrt(ANN)),
                   "ir_h1": metrics(s.iloc[:half], bench)["ir"], "ir_h2": metrics(s.iloc[half:], bench)["ir"],
                   "down_ex": float((s.reindex(down) - bench.reindex(down)).mean()), "turn": float(np.mean(turn[v]) * ANN)}
    df = pd.DataFrame(rows).T
    print(f"\n{'변형':8} {'연수익':>8} {'초과':>8} {'TE':>7} {'IR':>6} {'MDD':>8} {'실현β':>6} {'IR전':>6} {'IR후':>6} {'하락일초과/일':>12} {'연회전':>6}")
    for v, m in df.iterrows():
        print(f"{v:8} {m['ann']:+8.1%} {m['excess']:+8.1%} {m['te']:7.1%} {m['ir']:+6.2f} {m['mdd']:+8.1%} {m['beta']:6.2f} {m['ir_h1']:+6.2f} {m['ir_h2']:+6.2f} {m['down_ex']*1e4:+12.1f}bp {m['turn']:6.1f}")
    base = df.loc["BASE"]; print()
    for v, m in df.iterrows():
        if v in ("BASE", "PROXY"):
            continue
        c1 = m["ir"] > 0 and m["ir"] >= base["ir"] + 1.0
        c2 = m["excess"] >= base["excess"] + 0.20
        c3 = m["mdd"] >= bm["mdd"] - 0.02
        c4 = m["down_ex"] >= 0
        print(f"{v:8} ① {'○' if c1 else '×'}  ② {'○' if c2 else '×'}  ③ MDD {'○' if c3 else '×'}  ④ 하락일 {'○' if c4 else '×'}  → {'채택 후보' if c1 and c2 and c3 and c4 else '기록만'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
