"""시행 Q(K200 유니버스 제한) · 시행 R(미장 평활·완충). docs/protocols/selection-q-r-2026-09.md.

    .venv/bin/python tools/trial_selection_pair.py --trial Q [--save]
    .venv/bin/python tools/trial_selection_pair.py --trial R [--save]
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
from quant_rl_trading.collectors.market_hours import Market, trading_days  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from quant_rl_trading.store.prices import read_prices  # noqa: E402
from tools.trial_overlay import ANN, MAX_MOVE, metrics  # noqa: E402
from tools.trial_selection_smoothing import pick_mult  # noqa: E402

PROTOCOL = Path("docs/protocols/selection-q-r-2026-09.md")
N = 24
TRIALS = {
    "Q": dict(market="KR", start=date(2024, 3, 4), end=date(2025, 5, 20), cost=0.0041, index="KR:IDX:KOSPI200", entry_shift=1,
              base=("EMA5", 3, "ALL"), treat=("EMA5", 3, "K200"), turn_rule=("max_mult", 1.5), family_id="selection-q-2026-09:Q"),
    "R": dict(market="US", start=date(2025, 9, 1), end=date(2026, 6, 23), cost=0.0030, index="US:IDX:SP500", entry_shift=0,
              base=("RAW", 2, "ALL"), treat=("EMA5", 3, "ALL"), turn_rule=("max_ratio", 2 / 3), family_id="selection-r-2026-09:R"),
}
T_GATE, MDD_SLACK = 2.0, 0.03


def scores_of(store: Store, analyst: str, sessions: list[date], market: str) -> pd.DataFrame:
    now = datetime.combine(sessions[-1], time(23, 0), tzinfo=UTC)
    f = store.get("signals", as_of=now, lookback=(sessions[-1] - sessions[0]).days + 10, market=market,
                  columns=["entity_id", "valid_from", "observed_at", "analyst", "score"])
    f = f[f["analyst"] == analyst].sort_values("observed_at").groupby(["entity_id", "valid_from"], as_index=False).tail(1)
    f["session"] = f["valid_from"].dt.date
    return f.pivot_table(index="session", columns="entity_id", values="score", aggfunc="last").sort_index()


def tradable_of(store: Store, sessions: list[date], market: str) -> dict[date, set[str]]:
    now = datetime.combine(sessions[-1], time(23, 0), tzinfo=UTC)
    u = store.get("universe", as_of=now, lookback=(sessions[-1] - sessions[0]).days + 60, market=market,
                  columns=["entity_id", "valid_from", "is_listed", "is_tradable"])
    u["d"] = u["valid_from"].dt.date; u = u.sort_values("valid_from"); out = {}
    for day in sessions:
        sub = u[u["d"] <= day].groupby("entity_id").tail(1)
        out[day] = set(sub[sub["is_listed"].astype(bool) & sub["is_tradable"].astype(bool)]["entity_id"].astype(str))
    return out


def prices_of(store: Store, sessions: list[date], market: str) -> pd.DataFrame:
    now = datetime.combine(sessions[-1], time(23, 0), tzinfo=UTC)
    p = read_prices(store, as_of=now, lookback=(sessions[-1] - sessions[0]).days + 40, columns=["close"], adjusted=True, market=market)
    p["day"] = pd.to_datetime(p["valid_from"]).dt.date
    return p.pivot_table(index="day", columns="entity_id", values="close", aggfunc="last").sort_index()


def index_of(store: Store, sessions: list[date], market: str, entity: str) -> pd.Series:
    now = datetime.combine(sessions[-1], time(23, 0), tzinfo=UTC)
    f = store.get("indices", as_of=now, lookback=(sessions[-1] - sessions[0]).days + 40, market=market, columns=["entity_id", "valid_from", "close"])
    f = f[f["entity_id"] == entity]; f["day"] = pd.to_datetime(f["valid_from"]).dt.date
    return f.groupby("day")["close"].last().sort_index()


def k200_members(sessions: list[date]) -> dict[date, set[str]]:
    from tools.backfill import load_env; load_env()
    from pykrx import stock
    pts = {}
    for d in ("20240304", "20240614", "20240913", "20241213", "20250314", "20250520"):
        pts[date(int(d[:4]), int(d[4:6]), int(d[6:]))] = set("KR:" + x for x in stock.get_index_portfolio_deposit_file("1028", d))
    keys = sorted(pts)
    return {day: pts[[k for k in keys if k <= day][-1] if any(k <= day for k in keys) else keys[0]] for day in sessions}


def run(cfg, sessions, ranker, risk, trad, ret, floor, spec, members):
    kind, exit_mult, universe = spec
    scores = ranker.ewm(span=5).mean() if kind == "EMA5" else ranker
    held: list[str] = []; prev = None; out = {}
    for day in sessions:
        if day not in scores.index or day not in ret.index:
            continue
        ok = set(trad.get(day, set()))
        if universe == "K200":
            ok &= members[day]
        f = scores.loc[day].dropna(); f = f[f.index.isin(ok)]
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
    parser.add_argument("--trial", choices=sorted(TRIALS), required=True)
    parser.add_argument("--root", default="data"); parser.add_argument("--save", action="store_true")
    args = parser.parse_args(argv); cfg = TRIALS[args.trial]
    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16]
    print(f"=== 시행 {args.trial} — {PROTOCOL} (해시 {digest}) ===", flush=True)
    store = Store(root=Path(args.root)); market = cfg["market"]
    ranker_all = scores_of(store, "ranker", [cfg["start"], cfg["end"]], market)
    sessions = [d for d in ranker_all.index if cfg["start"] <= d <= cfg["end"]]
    ranker = ranker_all.reindex(sessions); risk = scores_of(store, "risk", [cfg["start"], cfg["end"]], market).reindex(sessions)
    trad = tradable_of(store, sessions, market); wide = prices_of(store, sessions, market)
    s1, s2 = cfg["entry_shift"], cfg["entry_shift"] + 1
    ret = (wide.shift(-s2) / wide.shift(-s1) - 1.0); ret = ret.where(ret.abs() <= MAX_MOVE)
    idx = index_of(store, sessions, market, cfg["index"]); idx_ret = (idx.shift(-s2) / idx.shift(-s1) - 1.0)
    floor = float(store.config("selector.risk_floor_percentile", as_of=datetime.combine(cfg["end"], time(23), tzinfo=UTC)))
    members = k200_members(sessions) if args.trial == "Q" else {}
    print(f"{market} 세션 {len(sessions)} ({sessions[0]}~{sessions[-1]}) · 위험 하한 {floor:.2f}", flush=True)
    res = {}
    for name, spec in (("대조", cfg["base"]), ("처리", cfg["treat"])):
        fr = run(cfg, sessions, ranker, risk, trad, ret, floor, spec, members)
        net = fr["gross"] - cfg["cost"] * fr["turn"]; b = idx_ret.reindex(net.index).fillna(0.0)
        m = metrics(net, b); m["turn"] = float(fr["turn"].mean() * ANN); m["gross"] = metrics(fr["gross"], b)["ann"]; res[name] = (m, net)
        print(f"{name} {spec}: 비용전 {m['gross']:+.1%} · 비용후 연수익 {m['ann']:+.1%} 샤프 {m['sharpe']:+.2f} MDD {m['mdd']:.1%} IR(지수) {m['ir']:+.2f} 연회전 {m['turn']:.1f} n {m['n']}", flush=True)
    (mb, nb), (mt, nt) = res["대조"], res["처리"]
    d = (nt - nb).dropna(); t = float(ic_module.newey_west_t(d, lag=4))
    rule, val = cfg["turn_rule"]; c3 = mt["turn"] <= mb["turn"] * val
    c1 = t >= T_GATE; c2 = mt["mdd"] >= mb["mdd"] - MDD_SLACK
    lines = [f"① Δ연수익 {d.mean()*ANN:+.1%} · NW t {t:+.2f} {'○' if c1 else '×'}",
             f"② MDD 처리 {mt['mdd']:.1%} 대 대조 {mb['mdd']:.1%} {'○' if c2 else '×'}",
             f"③ 연회전 처리 {mt['turn']:.1f} 대 대조 {mb['turn']:.1f} ({rule} {val:.2f}) {'○' if c3 else '×'}",
             f"판정: {'채택' if (c1 and c2 and c3) else '기각'}"]
    print("\n" + "\n".join(lines), flush=True)
    if args.save:
        now = datetime.now(UTC)  # invariant-allow: wallclock — 시행 기록 시각
        store.append("research_trials", [{
            "entity_id": cfg["family_id"], "valid_from": now, "observed_at": now, "source": "trial_selection_pair",
            "market": market, "family": "selection", "n_trials": 1, "protocol_hash": digest, "detail": " | ".join(lines)[:900],
        }], ingest_run_id=f"trial-selection-{args.trial}-{now:%Y%m%dT%H%M%S}")
        print(f"research_trials 기록: selection/{args.trial} · protocol {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
