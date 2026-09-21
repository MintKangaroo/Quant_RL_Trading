"""시행 AG — "넓게 들고 하위만 뺀다" 를 미장에서 검증. docs/protocols/us-index-minus-losers-2026-09.md.

    .venv/bin/python tools/trial_us_index_minus_losers.py [--save] [--smoke 2]

G0 = 현행 대응(GBM 점수 EMA5 상위 24 동일가중) · G1 = 시총 상위 500 시총가중·하위 10% 제외·상한 10% ·
G2 = 같은 구성 상한 25% · G3 = SPY. 판정은 G0 대비(두 국면 +2%p · NW t ≥ 2 · MDD · 회전 절반 이하).

**메모리가 설계의 절반이다.** 미장은 6,600종목이라 전 종목 × 1,000세션으로는 한 대에서 못 돈다. 세션마다
시가총액 상위 1,000 으로 먼저 좁히고, 점수 파일은 읽자마자 그 1,000 으로 걸러 버린다.
"""
from __future__ import annotations

import argparse
import glob
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
from quant_rl_trading.store.prices import read_prices  # noqa: E402
from tools.trial_market_beta import capture  # noqa: E402
from tools.trial_overlay import ANN, MAX_MOVE, metrics  # noqa: E402
from tools.trial_pooled import fit_gbm  # noqa: E402
from tools.trial_pooled_rank import rank_gauss  # noqa: E402
from tools.trial_selection_ranker import capped_cap_weights  # noqa: E402
from tools.trial_selection_smoothing import pick_mult  # noqa: E402

PROTOCOL = Path("docs/protocols/us-index-minus-losers-2026-09.md")
WORK_DIRS = (Path("data/ic-history-us-early"), Path("data/ic-history-us"))
SOURCES = [("chart", "chart"), ("event", "event"), ("flow_us", "flow"),
           ("fundamental", "fundamental"), ("regime", "regime"), ("risk", "risk")]
FEATS = [col for _, col in SOURCES]
JUDGE_START, JUDGE_END, BOX_END = date(2022, 7, 1), date(2026, 6, 30), date(2024, 12, 31)
UNIVERSE, WIDE, CUT, REENTRY = 1000, 500, 0.10, 0.10
H, MIN_TRAIN, BLOCK, PURGE = 5, 150, 20, 5
N, EXIT_MULT, SPAN = 24, 3, 5
BENCH = "US:SPY"
GATE_ANN, GATE_T, GATE_TURN = 0.02, 2.0, 0.5


def cost_one_way(store: Store, as_of: datetime) -> float:
    return float(store.config("accounting.fee_us", as_of=as_of))


def top_caps(store: Store, now: datetime, span: int) -> pd.DataFrame:
    """세션×종목 시가총액(넓은 표). 상위 1,000 을 가르는 자리다."""
    f = store.get("market_stats", as_of=now, lookback=span, market="US",
                  columns=["entity_id", "valid_from", "metric", "value"])
    f = f[f["metric"] == "market_cap"]
    f["day"] = pd.to_datetime(f["valid_from"]).dt.date
    return f.pivot_table(index="day", columns="entity_id", values="value", aggfunc="last").sort_index()


