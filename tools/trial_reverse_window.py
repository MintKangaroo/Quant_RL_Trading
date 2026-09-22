"""시행 AH — "넓게 들고 하위만 뺀다" 를 안 본 하락장(2021-11~2022-06)에서. docs/protocols/reverse-window-index-minus-losers-2026-09.md.

    .venv/bin/python tools/trial_reverse_window.py [--save]

워크포워드 랭커는 이 창에서 점수를 못 낸다(최소 학습창 150세션을 못 채운다). 그래서 **뒤 구간(2022-07-08~2026-06-23)으로
학습한 GBM 하나를 고정해** 앞 구간을 채점한다. 라벨은 새지 않지만 모델은 미래 국면을 본 상태다 — 등록 문서의 한계 절.
H0(현행 대응: 상위 24 동일가중, 위험 하한) · H1(AD-D1 그대로) · H2(K200). 판정은 H0 대비 수익 +2%p · MDD · 회전 절반.
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
from tools.trial_beta_megacap import CACHE, ETF_FEE, k200_members  # noqa: E402
from tools.trial_market_beta import capture  # noqa: E402
from tools.trial_overlay import ANN, MAX_MOVE, ONE_WAY_COST, _index, _prices, metrics  # noqa: E402
from tools.trial_pooled import fit_gbm  # noqa: E402
from tools.trial_pooled_rank import rank_gauss  # noqa: E402
from tools.trial_ranker_ensemble import FEATS, load_panel  # noqa: E402
from tools.trial_selection_ranker import _scores, capped_cap_weights  # noqa: E402
from tools.trial_selection_smoothing import pick_mult  # noqa: E402

PROTOCOL = Path("docs/protocols/reverse-window-index-minus-losers-2026-09.md")
JUDGE_START, JUDGE_END = date(2021, 11, 1), date(2022, 6, 30)
TRAIN_START, TRAIN_END = date(2022, 7, 8), date(2026, 6, 23)
SPAN, N, EXIT_MULT = 5, 24, 3
CUT, CAP_LIMIT, REENTRY = 0.10, 0.10, 0.10
GATE_ANN = 0.02


def reverse_scores() -> tuple[pd.DataFrame, pd.Series]:
    """뒤 구간으로 학습한 GBM 하나로 판정 창을 채점한다. (entity_id, session, pred) 와 판정 창 일별 IC."""
    panel = load_panel(CACHE)
    panel = panel[(panel["session"] >= JUDGE_START) & (panel["session"] <= TRAIN_END)]
    panel = panel.drop(columns=[c for c in ("y20", "y60") if c in panel.columns])
    panel = rank_gauss(panel, [*FEATS, "y5"])
    train = panel[(panel["session"] >= TRAIN_START) & panel["y5"].notna()]
    test = panel[panel["session"] <= JUDGE_END].copy()
    print(f"학습 {len(train):,}행 {train['session'].min()}~{train['session'].max()} · "
          f"판정 {len(test):,}행 {test['session'].min()}~{test['session'].max()}", flush=True)
    model = fit_gbm(train[FEATS].to_numpy(np.float32), train["y5"].to_numpy(np.float32))
    del train
    test["pred"] = model.predict(test[FEATS].to_numpy(np.float32))
    ic = ic_module.daily_ic(test[["entity_id", "session", "pred", "y5"]]
                            .rename(columns={"pred": "score", "y5": "target"}).dropna())
    return test[["entity_id", "session", "pred"]], ic


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="data")
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args(argv)
    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16]
    print(f"=== 시행 AH — {PROTOCOL} (해시 {digest}) ===", flush=True)
    store = Store(root=Path(args.root))

    pred, ic = reverse_scores()
    score = pred.pivot_table(index="session", columns="entity_id", values="pred").sort_index().ewm(span=SPAN).mean()

    trad_frame = pd.read_pickle(CACHE / "tradable-KR.pkl")  # invariant-allow: data-access — 작업 캐시
    trad_frame["session"] = pd.to_datetime(trad_frame["session"]).dt.date
    trad_frame = trad_frame[(trad_frame["session"] >= JUDGE_START) & (trad_frame["session"] <= JUDGE_END)]
    trad = {day: set(part["entity_id"]) for day, part in trad_frame.groupby("session")}
    sessions = sorted(trad)
    del trad_frame
    end_moment = datetime.combine(sessions[-1], time(16), tzinfo=UTC)
    floor = float(store.config("selector.risk_floor_percentile", as_of=end_moment))
    risk = _scores(store, "risk", sessions)
    wide = _prices(store, sessions)
    ret = (wide.shift(-2) / wide.shift(-1) - 1.0)
    ret = ret.where(ret.abs() <= MAX_MOVE)
    idx = _index(store, sessions)
    bench = (idx.shift(-2) / idx.shift(-1) - 1.0)
    caps = _caps(store, sessions)
    fl = store.get("float_ratio", as_of=datetime.now(UTC), lookback=30)  # invariant-allow: wallclock — 참조 데이터, 최초 관측 소급(시행 P·AD 와 같다)
    fl = fl.sort_values("observed_at").groupby("entity_id").tail(1).set_index("entity_id")["float_ratio"]
    members = k200_members(sessions, first_quarter="2021Q3")
    print(f"세션 {len(sessions)} {sessions[0]}~{sessions[-1]} · 위험 하한 {floor:.0%}", flush=True)

    series: dict[str, pd.Series] = {"H2": (bench - ETF_FEE / ANN).reindex(sessions).dropna()}
    extra: dict[str, dict[str, float]] = {"H2": {}}

    # H0 — 현행 대응(AD 의 D0 과 같은 코드: 전체 유니버스 상위 24 동일가중, 위험 하한)
    held: list[str] = []
    prev = None
    out, turns = {}, []
    for day in sessions:
        if day not in score.index or day not in ret.index:
            continue
        f = score.loc[day].dropna()
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
        turns.append(t)
        drift = w * (1 + dr)
        prev = drift / drift.sum() if drift.sum() > 0 else w
    series["H0"] = pd.Series(out).sort_index()
    extra["H0"] = {"turn": float(np.mean(turns) * ANN)}

    # H1 — AD-D1 그대로(K200 유동시총 가중, 하위 10% 제외, 상한 10%, 재편입 +10%p, 위험 하한 없음)
    excluded: set[str] = set()
    prev = None
    prev_keep: frozenset[str] = frozenset()
    out, turns, effs, top2, loser_ret, keep_ret = {}, [], [], [], [], []
    for day in sessions:
        if day not in score.index or day not in ret.index:
            continue
        pool = score.loc[day].dropna()
        pool = pool[pool.index.isin(trad[day] & members[day])]
        if len(pool) < 50:
            continue
        pct = pool.rank(pct=True)
        excluded = {e for e in excluded if e in pct.index and pct[e] < CUT + REENTRY} | set(pct[pct < CUT].index)
        keep = [e for e in pool.index if e not in excluded]
        if not keep:
            continue
        same = prev is not None and frozenset(keep) == prev_keep and float(prev.max()) <= CAP_LIMIT + 0.02
        if same:
            w = prev
        elif day in caps.index:
            fcap = (caps.loc[day].reindex(keep) * fl.reindex(keep).fillna(fl.median())).dropna()
            w = capped_cap_weights(fcap, CAP_LIMIT) if len(fcap) >= 30 else pd.Series(1.0 / len(keep), index=keep)
        else:
            w = pd.Series(1.0 / len(keep), index=keep)
        prev_keep = frozenset(keep)
        dr = ret.loc[day].reindex(w.index).fillna(0.0)
        t = 0.0 if same else (float(w.subtract(prev, fill_value=0.0).abs().sum()) if prev is not None else 1.0)
        out[day] = float((w * dr).sum() - ONE_WAY_COST * t)
        turns.append(t)
        effs.append(1.0 / float((w * w).sum()))
        top2.append(float(w.nlargest(2).sum()))
        losers = [e for e in excluded if e in ret.columns]
        if losers:
            loser_ret.append(float(ret.loc[day].reindex(losers).mean()))
            keep_ret.append(float(ret.loc[day].reindex(keep).mean()))
        drift = w * (1 + dr)
        prev = drift / drift.sum() if drift.sum() > 0 else w
    series["H1"] = pd.Series(out).sort_index()
    extra["H1"] = {"turn": float(np.mean(turns) * ANN), "effn": float(np.mean(effs)),
                   "top2": float(np.mean(top2)), "top2max": float(np.max(top2)),
                   "loser_ann": float(np.nanmean(loser_ret) * ANN), "keep_ann": float(np.nanmean(keep_ret) * ANN)}

    rows: dict[str, dict[str, float]] = {}
    for name, s in series.items():
        b = bench.reindex(s.index).fillna(0.0)
        m = metrics(s, b)
        m["up"], m["down"] = capture(s, b)
        m["asym"] = m["up"] - m["down"]
        m["excess"] = float((s - b).mean() * ANN)
        rows[name] = {**extra.get(name, {}), **m}

    bnav = (1 + bench.reindex(series["H2"].index).fillna(0.0)).cumprod()
    h1 = extra["H1"]
    t_diff = float(ic_module.newey_west_t((series["H1"] - series["H0"]).dropna(), lag=4))
    lines = [
        f"K200 같은 창: 연 {bench.reindex(series['H2'].index).mean() * ANN:+.1%} · MDD {float((bnav / bnav.cummax() - 1).min()):.1%}",
        f"기록: 판정 창 IC(h5) 평균 {ic.mean():+.4f} (t {float(ic_module.newey_west_t(ic, lag=4)):+.2f}, {len(ic)}세션)",
        f"기록: 제외 하위10% 연 {h1['loser_ann']:+.1%} · 잔류 {h1['keep_ann']:+.1%} · 제외−잔류 {h1['loser_ann'] - h1['keep_ann']:+.1%}p",
        f"기록: (H1 − H0) 일별 NW t {t_diff:+.2f}",
        "",
        "| 변형 | 연수익 | 대 K200 | 샤프 | MDD | β | 상승 | 하락 | 비대칭 | 회전 | 유효N | 상위2(평균/최대) |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name in ("H0", "H1", "H2"):
        m = rows[name]
        top = f"{m['top2']:.0%}/{m['top2max']:.0%}" if "top2" in m else "—"
        lines.append(f"| {name} | {m['ann']:+.1%} | {m['excess']:+.1%} | {m['sharpe']:+.2f} | {m['mdd']:.1%} | {m['beta']:.2f} | "
                     f"{m['up']:.2f} | {m['down']:.2f} | {m['asym']:+.3f} | {m.get('turn', float('nan')):.1f} | "
                     f"{m.get('effn', float('nan')):.0f} | {top} |")
    lines.append("")
    a, b0 = rows["H1"], rows["H0"]
    c = (a["ann"] >= b0["ann"] + GATE_ANN, a["mdd"] >= b0["mdd"], a["turn"] <= 0.5 * b0["turn"])
    mark = lambda ok: "○" if ok else "×"  # noqa: E731
    lines.append(f"H1: ①연수익 {a['ann']:+.1%} vs H0 {b0['ann']:+.1%}+2%p {mark(c[0])} · ②MDD {a['mdd']:.1%} vs {b0['mdd']:.1%} "
                 f"{mark(c[1])} · ③회전 {a['turn']:.1f} vs {b0['turn']:.1f}의 절반 {mark(c[2])} → {'통과' if all(c) else '탈락'}")
    verdict = "채택 H1" if all(c) else "기각"
    lines.append(f"판정: {verdict}")
    print("\n" + "\n".join(lines), flush=True)
    if args.save:
        now = datetime.now(UTC)  # invariant-allow: wallclock — 시행 기록 시각
        store.append("research_trials", [{
            "entity_id": "reverse-window-index-minus-losers-2026-09:AH", "valid_from": now, "observed_at": now,
            "source": "trial_reverse_window", "market": "KR", "family": "selection", "n_trials": 1,
            "protocol_hash": digest, "detail": (f"{verdict} | " + " | ".join(lines[:4] + lines[7:]))[:900],
        }], ingest_run_id=f"trial-reverse-window-AH-{now:%Y%m%dT%H%M%S}")
        print(f"research_trials 기록: selection/AH · protocol {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
