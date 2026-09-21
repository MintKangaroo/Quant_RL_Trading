"""시행 AA — 랭커의 타깃을 β 잔차로 바꾼다. docs/protocols/ranker-target-beta-2026-09.md.

    .venv/bin/python tools/trial_ranker_target_beta.py [--save]

T0 = 현행 타깃(h5 수익의 세션 안 rank-gauss) · T1 = β 잔차 r_i − β_i·r_K200 의 세션 안 rank-gauss.
(T2 '시장 초과' 는 세션 안 순위가 T0 과 같아 뺐다 — 등록 문서 정정 1.)
모델·피처·워크포워드는 시행 L·AB 와 같다. 판정 블록은 퍼지 5 로 잡는다(h5 뿐이다).
부산물로 T0 의 **연 회전**을 기록한다 — 시행 Z 의 Z0(연 −1.0%·회전 36.9)과 AB 의 B0(+13.7%)이 갈린 이유를 가린다.
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
from quant_rl_trading.store.prices import read_prices  # noqa: E402
from tools.trial_market_beta import capture  # noqa: E402
from tools.trial_overlay import ANN, INDEX, MAX_MOVE, ONE_WAY_COST, _index, _prices, metrics  # noqa: E402
from tools.trial_pooled import fit_gbm  # noqa: E402
from tools.trial_pooled_rank import rank_gauss  # noqa: E402
from tools.trial_ranker_ensemble import BOX_END, FEATS, JUDGE_END, JUDGE_START, load_panel  # noqa: E402
from tools.trial_selection_smoothing import pick_mult  # noqa: E402

PROTOCOL = Path("docs/protocols/ranker-target-beta-2026-09.md")
CACHE = Path("data/_diag-long")
H, MIN_TRAIN, BLOCK, PURGE = 5, 150, 20, 5
BETA_WINDOW, BETA_MIN = 120, 60
N, EXIT_MULT, SPAN = 24, 3, 5
GATE_BETA, GATE_IC, GATE_ASYM, GATE_T = 0.15, -0.005, 0.05, 2.0


def beta_residual_target(store: Store, sessions: list[date]) -> pd.DataFrame:
    """(entity_id, session, y_beta) — r_i,h5 − β_i·r_m,h5, β 는 t−1 까지의 120세션 OLS."""
    now = datetime.combine(sessions[-1], time(16), tzinfo=UTC)
    span = (sessions[-1] - sessions[0]).days + 260  # β 창(120세션) + h5 여유
    frame = read_prices(store, as_of=now, lookback=span, columns=["close"], adjusted=True, market="KR")
    frame["day"] = pd.to_datetime(frame["valid_from"]).dt.date
    close = frame.pivot_table(index="day", columns="entity_id", values="close", aggfunc="last").sort_index()
    del frame
    idx = store.get("indices", as_of=now, lookback=span, market="KR", columns=["entity_id", "valid_from", "close"])
    idx = idx[idx["entity_id"] == INDEX].assign(day=lambda f: pd.to_datetime(f["valid_from"]).dt.date)
    mkt_close = idx.groupby("day")["close"].last().reindex(close.index).ffill()
    daily = close.pct_change(fill_method=None)
    daily = daily.where(daily.abs() <= MAX_MOVE)
    mkt = mkt_close.pct_change()
    beta = (daily.rolling(BETA_WINDOW, min_periods=BETA_MIN).cov(mkt)
            .div(mkt.rolling(BETA_WINDOW, min_periods=BETA_MIN).var(), axis=0)).shift(1)
    fwd = close.shift(-H) / close - 1.0
    fwd = fwd.where(fwd.abs() <= 1.0)
    fwd_m = mkt_close.shift(-H) / mkt_close - 1.0
    resid = fwd - beta.mul(fwd_m, axis=0)
    out = resid.stack().rename("y_beta").reset_index()
    out.columns = ["session", "entity_id", "y_beta"]
    # 기록 항목 — β 추정 안정성(60세션 대 120세션 β 의 세션별 순위상관)
    beta60 = (daily.rolling(60, min_periods=40).cov(mkt).div(mkt.rolling(60, min_periods=40).var(), axis=0)).shift(1)
    stab = beta.rank(axis=1).corrwith(beta60.rank(axis=1), axis=1)
    return out[out["session"].isin(set(sessions))], float(stab.reindex(sessions).mean())


def blocks(sessions: list[date]) -> list[tuple[int, int]]:
    out, start = [], MIN_TRAIN + PURGE
    while start + BLOCK <= len(sessions):
        out.append((start, start + BLOCK - 1))
        start += BLOCK
    return out


def walk(panel: pd.DataFrame, sessions: list[date], target: str, bl: list[tuple[int, int]]) -> pd.DataFrame:
    parts = []
    for first, last in bl:
        train_end = sessions[first - PURGE - 1]
        train = panel[(panel["session"] <= train_end) & panel[target].notna()]
        test = panel[(panel["session"] >= sessions[first]) & (panel["session"] <= sessions[last])].copy()
        if train.empty or test.empty:
            continue
        model = fit_gbm(train[FEATS].to_numpy(np.float32), train[target].to_numpy(np.float32))
        test["pred"] = model.predict(test[FEATS].to_numpy(np.float32))
        parts.append(test[["entity_id", "session", "pred", "y5"]])
        print(f"  {target} 블록 {sessions[first]}~{sessions[last]} · 학습 {len(train):,}행", flush=True)
    return pd.concat(parts, ignore_index=True)


def portfolio(score: pd.DataFrame, ret: pd.DataFrame, trad: dict[date, set[str]]) -> tuple[pd.Series, float]:
    """시행 AB 와 같은 포트(EMA5·완충 3N·동일가중 24) + **연 회전**을 같이 돌려준다."""
    wide = score.pivot_table(index="session", columns="entity_id", values="pred").sort_index().ewm(span=SPAN).mean()
    held: list[str] = []
    prev = None
    out, turns = {}, []
    for day in wide.index:
        if day not in ret.index:
            continue
        row = wide.loc[day].dropna()
        ok = trad.get(day)
        if ok:
            row = row[row.index.isin(ok)]
        if row.empty:
            continue
        held = pick_mult(held, row.sort_values(ascending=False).index, N, EXIT_MULT)
        w = pd.Series(1.0 / len(held), index=held)
        dr = ret.loc[day].reindex(w.index).fillna(0.0)
        t = float(w.subtract(prev, fill_value=0.0).abs().sum()) if prev is not None else 1.0
        out[day] = float((w * dr).sum() - ONE_WAY_COST * t)
        turns.append(t)
        drift = w * (1 + dr)
        prev = drift / drift.sum() if drift.sum() > 0 else w
    return pd.Series(out).sort_index(), float(np.mean(turns) * ANN)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="data")
    parser.add_argument("--save", action="store_true")
    parser.add_argument("--smoke", type=int, default=0, help="블록 이만큼만 — 배선 확인용, 수치는 안 찍는다")
    args = parser.parse_args(argv)
    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16]
    print(f"=== 시행 AA — {PROTOCOL} (해시 {digest}) ===", flush=True)
    store = Store(root=Path(args.root))

    panel = load_panel(CACHE)
    panel = panel[(panel["session"] >= JUDGE_START) & (panel["session"] <= JUDGE_END)]
    sessions = sorted(panel["session"].unique())
    yb, beta_stab = beta_residual_target(store, sessions)
    panel = panel.merge(yb, on=["entity_id", "session"], how="left")
    panel = rank_gauss(panel, [*FEATS, "y5", "y_beta"])
    bl = blocks(sessions)
    if args.smoke:
        bl = bl[: args.smoke]
    print(f"패널 {len(panel):,}행 · 세션 {len(sessions)} · 블록 {len(bl)} · β 타깃 결측 "
          f"{panel['y_beta'].eq(0).mean():.1%} · β 안정성(60 vs 120 순위상관) {beta_stab:.3f}", flush=True)

    preds = {"T0": walk(panel, sessions, "y5", bl), "T1": walk(panel, sessions, "y_beta", bl)}
    if args.smoke:
        print("배선 확인만 했다 — 판정 수치는 안 찍는다(--smoke)", flush=True)
        return 0

    wide = _prices(store, sessions)
    ret = (wide.shift(-2) / wide.shift(-1) - 1.0)
    ret = ret.where(ret.abs() <= MAX_MOVE)
    idx = _index(store, sessions)
    bench = (idx.shift(-2) / idx.shift(-1) - 1.0)
    trad_frame = pd.read_pickle(CACHE / "tradable-KR.pkl")  # invariant-allow: data-access — 작업 캐시
    trad_frame["session"] = pd.to_datetime(trad_frame["session"]).dt.date
    trad = {day: set(part["entity_id"]) for day, part in trad_frame.groupby("session")}
    del trad_frame

    rows, series = {}, {}
    for name, pred in preds.items():
        daily, turn = portfolio(pred, ret, trad)
        b = bench.reindex(daily.index).fillna(0.0)
        m = metrics(daily, b)
        m["up"], m["down"] = capture(daily, b)
        m["asym"] = m["up"] - m["down"]
        m["turn"] = turn
        ic = ic_module.daily_ic(pred[["entity_id", "session", "pred", "y5"]].rename(columns={"pred": "score", "y5": "target"}).dropna())
        ic.index = pd.to_datetime(pd.Series(ic.index)).dt.date.values
        for label, part in (("box", daily[daily.index <= BOX_END]), ("rally", daily[daily.index > BOX_END])):
            pm = metrics(part, b.reindex(part.index))
            m[f"{label}_ann"], m[f"{label}_beta"] = float(part.mean() * ANN), pm["beta"]
            keep = [d for d in ic.index if (d <= BOX_END) == (label == "box")]
            m[f"{label}_ic"] = float(ic.loc[keep].mean()) if keep else float("nan")
        rows[name], series[name] = m, daily

    t0, t1 = rows["T0"], rows["T1"]
    t = float(ic_module.newey_west_t((series["T1"] - series["T0"]).dropna(), lag=4))
    lines = [f"기록: β 추정 안정성(60 vs 120세션 β 순위상관) {beta_stab:.3f} · T0 연 회전 {t0['turn']:.1f}", "",
             "| 변형 | 연수익 | 박스 | 급등 | 샤프 | MDD | β 박스/급등 | IC 박스/급등 | 상승 | 하락 | 비대칭 | 회전 | IR(K200) |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, m in rows.items():
        lines.append(f"| {name} | {m['ann']:+.1%} | {m['box_ann']:+.1%} | {m['rally_ann']:+.1%} | {m['sharpe']:+.2f} | "
                     f"{m['mdd']:.1%} | {m['box_beta']:.2f}/{m['rally_beta']:.2f} | {m['box_ic']:+.4f}/{m['rally_ic']:+.4f} | "
                     f"{m['up']:.2f} | {m['down']:.2f} | {m['asym']:+.3f} | {m['turn']:.1f} | {m['ir']:+.2f} |")
    c = (t1["box_beta"] >= t0["box_beta"] + GATE_BETA and t1["rally_beta"] >= t0["rally_beta"] + GATE_BETA,
         t1["box_ic"] >= t0["box_ic"] + GATE_IC and t1["rally_ic"] >= t0["rally_ic"] + GATE_IC,
         t1["asym"] >= t0["asym"] + GATE_ASYM,
         t >= GATE_T)
    mark = lambda ok: "○" if ok else "×"  # noqa: E731
    lines += ["", f"T1: ①두 국면 β +0.15 ({t1['box_beta'] - t0['box_beta']:+.2f}/{t1['rally_beta'] - t0['rally_beta']:+.2f}) {mark(c[0])} · "
                  f"②두 국면 IC ≥ T0−0.005 {mark(c[1])} · ③비대칭 {t1['asym'] - t0['asym']:+.3f} {mark(c[2])} · "
                  f"④NW t {t:+.2f} {mark(c[3])} → {'통과' if all(c) else '탈락'}"]
    verdict = "채택 T1" if all(c) else "기각 — 현행 타깃 유지"
    lines.append(f"판정: {verdict}")
    print("\n" + "\n".join(lines), flush=True)
    if args.save:
        now = datetime.now(UTC)  # invariant-allow: wallclock — 시행 기록 시각
        store.append("research_trials", [{
            "entity_id": "ranker-target-beta-2026-09:AA", "valid_from": now, "observed_at": now,
            "source": "trial_ranker_target_beta", "market": "KR", "family": "ranker", "n_trials": 1,
            "protocol_hash": digest, "detail": (f"{verdict} | " + " | ".join(lines))[:900],
        }], ingest_run_id=f"trial-ranker-target-beta-AA-{now:%Y%m%dT%H%M%S}")
        print(f"research_trials 기록: ranker/AA · protocol {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
