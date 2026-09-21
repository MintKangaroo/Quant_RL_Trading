"""시행 AF — 종목 상한을 얼마나 풀 것인가. docs/protocols/cap-relaxation-2026-09.md.

    .venv/bin/python tools/trial_cap_relax.py [--save]

시행 AD 의 D1(K200 · 유동시총 가중 · 랭커 하위 10% 제외)을 그대로 두고 **상한만** 바꾼다.
게이트 다섯 중 셋이 위험이다: MDD · 최악 20세션 블록 · **상위 2종목 합계 비중 최대 ≤ 60%**.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import UTC, datetime
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

PROTOCOL = Path("docs/protocols/cap-relaxation-2026-09.md")
SPAN, CUT, REENTRY = 5, 0.10, 0.10   # 하위 10% 제외 — 시행 AD 의 D1 고정
#: 이름: 종목 상한. None 이면 지수 비중 그대로.
CAPS: dict[str, float | None] = {"F10": 0.10, "F15": 0.15, "F20": 0.20, "F25": 0.25, "F35": 0.35, "FIDX": None}
WORST_BLOCK = 20
GATE_EXCESS, GATE_T, GATE_MDD, GATE_WORST, GATE_TOP2 = 0.005, 2.0, 0.02, 0.02, 0.60


def worst_block(daily: pd.Series) -> float:
    rolled = (1 + daily).rolling(WORST_BLOCK).apply(np.prod, raw=True) - 1
    return float(rolled.min())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="data")
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args(argv)
    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16]
    print(f"=== 시행 AF — {PROTOCOL} (해시 {digest}) ===", flush=True)
    store = Store(root=Path(args.root))

    trad_frame = pd.read_pickle(CACHE / "tradable-KR.pkl")  # invariant-allow: data-access — 작업 캐시
    trad_frame["session"] = pd.to_datetime(trad_frame["session"]).dt.date
    trad_frame = trad_frame[(trad_frame["session"] >= JUDGE_START) & (trad_frame["session"] <= JUDGE_END)]
    trad = {day: set(part["entity_id"]) for day, part in trad_frame.groupby("session")}
    sessions = sorted(trad)
    del trad_frame
    ranker = _scores(store, "ranker", sessions).ewm(span=SPAN).mean()
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

    series: dict[str, pd.Series] = {"IDX": (bench - ETF_FEE / ANN).reindex(sessions).dropna()}
    extra: dict[str, dict[str, float]] = {"IDX": {}}

    for name, cap_limit in CAPS.items():
        excluded: set[str] = set()
        prev = None
        prev_keep: frozenset[str] = frozenset()
        out, turns, effs, maxw, top2w = {}, [], [], [], []
        for day in sessions:
            if day not in ranker.index or day not in ret.index or day not in caps.index:
                continue
            pool = ranker.loc[day].dropna()
            pool = pool[pool.index.isin(trad[day] & members[day])]
            if len(pool) < 50:
                continue
            pct = pool.rank(pct=True)
            excluded = {e for e in excluded if e in pct.index and pct[e] < CUT + REENTRY} | set(pct[pct < CUT].index)
            keep = [e for e in pool.index if e not in excluded]
            if not keep:
                continue
            # 편입·제외가 바뀔 때만 재조정(시행 AD 와 같은 규칙). 상한을 크게 벗어나면 그때도 맞춘다.
            limit = cap_limit if cap_limit is not None else 1.0
            same = prev is not None and frozenset(keep) == prev_keep and float(prev.max()) <= limit + 0.02
            if same:
                w = prev
            else:
                fcap = (caps.loc[day].reindex(keep) * fl.reindex(keep).fillna(fl.median())).dropna()
                if len(fcap) < 30:
                    w = pd.Series(1.0 / len(keep), index=keep)
                elif cap_limit is None:
                    w = fcap / fcap.sum()          # 지수 비중 그대로
                else:
                    w = capped_cap_weights(fcap, cap_limit)
            prev_keep = frozenset(keep)
            dr = ret.loc[day].reindex(w.index).fillna(0.0)
            t = 0.0 if same else (float(w.subtract(prev, fill_value=0.0).abs().sum()) if prev is not None else 1.0)
            out[day] = float((w * dr).sum() - ONE_WAY_COST * t)
            turns.append(t); effs.append(1.0 / float((w * w).sum())); maxw.append(float(w.max()))
            top2w.append(float(w.sort_values(ascending=False).head(2).sum()))
            drift = w * (1 + dr)
            prev = drift / drift.sum() if drift.sum() > 0 else w
        series[name] = pd.Series(out).sort_index()
        extra[name] = {"turn": float(np.mean(turns) * ANN), "effn": float(np.mean(effs)),
                       "maxw": float(np.mean(maxw)), "maxw_max": float(np.max(maxw)),
                       "top2": float(np.mean(top2w)), "top2_max": float(np.max(top2w))}
        print(f"  {name}: 연 {series[name].mean() * ANN:+.1%} · 상위2 평균 {np.mean(top2w):.1%} "
              f"(최대 {np.max(top2w):.1%}) · 회전 {np.mean(turns) * ANN:.1f}", flush=True)

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
        m["worst"] = worst_block(s)
        rows[name] = {**extra.get(name, {}), **m}

    bench_series = bench.reindex(series["IDX"].index).fillna(0.0)
    bnav = (1 + bench_series).cumprod()
    bench_mdd = float((bnav / bnav.cummax() - 1).min())
    bench_worst = worst_block(bench_series)
    lines = [f"K200 같은 창: 연 {bench_series.mean() * ANN:+.1%} · MDD {bench_mdd:.1%} · 최악20 {bench_worst:.1%}", "",
             "| 변형 | 상한 | 연수익 | 박스 초과 | 급등 초과 | 추적오차 | IR | 샤프 | MDD | 최악20 | β | 비대칭 | 회전 | 유효N | 최대비중(평균/최대) | 상위2(평균/최대) |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name in [*CAPS, "IDX"]:
        m = rows[name]
        cap = CAPS.get(name)
        label = "—" if name == "IDX" else ("없음" if cap is None else f"{cap:.0%}")
        lines.append(f"| {name} | {label} | {m['ann']:+.1%} | {m['box_excess']:+.1%} | {m['rally_excess']:+.1%} | "
                     f"{m['te']:.1%} | {m['ir']:+.2f} | {m['sharpe']:+.2f} | {m['mdd']:.1%} | {m['worst']:.1%} | "
                     f"{m['beta']:.2f} | {m['asym']:+.3f} | {m.get('turn', float('nan')):.1f} | "
                     f"{m.get('effn', float('nan')):.0f} | {m.get('maxw', float('nan')):.0%}/{m.get('maxw_max', float('nan')):.0%} | "
                     f"{m.get('top2', float('nan')):.0%}/{m.get('top2_max', float('nan')):.0%} |")
    lines.append("")
    passed = []
    for name in CAPS:
        m = rows[name]
        t = float(ic_module.newey_west_t((series[name] - series["IDX"]).dropna(), lag=4))
        c = (m["box_excess"] >= GATE_EXCESS and m["rally_excess"] >= GATE_EXCESS,
             t >= GATE_T,
             m["mdd"] >= bench_mdd - GATE_MDD,
             m["worst"] >= bench_worst - GATE_WORST,
             m["top2_max"] <= GATE_TOP2)
        mark = lambda ok: "○" if ok else "×"  # noqa: E731
        lines.append(f"{name}: ①두 국면 초과 ({m['box_excess']:+.1%}/{m['rally_excess']:+.1%}) {mark(c[0])} · "
                     f"②NW t {t:+.2f} {mark(c[1])} · ③MDD {m['mdd']:.1%} {mark(c[2])} · "
                     f"④최악20 {m['worst']:.1%} {mark(c[3])} · ⑤상위2 최대 {m['top2_max']:.0%} {mark(c[4])} "
                     f"→ {'통과' if all(c) else '탈락'}")
        if all(c):
            passed.append((m["ir"], name))
    verdict = f"채택 {max(passed)[1]}" if passed else "기각 — 상한 10% 유지"
    lines.append(f"판정: {verdict}")
    print("\n" + "\n".join(lines), flush=True)
    if args.save:
        now = datetime.now(UTC)  # invariant-allow: wallclock — 시행 기록 시각
        store.append("research_trials", [{
            "entity_id": "cap-relaxation-2026-09:AF", "valid_from": now, "observed_at": now,
            "source": "trial_cap_relax", "market": "KR", "family": "selection", "n_trials": 1,
            "protocol_hash": digest, "detail": (f"{verdict} | " + " | ".join(lines[4:]))[:900],
        }], ingest_run_id=f"trial-cap-relax-AF-{now:%Y%m%dT%H%M%S}")
        print(f"research_trials 기록: selection/AF · protocol {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
