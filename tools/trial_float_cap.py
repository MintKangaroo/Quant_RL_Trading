"""시행 P — 유동시총 가중 구성(종목 상한). docs/protocols/float-cap-construction-2026-09.md.

    .venv/bin/python tools/trial_float_cap.py [--save]
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

from quant_rl_trading.analysts import ic as ic_module  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from tools.trial_bench_construct import _caps  # noqa: E402
from tools.trial_overlay import ANN, HOLDOUT_START, MAX_MOVE, ONE_WAY_COST, _index, _pkl, _prices, metrics  # noqa: E402
from tools.trial_selection_ranker import _scores, capped_cap_weights  # noqa: E402
from tools.trial_selection_smoothing import pick_mult  # noqa: E402

PROTOCOL = Path("docs/protocols/float-cap-construction-2026-09.md")
N, EXIT_MULT, SPAN = 24, 3, 5
VARIANTS = {"EW": (None, None, False), "FCW10": (0.10, "float", False), "FCW15": (0.15, "float", False), "FCW10-K200": (0.10, "float", True)}
T_GATE, MDD_SLACK, EFFN_GATE = 2.0, 0.03, 12.0


def k200_members(store: Store, sessions) -> dict:
    from tools.backfill import load_env; load_env()
    from pykrx import stock
    from datetime import date
    out = {}
    for d in ("20250521", "20250915", "20251215", "20260316", "20260630"):
        out[date(int(d[:4]), int(d[4:6]), int(d[6:]))] = set("KR:" + x for x in stock.get_index_portfolio_deposit_file("1028", d))
    keys = sorted(out)
    return {day: out[[k for k in keys if k <= day][-1] if any(k <= day for k in keys) else keys[0]] for day in sessions}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="data"); parser.add_argument("--save", action="store_true")
    args = parser.parse_args(argv)
    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16]
    print(f"=== 시행 P — {PROTOCOL} (해시 {digest}) ===", flush=True)
    store = Store(root=Path(args.root))
    trad = _pkl("tradable"); sessions = sorted(d for d in trad["session"].unique() if d < HOLDOUT_START)
    end_moment = datetime.combine(sessions[-1], time(16), tzinfo=UTC)
    floor = float(store.config("selector.risk_floor_percentile", as_of=end_moment))
    ranker = _scores(store, "ranker", sessions).ewm(span=SPAN).mean(); risk = _scores(store, "risk", sessions)
    wide = _prices(store, sessions); ret = (wide.shift(-2) / wide.shift(-1) - 1.0); ret = ret.where(ret.abs() <= MAX_MOVE)
    idx = _index(store, sessions); idx_ret = (idx.shift(-2) / idx.shift(-1) - 1.0)
    caps = _caps(store, sessions)
    fl = store.get("float_ratio", as_of=datetime.now(UTC), lookback=30)  # invariant-allow: wallclock — 참조 데이터, 최초 관측 소급(등록 문서)
    fl = fl.sort_values("observed_at").groupby("entity_id").tail(1).set_index("entity_id")["float_ratio"]
    members = k200_members(store, sessions)
    print(f"세션 {len(sessions)} · 유동비율 {len(fl)}종목 · 위험 하한 {floor:.2f}", flush=True)
    rows, series = {}, {}
    for name, (cap_limit, kind, k200_only) in VARIANTS.items():
        held: list[str] = []; prev = None; out, turn, effs = {}, {}, []
        for day in sessions:
            if day not in ranker.index or day not in ret.index:
                continue
            f = ranker.loc[day].dropna(); ok = set(trad[trad["session"] == day]["entity_id"])
            if k200_only:
                ok &= members[day]
            f = f[f.index.isin(ok)]
            r = risk.loc[day].reindex(f.index) if day in risk.index else pd.Series(dtype=float)
            if not r.dropna().empty:
                f = f[r >= r.quantile(floor)]
            if f.empty:
                continue
            held = pick_mult(held, f.sort_values(ascending=False).index, N, EXIT_MULT)
            if kind == "float" and day in caps.index:
                cap = caps.loc[day].reindex(held).dropna() * fl.reindex(held).fillna(fl.median())
                w = capped_cap_weights(cap.dropna(), cap_limit) if len(cap.dropna()) >= N // 2 else pd.Series(1.0 / len(held), index=held)
                w = w.reindex(held).fillna(0.0); w = w / w.sum()
            else:
                w = pd.Series(1.0 / len(held), index=held)
            dr = ret.loc[day].reindex(w.index).fillna(0.0)
            t = float(w.subtract(prev, fill_value=0.0).abs().sum()) if prev is not None else 1.0
            out[day] = float((w * dr).sum() - ONE_WAY_COST * t); turn[day] = t; effs.append(1.0 / float((w * w).sum()))
            drift = w * (1 + dr); prev = drift / drift.sum() if drift.sum() > 0 else w
        s = pd.Series(out).sort_index(); b = idx_ret.reindex(s.index).fillna(0.0)
        m = metrics(s, b); m["turn"] = float(pd.Series(turn).mean() * ANN); m["effn"] = float(np.mean(effs)); rows[name] = m; series[name] = s
        print(f"{name:11s} 연수익 {m['ann']:+.1%} 샤프 {m['sharpe']:+.2f} MDD {m['mdd']:.1%} IR(K200) {m['ir']:+.2f} 연회전 {m['turn']:.1f} 유효N {m['effn']:.1f}", flush=True)
    base = series["EW"]; passed = []; lines = []
    for v in VARIANTS:
        if v == "EW":
            continue
        d = (series[v] - base).dropna(); t = float(ic_module.newey_west_t(d, lag=4))
        c1 = t >= T_GATE; c2 = rows[v]["mdd"] >= rows["EW"]["mdd"] - MDD_SLACK; c3 = rows[v]["effn"] >= EFFN_GATE
        lines.append(f"{v}: Δ연수익 {d.mean()*ANN:+.1%} NW t {t:+.2f} {'○' if c1 else '×'} · MDD {'○' if c2 else '×'} · 유효N {rows[v]['effn']:.1f} {'○' if c3 else '×'} → {'통과' if (c1 and c2 and c3) else '탈락'}")
        if c1 and c2 and c3:
            passed.append((rows[v]["sharpe"], v))
    lines.append(f"판정: {'채택 ' + max(passed)[1] if passed else '기각 — 동일가중 유지'}")
    print("\n" + "\n".join(lines), flush=True)
    if args.save:
        now = datetime.now(UTC)  # invariant-allow: wallclock — 시행 기록 시각
        store.append("research_trials", [{
            "entity_id": "float-cap-construction-2026-09:P", "valid_from": now, "observed_at": now, "source": "trial_float_cap",
            "market": "KR", "family": "selection", "n_trials": 1, "protocol_hash": digest,
            "detail": (" | ".join(f"{v} 샤프 {rows[v]['sharpe']:+.2f} IR {rows[v]['ir']:+.2f}" for v in VARIANTS) + " | " + " | ".join(lines))[:900],
        }], ingest_run_id=f"trial-float-cap-P-{now:%Y%m%dT%H%M%S}")
        print(f"research_trials 기록: selection/P · protocol {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
