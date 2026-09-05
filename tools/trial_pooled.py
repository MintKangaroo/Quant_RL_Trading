"""표본을 늘린 규제 랭커 — docs/protocols/pooled-ranker-2026-09.md (베타, 회차 미차감)."""
from __future__ import annotations

import argparse
import glob
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.analysts import ic as ic_module  # noqa: E402

CACHE = Path("data/_diag"); US_WORK = Path("data/ic-history-us")
HOLDOUT = date(2026, 7, 1)
FEATS = ["chart", "event", "flow", "fundamental", "regime", "risk"]
TOP_N = 24; MIN_TRAIN = 150; BLOCK = 20; PURGE = 5; RIDGE_LAMBDA = 10.0


def _z(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    g = df.groupby(["market", "session"])[cols]
    return ((df[cols] - g.transform("mean")) / g.transform("std").replace(0, np.nan)).clip(-5, 5).fillna(0.0)


def load_kr() -> tuple[pd.DataFrame, pd.DataFrame]:
    frames = []
    for name, col in (("chart", "chart"), ("event", "event"), ("flow_kr", "flow"), ("fundamental", "fundamental"), ("regime", "regime"), ("risk", "risk")):
        f = pd.read_pickle(CACHE / f"scores-{name}-KR.pkl"); f["session"] = pd.to_datetime(f["session"]).dt.date
        frames.append(f.rename(columns={"score": col})[["entity_id", "session", col]])
    df = frames[0]
    for f in frames[1:]:
        df = df.merge(f, on=["entity_id", "session"], how="outer")
    t = pd.read_pickle(CACHE / "targets-KR-h5.pkl"); t["session"] = pd.to_datetime(t["session"]).dt.date
    df = df.merge(t, on=["entity_id", "session"], how="inner"); df["market"] = "KR"
    return df[df["session"] < HOLDOUT], df


def load_us() -> pd.DataFrame:
    parts = []
    # ic-history 작업 파일(창고가 아니다) — backfill_ic_history 의 work 디렉터리와 같은 규약.
    for name, col in (("chart", "chart"), ("event", "event"), ("flow_us", "flow"), ("fundamental", "fundamental"), ("regime", "regime"), ("risk", "risk")):
        fs = sorted(glob.glob(str(US_WORK / f"scores-{name}-0*.parquet")))  # invariant-allow: data-access — 창고가 아닌 작업 파일
        f = pd.concat([pd.read_parquet(x) for x in fs], ignore_index=True)  # invariant-allow: data-access — 창고가 아닌 작업 파일
        parts.append(f.rename(columns={"score": col})[["entity_id", "session", col]])
    df = parts[0]
    for f in parts[1:]:
        df = df.merge(f, on=["entity_id", "session"], how="outer")
    tf = sorted(glob.glob(str(US_WORK / "targets-*.parquet")))  # invariant-allow: data-access — 창고가 아닌 작업 파일
    t = pd.concat([pd.read_parquet(x) for x in tf], ignore_index=True)  # invariant-allow: data-access — 창고가 아닌 작업 파일
    t = t.groupby(["entity_id", "session"], as_index=False)["target"].mean()
    df = df.merge(t, on=["entity_id", "session"], how="inner"); df["market"] = "US"
    df["session"] = pd.to_datetime(df["session"]).dt.date
    return df[df["session"] < HOLDOUT]


def fit_ridge(X: np.ndarray, y: np.ndarray) -> np.ndarray:
    Xb = np.column_stack([np.ones(len(X)), X])
    A = Xb.T @ Xb + RIDGE_LAMBDA * np.eye(Xb.shape[1]); A[0, 0] -= RIDGE_LAMBDA
    return np.linalg.solve(A, Xb.T @ y)


def predict_ridge(w: np.ndarray, X: np.ndarray) -> np.ndarray:
    return np.column_stack([np.ones(len(X)), X]) @ w


def fit_gbm(X, y):
    import lightgbm as lgb
    params = dict(objective="regression", num_leaves=7, min_data_in_leaf=2000, learning_rate=0.03, bagging_fraction=0.8,
                  bagging_freq=1, feature_fraction=1.0, lambda_l2=1.0, verbose=-1, seed=0, num_threads=6)
    return lgb.train(params, lgb.Dataset(X, y), num_boost_round=300)


def _nw(s: pd.Series, lag: int) -> float:
    return float(ic_module.newey_west_t(s.dropna(), lag=lag)) if s.notna().sum() > 3 else float("nan")


def daily_ic(df: pd.DataFrame, col: str) -> pd.Series:
    return ic_module.daily_ic(df[["entity_id", "session", col, "target"]].rename(columns={col: "score"}).dropna())


def top_excess(df: pd.DataFrame, col: str) -> pd.Series:
    top = df.sort_values(col, ascending=False).groupby("session").head(TOP_N)
    return top.groupby("session")["target"].mean()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-gbm", action="store_true")
    args = parser.parse_args(argv)
    kr, _ = load_kr(); us = load_us()
    for df in (kr, us):
        df[FEATS] = _z(df, FEATS)
    kr["is_us"] = 0.0; us["is_us"] = 1.0
    X_COLS = FEATS + ["is_us"]
    kr_sessions = sorted(kr["session"].unique())
    print(f"국장 {len(kr):,}행 {len(kr_sessions)}세션 · 미장 {len(us):,}행 {us['session'].nunique()}세션")
    blocks = []; end = MIN_TRAIN
    while end + PURGE + BLOCK <= len(kr_sessions):
        blocks.append((kr_sessions[end - 1], kr_sessions[end + PURGE], kr_sessions[end + PURGE + BLOCK - 1])); end += BLOCK
    print(f"판정 블록 {len(blocks)}개")
    variants = ["RIDGE-KR", "RIDGE-POOL"] + ([] if args.no_gbm else ["GBM-KR", "GBM-POOL"])
    preds = {v: [] for v in variants}; preds_us = {v: [] for v in variants}
    for i, (train_end, pred_start, pred_end) in enumerate(blocks, 1):
        tr_kr = kr[kr["session"] <= train_end]; tr_us = us[us["session"] <= train_end]
        te_kr = kr[(kr["session"] >= pred_start) & (kr["session"] <= pred_end)].copy()
        te_us = us[(us["session"] >= pred_start) & (us["session"] <= pred_end)].copy()
        for v in variants:
            train = tr_kr if v.endswith("-KR") else pd.concat([tr_kr, tr_us], ignore_index=True)
            X, y = train[X_COLS].to_numpy(np.float32), train["target"].to_numpy(np.float32)
            if v.startswith("RIDGE"):
                w = fit_ridge(X, y); f = lambda Z: predict_ridge(w, Z)
            else:
                m = fit_gbm(X, y); f = lambda Z: m.predict(Z)
            te_kr[v] = f(te_kr[X_COLS].to_numpy(np.float32)); te_us[v] = f(te_us[X_COLS].to_numpy(np.float32))
        preds_all = te_kr; preds_all_us = te_us
        for v in variants:
            preds[v].append(preds_all[["entity_id", "session", "target", "fundamental", v]].rename(columns={v: "pred"}))
            preds_us[v].append(preds_all_us[["entity_id", "session", "target", "fundamental", v]].rename(columns={v: "pred"}))
        print(f"블록 {i}/{len(blocks)} 학습 ~{train_end} (국장 {len(tr_kr):,} · 미장 {len(tr_us):,}) → 판정 {pred_start}~{pred_end}", flush=True)
    print()
    for label, store in (("국장", preds), ("미장", preds_us)):
        print(f"== {label} 판정 블록 ==")
        for v in variants:
            d = pd.concat(store[v], ignore_index=True)
            ic_p = daily_ic(d, "pred"); ic_f = daily_ic(d, "fundamental"); common = ic_p.index.intersection(ic_f.index)
            delta = ic_p.loc[common] - ic_f.loc[common]
            bd = []
            for (_e, ps, pe) in blocks:
                days = [x for x in delta.index if ps <= x <= pe]; bd.append(float(delta.loc[days].mean()) if days else np.nan)
            tp, tf = top_excess(d, "pred").mean(), top_excess(d, "fundamental").mean()
            worst = np.nanmin(bd) if len(bd) else np.nan
            ok = (_nw(delta, 4) >= 2.0) and (tp >= tf) and (worst >= -0.03)
            print(f"{v:11} IC {ic_p.mean():+.4f} (t {_nw(ic_p,4):+.2f}) 대 fund {ic_f.mean():+.4f} · ΔIC {delta.mean():+.4f} (NW t {_nw(delta,4):+.2f}) · 상위24 {tp:+.4f} vs {tf:+.4f} · 최악 블록 {worst:+.3f} → {'통과' if ok else '기각'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
