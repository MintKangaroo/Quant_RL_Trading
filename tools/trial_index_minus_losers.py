"""시행 AD — 지수에서 패자만 뺀다. docs/protocols/index-minus-losers-2026-09.md.

    .venv/bin/python tools/trial_index_minus_losers.py [--save]

K200 구성종목을 유동시총 가중(상한 10%)으로 넓게 들고 랭커 **하위 q%** 만 뺀다. 판정 상대는 D0 가 아니라
**지수(D5)** 다 — 두 국면 모두 +1%p · NW t ≥ 2 · MDD · 비대칭 ≥ 0.
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
from tools.trial_beta_megacap import BOX_END, CACHE, ETF_FEE, JUDGE_END, JUDGE_START, k200_members  # noqa: E402
from tools.trial_market_beta import capture  # noqa: E402
from tools.trial_overlay import ANN, MAX_MOVE, ONE_WAY_COST, _index, _prices, metrics  # noqa: E402
from tools.trial_selection_ranker import _scores, capped_cap_weights  # noqa: E402
from tools.trial_selection_smoothing import pick_mult  # noqa: E402

PROTOCOL = Path("docs/protocols/index-minus-losers-2026-09.md")
SPAN, CAP_LIMIT, REENTRY = 5, 0.10, 0.10
N, EXIT_MULT = 24, 3
#: 이름: (제외 백분위, 유동시총 가중인가). D0·D5 는 따로 다룬다.
CUTS = {"D1": (0.10, True), "D2": (0.20, True), "D3": (0.30, True), "D4": (0.20, False)}
GATE_EXCESS, GATE_T, GATE_MDD, GATE_ASYM = 0.01, 2.0, 0.01, 0.0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="data")
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args(argv)
    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16]
    print(f"=== 시행 AD — {PROTOCOL} (해시 {digest}) ===", flush=True)
    store = Store(root=Path(args.root))

    trad_frame = pd.read_pickle(CACHE / "tradable-KR.pkl")  # invariant-allow: data-access — 작업 캐시
    trad_frame["session"] = pd.to_datetime(trad_frame["session"]).dt.date
    trad_frame = trad_frame[(trad_frame["session"] >= JUDGE_START) & (trad_frame["session"] <= JUDGE_END)]
    trad = {day: set(part["entity_id"]) for day, part in trad_frame.groupby("session")}
    sessions = sorted(trad)
    del trad_frame
    end_moment = datetime.combine(sessions[-1], time(16), tzinfo=UTC)
    floor = float(store.config("selector.risk_floor_percentile", as_of=end_moment))
    ranker = _scores(store, "ranker", sessions).ewm(span=SPAN).mean()
    risk = _scores(store, "risk", sessions)
    wide = _prices(store, sessions)
    ret = (wide.shift(-2) / wide.shift(-1) - 1.0)
    ret = ret.where(ret.abs() <= MAX_MOVE)
    idx = _index(store, sessions)
    bench = (idx.shift(-2) / idx.shift(-1) - 1.0)
    caps = _caps(store, sessions)
    fl = store.get("float_ratio", as_of=datetime.now(UTC), lookback=30)  # invariant-allow: wallclock — 참조 데이터, 최초 관측 소급(시행 P 와 같다)
    fl = fl.sort_values("observed_at").groupby("entity_id").tail(1).set_index("entity_id")["float_ratio"]
    members = k200_members(sessions)
    print(f"세션 {len(sessions)} {sessions[0]}~{sessions[-1]}", flush=True)

    series: dict[str, pd.Series] = {"D5": (bench - ETF_FEE / ANN).reindex(sessions).dropna()}
    extra: dict[str, dict[str, float]] = {"D5": {}}

    # D0 — 현행 대조(전체 유니버스 상위 24 동일가중, 위험 하한 있음)
    held: list[str] = []
    prev = None
    out = {}
    for day in sessions:
        if day not in ranker.index or day not in ret.index:
            continue
        f = ranker.loc[day].dropna()
        f = f[f.index.isin(trad[day])]
        r = risk.loc[day].reindex(f.index) if day in risk.index else pd.Series(dtype=float)
        if not r.dropna().empty:
            f = f[r >= r.quantile(floor)]
        if f.empty:
            continue
        held = pick_mult(held, f.sort_values(ascending=False).index, N, EXIT_MULT)
        w = pd.Series(1.0 / len(held), index=held)
        dr = ret.loc[day].reindex(w.index).fillna(0.0)
        t = float(w.subtract(prev, fill_value=0.0).abs().sum()) if prev is not None else 1.0
        out[day] = float((w * dr).sum() - ONE_WAY_COST * t)
        drift = w * (1 + dr)
        prev = drift / drift.sum() if drift.sum() > 0 else w
    series["D0"] = pd.Series(out).sort_index()
    extra["D0"] = {}

    for name, (cut, by_cap) in CUTS.items():
        excluded: set[str] = set()
        prev = None
        prev_keep: frozenset[str] = frozenset()
        out, turns, effs, n_out, loser_ret = {}, [], [], [], []
        for day in sessions:
            if day not in ranker.index or day not in ret.index:
                continue
            pool = ranker.loc[day].dropna()
            pool = pool[pool.index.isin(trad[day] & members[day])]
            if len(pool) < 50:
                continue
            pct = pool.rank(pct=True)
            # 완충: 컷 아래면 제외, 다시 넣는 것은 컷 + 10%p 위로 올라왔을 때만.
            excluded = {e for e in excluded if e in pct.index and pct[e] < cut + REENTRY} | set(pct[pct < cut].index)
            keep = [e for e in pool.index if e not in excluded]
            if not keep:
                continue
            # **편입·제외가 바뀔 때만 재조정한다**(등록). 그대로면 어제 비중이 드리프트한 채로 간다 —
            # 시총가중은 가격과 같이 움직이므로 매일 맞출 이유가 없다. 상한을 2%p 넘게 벗어나면 그때도 맞춘다.
            same = prev is not None and frozenset(keep) == prev_keep and float(prev.max()) <= CAP_LIMIT + 0.02
            if same:
                w = prev
            elif by_cap and day in caps.index:
                fcap = (caps.loc[day].reindex(keep) * fl.reindex(keep).fillna(fl.median())).dropna()
                w = capped_cap_weights(fcap, CAP_LIMIT) if len(fcap) >= 30 else pd.Series(1.0 / len(keep), index=keep)
            else:
                w = pd.Series(1.0 / len(keep), index=keep)
            prev_keep = frozenset(keep)
            dr = ret.loc[day].reindex(w.index).fillna(0.0)
            t = 0.0 if same else (float(w.subtract(prev, fill_value=0.0).abs().sum()) if prev is not None else 1.0)
            out[day] = float((w * dr).sum() - ONE_WAY_COST * t)
            turns.append(t); effs.append(1.0 / float((w * w).sum())); n_out.append(len(excluded))
            losers = [e for e in excluded if e in ret.columns]
            if losers:
                loser_ret.append(float(ret.loc[day].reindex(losers).mean()))
            drift = w * (1 + dr)
            prev = drift / drift.sum() if drift.sum() > 0 else w
        series[name] = pd.Series(out).sort_index()
        extra[name] = {"turn": float(np.mean(turns) * ANN), "effn": float(np.mean(effs)),
                       "n_out": float(np.mean(n_out)), "loser_ann": float(np.nanmean(loser_ret) * ANN)}

    rows: dict[str, dict[str, float]] = {}
    for name, s in series.items():
        b = bench.reindex(s.index).fillna(0.0)
        m = metrics(s, b)
        m["up"], m["down"] = capture(s, b)
        m["asym"] = m["up"] - m["down"]
        ex = s - b
        m["te"] = float(ex.std() * np.sqrt(ANN))
        m["box_excess"] = float(ex[ex.index <= BOX_END].mean() * ANN)
        m["rally_excess"] = float(ex[ex.index > BOX_END].mean() * ANN)
        rows[name] = {**extra.get(name, {}), **m}

    bnav = (1 + bench.reindex(series["D5"].index).fillna(0.0)).cumprod()
    bench_mdd = float((bnav / bnav.cummax() - 1).min())
    lines = [f"K200 같은 창: 연 {bench.reindex(series['D5'].index).mean() * ANN:+.1%} · MDD {bench_mdd:.1%}", "",
             "| 변형 | 연수익 | 박스 초과 | 급등 초과 | 추적오차 | IR | 샤프 | MDD | β | 상승 | 하락 | 비대칭 | 회전 | 유효N | 평균 제외 | 제외종목 연수익 |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name in ("D0", "D1", "D2", "D3", "D4", "D5"):
        m = rows[name]
        lines.append(f"| {name} | {m['ann']:+.1%} | {m['box_excess']:+.1%} | {m['rally_excess']:+.1%} | {m['te']:.1%} | "
                     f"{m['ir']:+.2f} | {m['sharpe']:+.2f} | {m['mdd']:.1%} | {m['beta']:.2f} | {m['up']:.2f} | {m['down']:.2f} | "
                     f"{m['asym']:+.3f} | {m.get('turn', float('nan')):.1f} | {m.get('effn', float('nan')):.0f} | "
                     f"{m.get('n_out', float('nan')):.0f} | {m.get('loser_ann', float('nan')):+.1%} |")
    lines.append("")
    passed = []
    for name in CUTS:
        m = rows[name]
        t = float(ic_module.newey_west_t((series[name] - series["D5"]).dropna(), lag=4))
        c = (m["box_excess"] >= GATE_EXCESS and m["rally_excess"] >= GATE_EXCESS, t >= GATE_T,
             m["mdd"] >= bench_mdd - GATE_MDD, m["asym"] >= GATE_ASYM)
        mark = lambda ok: "○" if ok else "×"  # noqa: E731
        lines.append(f"{name}: ①두 국면 초과 ≥+1%p ({m['box_excess']:+.1%}/{m['rally_excess']:+.1%}) {mark(c[0])} · "
                     f"②대 지수 NW t {t:+.2f} {mark(c[1])} · ③MDD {m['mdd']:.1%} {mark(c[2])} · "
                     f"④비대칭 {m['asym']:+.3f} {mark(c[3])} → {'통과' if all(c) else '탈락'}")
        if all(c):
            passed.append((m["ir"], name))
    verdict = f"채택 {max(passed)[1]}" if passed else "기각"
    lines.append(f"판정: {verdict}")
    print("\n" + "\n".join(lines), flush=True)
    if args.save:
        now = datetime.now(UTC)  # invariant-allow: wallclock — 시행 기록 시각
        store.append("research_trials", [{
            "entity_id": "index-minus-losers-2026-09:AD", "valid_from": now, "observed_at": now,
            "source": "trial_index_minus_losers", "market": "KR", "family": "selection", "n_trials": 1,
            "protocol_hash": digest, "detail": (f"{verdict} | " + " | ".join(lines[4:]))[:900],
        }], ingest_run_id=f"trial-index-minus-losers-AD-{now:%Y%m%dT%H%M%S}")
        print(f"research_trials 기록: selection/AD · protocol {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