def load_scores(keep: pd.DataFrame) -> pd.DataFrame:
    """두 작업 디렉터리의 점수 조각을 읽자마자 (종목, 세션) 상위 1,000 으로 걸러 잇는다."""
    parts = []
    key = keep.set_index(["entity_id", "session"]).index
    for name, col in SOURCES:
        chunks = []
        for work in WORK_DIRS:
            for path in sorted(glob.glob(str(work / f"scores-{name}-0*.parquet"))):  # invariant-allow: data-access — 작업 파일
                f = pd.read_parquet(path, columns=["entity_id", "session", "score"])  # invariant-allow: data-access — 작업 파일
                f["session"] = pd.to_datetime(f["session"]).dt.date
                f = f[f.set_index(["entity_id", "session"]).index.isin(key)]
                chunks.append(f)
        s = pd.concat(chunks, ignore_index=True).drop_duplicates(["entity_id", "session"], keep="last")
        parts.append(s.set_index(["entity_id", "session"])["score"].astype(np.float32).rename(col))
        print(f"  점수 {name}: {len(s):,}행", flush=True)
    return pd.concat(parts, axis=1, join="outer").reset_index()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="data")
    parser.add_argument("--save", action="store_true")
    parser.add_argument("--smoke", type=int, default=0, help="블록 이만큼만 — 배선 확인용, 수치는 안 찍는다")
    args = parser.parse_args(argv)
    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16]
    print(f"=== 시행 AG — {PROTOCOL} (해시 {digest}) ===", flush=True)
    store = Store(root=Path(args.root))
    now = datetime.combine(JUDGE_END, time(23), tzinfo=UTC)
    span = (JUDGE_END - JUDGE_START).days + 60
    cost = cost_one_way(store, now)

    caps = top_caps(store, now, span)
    caps = caps[(caps.index >= JUDGE_START) & (caps.index <= JUDGE_END)]
    ranks = caps.rank(axis=1, ascending=False)
    in_universe = ranks <= UNIVERSE
    keep = in_universe.stack()
    keep = keep[keep].reset_index()[["day", "entity_id"]].rename(columns={"day": "session"})
    names = sorted(keep["entity_id"].unique())
    print(f"시총 {caps.shape[0]}세션 · 상위 {UNIVERSE} 에 한 번이라도 든 종목 {len(names):,} · 비용 편도 {cost:.2%}", flush=True)

    panel = load_scores(keep)
    # 타깃·수익 — 상위 1,000 에 든 종목만 읽는다.
    prices = read_prices(store, as_of=now, lookback=span + 20, entity=names + [BENCH],
                         columns=["close"], adjusted=True, market="US")
    prices["day"] = pd.to_datetime(prices["valid_from"]).dt.date
    close = prices.pivot_table(index="day", columns="entity_id", values="close", aggfunc="last").sort_index()
    del prices
    bench_close = close.pop(BENCH) if BENCH in close.columns else None
    fwd = close.shift(-H) / close - 1.0
    fwd = fwd.where(fwd.abs() <= 1.0)
    y = fwd.stack().rename("y5").reset_index()
    y.columns = ["session", "entity_id", "y5"]
    panel = panel.merge(y, on=["entity_id", "session"], how="left")
    panel["market"] = "US"
    panel = panel[(panel["session"] >= JUDGE_START) & (panel["session"] <= JUDGE_END)]
    panel = rank_gauss(panel, [*FEATS, "y5"])
    sessions = sorted(panel["session"].unique())
    bl, start = [], MIN_TRAIN + PURGE
    while start + BLOCK <= len(sessions):
        bl.append((start, start + BLOCK - 1))
        start += BLOCK
    if args.smoke:
        bl = bl[: args.smoke]
    print(f"패널 {len(panel):,}행 · 세션 {len(sessions)} {sessions[0]}~{sessions[-1]} · 블록 {len(bl)}", flush=True)

    parts = []
    for first, last in bl:
        train_end = sessions[first - PURGE - 1]
        train = panel[(panel["session"] <= train_end) & panel["y5"].notna()]
        test = panel[(panel["session"] >= sessions[first]) & (panel["session"] <= sessions[last])].copy()
        model = fit_gbm(train[FEATS].to_numpy(np.float32), train["y5"].to_numpy(np.float32))
        test["pred"] = model.predict(test[FEATS].to_numpy(np.float32))
        parts.append(test[["entity_id", "session", "pred"]])
        print(f"  블록 {sessions[first]}~{sessions[last]} · 학습 {len(train):,}행", flush=True)
    pred = pd.concat(parts, ignore_index=True)
    if args.smoke:
        print(f"예측 {len(pred):,}행 — 배선 확인만(수치 안 찍음)", flush=True)
        return 0

    score = pred.pivot_table(index="session", columns="entity_id", values="pred").sort_index().ewm(span=SPAN).mean()
    ret = (close.shift(-2) / close.shift(-1) - 1.0)
    ret = ret.where(ret.abs() <= MAX_MOVE)
    bench = (bench_close.shift(-2) / bench_close.shift(-1) - 1.0) if bench_close is not None else None

    series: dict[str, pd.Series] = {}
    extra: dict[str, dict[str, float]] = {}

    # G0 — 현행 대응
    held: list[str] = []
    prev = None
    out, turns = {}, []
    for day in score.index:
        if day not in ret.index or day not in in_universe.index:
            continue
        row = score.loc[day].dropna()
        row = row[row.index.isin(in_universe.columns[in_universe.loc[day]])]
        if row.empty:
            continue
        held = pick_mult(held, row.sort_values(ascending=False).index, N, EXIT_MULT)
        w = pd.Series(1.0 / len(held), index=held)
        dr = ret.loc[day].reindex(w.index).fillna(0.0)
        t = float(w.subtract(prev, fill_value=0.0).abs().sum()) if prev is not None else 1.0
        out[day] = float((w * dr).sum() - cost * t)
        turns.append(t)
        d = w * (1 + dr)
        prev = d / d.sum() if d.sum() > 0 else w
    series["G0"] = pd.Series(out).sort_index()
    extra["G0"] = {"turn": float(np.mean(turns) * ANN)}

    # G1·G2 — 넓게 들고 하위만 뺀다
    excl_rows = []
    for name, cap_limit in (("G1", 0.10), ("G2", 0.25)):
        excluded: set[str] = set()
        prev = None
        prev_keep: frozenset[str] = frozenset()
        out, turns, effs, top2 = {}, [], [], []
        for day in score.index:
            if day not in ret.index or day not in caps.index:
                continue
            wide_set = ranks.loc[day][ranks.loc[day] <= WIDE].index
            pool = score.loc[day].reindex(wide_set).dropna()
            if len(pool) < 100:
                continue
            pct = pool.rank(pct=True)
            excluded = {e for e in excluded if e in pct.index and pct[e] < CUT + REENTRY} | set(pct[pct < CUT].index)
            keepers = [e for e in pool.index if e not in excluded]
            same = prev is not None and frozenset(keepers) == prev_keep and float(prev.max()) <= cap_limit + 0.02
            if same:
                w = prev
            else:
                cap = caps.loc[day].reindex(keepers).dropna()
                w = capped_cap_weights(cap, cap_limit)
            prev_keep = frozenset(keepers)
            dr = ret.loc[day].reindex(w.index).fillna(0.0)
            t = 0.0 if same else (float(w.subtract(prev, fill_value=0.0).abs().sum()) if prev is not None else 1.0)
            out[day] = float((w * dr).sum() - cost * t)
            turns.append(t); effs.append(1.0 / float((w * w).sum()))
            top2.append(float(w.sort_values(ascending=False).head(2).sum()))
            if name == "G1":
                rr = ret.loc[day]
                excl_rows.append({"day": day, "excl": float(rr.reindex(list(excluded)).mean()),
                                  "kept": float(rr.reindex(keepers).mean())})
            d = w * (1 + dr)
            prev = d / d.sum() if d.sum() > 0 else w
        series[name] = pd.Series(out).sort_index()
        extra[name] = {"turn": float(np.mean(turns) * ANN), "effn": float(np.mean(effs)),
                       "top2": float(np.mean(top2)), "top2_max": float(np.max(top2))}
    if bench is not None:
        series["G3"] = bench.reindex(series["G0"].index).dropna()
        extra["G3"] = {}

    rows: dict[str, dict[str, float]] = {}
    b_all = bench.reindex(series["G0"].index).fillna(0.0) if bench is not None else None
    for name, s in series.items():
        b = b_all.reindex(s.index).fillna(0.0)
        m = metrics(s, b)
        m["up"], m["down"] = capture(s, b)
        m["asym"] = m["up"] - m["down"]
        for label, part in (("box", s[s.index <= BOX_END]), ("rally", s[s.index > BOX_END])):
            m[f"{label}_ann"] = float(part.mean() * ANN)
            m[f"{label}_excess"] = float((part - b.reindex(part.index)).mean() * ANN)
        rows[name] = {**extra.get(name, {}), **m}

    ex = pd.DataFrame(excl_rows).set_index("day")
    lines = [f"SPY 같은 창(가격수익): 연 {b_all.mean() * ANN:+.1%} · 비용 편도 {cost:.2%}"]
    for label, part in (("박스", ex[ex.index <= BOX_END]), ("급등", ex[ex.index > BOX_END]), ("전체", ex)):
        lines.append(f"기록: {label} 제외 하위10% 연 {part['excl'].mean() * ANN:+.1%} · 잔류 {part['kept'].mean() * ANN:+.1%} · "
                     f"제외−잔류 {(part['excl'] - part['kept']).mean() * ANN:+.1%}p")
    lines += ["", "| 변형 | 연수익 | 박스 | 급등 | 샤프 | MDD | β | 박스 초과 | 급등 초과 | 비대칭 | 회전 | 유효N | 상위2(평균/최대) |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name in ("G0", "G1", "G2", "G3"):
        if name not in rows:
            continue
        m = rows[name]
        lines.append(f"| {name} | {m['ann']:+.1%} | {m['box_ann']:+.1%} | {m['rally_ann']:+.1%} | {m['sharpe']:+.2f} | "
                     f"{m['mdd']:.1%} | {m['beta']:.2f} | {m['box_excess']:+.1%} | {m['rally_excess']:+.1%} | {m['asym']:+.3f} | "
                     f"{m.get('turn', float('nan')):.1f} | {m.get('effn', float('nan')):.0f} | "
                     f"{m.get('top2', float('nan')):.0%}/{m.get('top2_max', float('nan')):.0%} |")
    lines.append("")
    g0 = rows["G0"]
    passed = []
    for name in ("G1", "G2"):
        m = rows[name]
        t = float(ic_module.newey_west_t((series[name] - series["G0"]).dropna(), lag=4))
        c = (m["box_ann"] >= g0["box_ann"] + GATE_ANN and m["rally_ann"] >= g0["rally_ann"] + GATE_ANN,
             t >= GATE_T, m["mdd"] >= g0["mdd"], m["turn"] <= g0["turn"] * GATE_TURN)
        mark = lambda ok: "○" if ok else "×"  # noqa: E731
        lines.append(f"{name}: ①두 국면 +2%p ({m['box_ann'] - g0['box_ann']:+.1%}/{m['rally_ann'] - g0['rally_ann']:+.1%}) {mark(c[0])} · "
                     f"②NW t {t:+.2f} {mark(c[1])} · ③MDD {m['mdd']:.1%} vs {g0['mdd']:.1%} {mark(c[2])} · "
                     f"④회전 {m['turn']:.1f} vs {g0['turn']:.1f} {mark(c[3])} → {'통과' if all(c) else '탈락'}")
        if all(c):
            passed.append((m["sharpe"], name))
    verdict = f"채택 {max(passed)[1]}" if passed else "기각"
    lines.append(f"판정: {verdict}")
    print("\n" + "\n".join(lines), flush=True)
    if args.save:
        stamp = datetime.now(UTC)  # invariant-allow: wallclock — 시행 기록 시각
        store.append("research_trials", [{
            "entity_id": "us-index-minus-losers-2026-09:AG", "valid_from": stamp, "observed_at": stamp,
            "source": "trial_us_index_minus_losers", "market": "US", "family": "selection", "n_trials": 1,
            "protocol_hash": digest, "detail": (f"{verdict} | " + " | ".join(lines))[:900],
        }], ingest_run_id=f"trial-us-index-minus-losers-AG-{stamp:%Y%m%dT%H%M%S}")
        print(f"research_trials 기록: selection/AG · protocol {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
