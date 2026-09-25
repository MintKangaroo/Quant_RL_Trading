"""시행 AZ — 국장 인핸스드 인덱스: KOSPI200 유동시총 비중을 랭커 점수로 기울인다. docs/protocols/kr-index-tilt-2026-09.md.

    .venv/bin/python tools/trial_kr_index_tilt.py --precheck   # 액티브 셰어만(수익 없음)
    .venv/bin/python tools/trial_kr_index_tilt.py [--save]

점수 원천 넷 = 시행 AM·AO 와 같다(실전 랭커 백필 + 루프 GBM seed 0·1·2, `data/_diag/portfolio-variance`). EMA5 평활, 10세션 재조정(AO).
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
from scipy.stats import norm  # noqa: E402

from quant_rl_trading.allocator.float_cap_baseline import capped  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from tools.trial_overlay import ANN, ONE_WAY_COST, metrics  # noqa: E402
from tools.trial_portfolio_variance import CACHE, SEEDS  # noqa: E402
from tools.trial_ranker_kit import SPAN, market_data, record, scores_chunked  # noqa: E402
from tools.trial_ranker_ensemble import BOX_END  # noqa: E402

PROTOCOL = Path("docs/protocols/kr-index-tilt-2026-09.md")
INDEX = "KOSPI200"
CAP_LIMIT, EVERY = 0.30, 10
LAMBDAS = {"K1": 0.25, "K2": 0.50}
PRECHECK_MIN_ACTIVE = 0.05
IR_GATE, MDD_SLACK = 0.30, 0.02


def members(store: Store, sessions: list) -> dict:
    """세션별 K200 구성(그 세션 이전 가장 늦은 스냅샷)."""
    end = datetime.combine(sessions[-1], time(23), tzinfo=UTC)
    f = store.get("index_members", as_of=end, lookback=(sessions[-1] - sessions[0]).days + 30, market="KR",
                  columns=["entity_id", "valid_from", "index_id"])
    f = f[f["index_id"] == INDEX].assign(day=lambda x: pd.to_datetime(x["valid_from"]).dt.date)
    snaps = {d: set(g["entity_id"]) for d, g in f.groupby("day")}
    keys = sorted(snaps)
    out, j = {}, -1
    for s in sessions:
        while j + 1 < len(keys) and keys[j + 1] <= s:
            j += 1
        if j >= 0:
            out[s] = snaps[keys[j]]
    return out


def caps_panel(store: Store, sessions: list, names: list[str]) -> pd.DataFrame:
    end = datetime.combine(sessions[-1], time(23), tzinfo=UTC)
    f = store.get("market_stats", as_of=end, lookback=(sessions[-1] - sessions[0]).days + 30, market="KR", entity=names,
                  columns=["entity_id", "valid_from", "metric", "value"])
    f = f[f["metric"] == "market_cap"].assign(day=lambda x: pd.to_datetime(x["valid_from"]).dt.date)
    return f.pivot_table(index="day", columns="entity_id", values="value", aggfunc="last").sort_index().ffill(limit=5)


def float_ratios(store: Store, names: list[str]) -> pd.Series:
    """현재 유동비율(이력이 없다 — 시행 Z 와 같은 한계). 모르면 아는 종목 중앙값."""
    f = store.get("float_ratio", as_of=datetime.now(UTC), lookback=60, entity=names,  # invariant-allow: wallclock — 이력 없음, 현재값
                  columns=["entity_id", "float_ratio", "observed_at"])
    fr = f.sort_values("observed_at").groupby("entity_id")["float_ratio"].last().astype(float)
    return fr.reindex(names).fillna(float(fr.median()) if not fr.empty else 1.0).clip(0.01, 1.0)


def run(variant: str, score: pd.DataFrame, mem: dict, caps: pd.DataFrame, fr: pd.Series, ret: pd.DataFrame,
        trad: dict) -> tuple[pd.Series, dict]:
    prev = None
    out, turns, active = {}, [], []
    days = [d for d in score.index if d in ret.index and d in mem and d in caps.index]
    for i, day in enumerate(days):
        if prev is None or i % EVERY == 0:
            names = [e for e in mem[day] if e in caps.columns and (not trad.get(day) or e in trad[day])]
            cap = (caps.loc[day].reindex(names) * fr.reindex(names)).dropna()
            cap = cap[cap > 0]
            base = capped(cap, CAP_LIMIT)
            if variant == "K0":
                w = base
            else:
                s = score.loc[day].reindex(cap.index).dropna()
                z = pd.Series(norm.ppf((s.rank() - 0.5) / len(s)), index=s.index).reindex(cap.index).fillna(0.0)
                w = capped(cap * np.exp(LAMBDAS[variant] * z), CAP_LIMIT)
            active.append(0.5 * float(w.subtract(base, fill_value=0.0).abs().sum()))
        else:
            w = prev
        dr = ret.loc[day].reindex(w.index).fillna(0.0)
        t = float(w.subtract(prev, fill_value=0.0).abs().sum()) if prev is not None else 1.0
        out[day] = float((w * dr).sum() - ONE_WAY_COST * t)
        turns.append(t)
        d = w * (1 + dr)
        prev = d / d.sum() if d.sum() > 0 else w
    return pd.Series(out).sort_index(), {"turn": float(np.mean(turns) * ANN), "active": float(np.mean(active))}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--precheck", action="store_true")
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args(argv)
    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16]
    print(f"=== 시행 AZ — {PROTOCOL} (해시 {digest}) ===", flush=True)
    store = Store(root=Path("data"))
    sessions = list(pd.read_pickle(CACHE / "sessions.pkl"))  # invariant-allow: data-access — AM loop 캐시
    sources = {f"loop{s}": pd.read_pickle(CACHE / f"loop-seed{s}.pkl") for s in SEEDS}  # invariant-allow: data-access — AM loop 캐시
    start = min(p["session"].min() for p in sources.values())
    sessions = [s for s in sessions if s >= start]
    live = scores_chunked(store, "ranker", sessions).stack().rename("pred").reset_index()
    live.columns = ["session", "entity_id", "pred"]
    sources = {"live": live[["entity_id", "session", "pred"]], **sources}
    ret, bench, trad = market_data(store, sessions)
    mem = members(store, sessions)
    print(f"판정 세션 {len(sessions)} · K200 스냅샷이 있는 세션 {len(mem)}", flush=True)
    if len(mem) < 0.9 * len(sessions):
        print("K200 구성 이력이 판정 창을 못 덮는다 — 측정하지 않는다(backfill 먼저)", flush=True)
        return 2
    names = sorted(set().union(*mem.values()))
    caps = caps_panel(store, sessions, names)
    fr = float_ratios(store, names)
    variants = ("K0", "K1", "K2")
    res: dict[str, dict[str, dict]] = {v: {} for v in variants}
    for src, frame in sources.items():
        score = frame.pivot_table(index="session", columns="entity_id", values="pred").sort_index().ewm(span=SPAN).mean()
        if args.precheck:
            score = score.iloc[:200]
        for v in variants:
            daily, extra = run(v, score, mem, caps, fr, ret, trad)
            b = bench.reindex(daily.index).fillna(0.0)
            m = metrics(daily, b)
            for label, part in (("box", daily.index <= BOX_END), ("rally", daily.index > BOX_END)):
                m[f"{label}_ann"] = float(daily[part].mean() * ANN)
                m[f"{label}_excess"] = float((daily[part] - b[part]).mean() * ANN)
            res[v][src] = {**m, **extra}
        if args.precheck:
            for v in ("K1", "K2"):
                print(f"등록 전 점검: {v} 액티브 셰어(K200 유동시총 대비) {res[v][src]['active']:.1%} · 첫 200세션 {src} → "
                      f"{'측정 진행' if res[v][src]['active'] >= PRECHECK_MIN_ACTIVE else '측정하지 않는다(5% 미만)'}", flush=True)
            return 0

    def avg(v: str, k: str) -> float:
        return float(np.mean([res[v][s][k] for s in sources]))

    lines = ["| 변형 | 원천 | 연수익 | 박스 | 급등 | 샤프 | MDD | β | IR(K200) | 박스 초과 | 급등 초과 | 액티브 셰어 | 회전 |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for v in variants:
        for s in sources:
            m = res[v][s]
            lines.append(f"| {v} | {s} | {m['ann']:+.1%} | {m['box_ann']:+.1%} | {m['rally_ann']:+.1%} | {m['sharpe']:+.2f} | {m['mdd']:.1%} | "
                         f"{m['beta']:.2f} | {m['ir']:+.2f} | {m['box_excess']:+.1%} | {m['rally_excess']:+.1%} | {m['active']:.0%} | {m['turn']:.1f} |")
    b_all = bench.reindex(pd.Index(sorted(mem))).dropna()
    lines += ["", f"K200(가격지수) 같은 창: 연 {b_all.mean() * ANN:+.1%}", ""]
    mark = lambda ok: "○" if ok else "×"  # noqa: E731
    passed = []
    for v in ("K1", "K2"):
        wins = sum(res[v][s]["ann"] > res["K0"][s]["ann"] for s in sources)
        c = (avg(v, "ir") >= IR_GATE, avg(v, "box_excess") >= 0 and avg(v, "rally_excess") >= 0,
             wins == len(sources), avg(v, "mdd") >= avg("K0", "mdd") - MDD_SLACK)
        lines.append(f"{v}: ①IR(K200) {avg(v, 'ir'):+.2f} (≥0.30) {mark(c[0])} · ②두 국면 초과 {avg(v, 'box_excess'):+.1%}/{avg(v, 'rally_excess'):+.1%} {mark(c[1])} · "
                     f"③K0 대비 {wins}/4 {mark(c[2])} · ④MDD {avg(v, 'mdd'):.1%} vs {avg('K0', 'mdd'):.1%} {mark(c[3])} → {'통과' if all(c) else '탈락'}")
        if all(c):
            passed.append((avg(v, "ir"), v))
    verdict = f"채택 {max(passed)[1]}" if passed else "기각"
    lines.append(f"판정: {verdict}")
    print("\n" + "\n".join(lines), flush=True)
    if args.save:
        record(store, entity="kr-index-tilt-2026-09:AZ", source="trial_kr_index_tilt", family="selection",
               digest=digest, verdict=verdict, lines=lines[-4:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
