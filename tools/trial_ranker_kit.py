"""랭커 계열 시행(AJ·AK·AL)이 같이 쓰는 부품. 시행 AA·AB 의 워크포워드·포트폴리오와 **같은 규칙**이되
피처 목록·목적함수·시드·종목 수·배제 집합을 인자로 받는다 — 도구마다 베껴 쓰다 규칙이 갈리는 것을 막는다.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta

import numpy as np
import pandas as pd
from scipy.stats import norm

from quant_rl_trading.analysts import ic as ic_module
from quant_rl_trading.analysts.regime import LOOKBACK_DAYS, classify
from quant_rl_trading.store import Store
from tools.trial_beta_megacap import CACHE
from tools.trial_market_beta import capture
from tools.trial_overlay import ANN, MAX_MOVE, ONE_WAY_COST, _index, _prices, metrics
from tools.trial_pooled_rank import rank_gauss
from tools.trial_ranker_ensemble import BOX_END, FEATS, JUDGE_END, JUDGE_START, load_panel
from tools.trial_selection_smoothing import pick_mult

INDEX = "KR:IDX:KOSPI200"
STATES = ("bull", "bear", "volatile", "crisis")
MIN_TRAIN, BLOCK, PURGE = 150, 20, 5
N, EXIT_MULT, SPAN = 24, 3, 5


def judge_panel() -> tuple[pd.DataFrame, list[date]]:
    """확장 패널의 판정 구간(2022-07~2026-06), 피처·y5 rank-gauss 까지. y20·y60 은 뺀다."""
    panel = load_panel(CACHE)
    panel = panel[(panel["session"] >= JUDGE_START) & (panel["session"] <= JUDGE_END)]
    panel = panel.drop(columns=[c for c in ("y20", "y60") if c in panel.columns])
    panel = rank_gauss(panel, [*FEATS, "y5"])
    return panel, sorted(panel["session"].unique())


def blocks(sessions: list[date]) -> list[tuple[int, int]]:
    out, start = [], MIN_TRAIN + PURGE
    while start + BLOCK <= len(sessions):
        out.append((start, start + BLOCK - 1))
        start += BLOCK
    return out


def market_state(store: Store, sessions: list[date], crisis_floor: float) -> pd.Series:
    """세션별 K200 국면(regime.classify 그대로, 종가 t 까지). 실전 RegimeAnalyst.state 와 같은 창(400일)."""
    end = datetime.combine(sessions[-1], time(16), tzinfo=UTC)
    idx = store.get("indices", as_of=end, lookback=(sessions[-1] - date(2020, 1, 1)).days, market="KR",
                    columns=["entity_id", "valid_from", "close"])
    idx = idx[idx["entity_id"] == INDEX].assign(day=lambda f: pd.to_datetime(f["valid_from"]).dt.date)
    closes = idx.groupby("day")["close"].last().sort_index()
    out = {}
    for day in sessions:
        window = closes[(closes.index > day - timedelta(days=LOOKBACK_DAYS)) & (closes.index <= day)]
        out[day] = classify(window, crisis_floor=crisis_floor)
    return pd.Series(out)


def fit(X: np.ndarray, y: np.ndarray, *, objective: str = "regression", seed: int = 0):
    """시행 L 의 하이퍼파라미터. objective·seed 만 인자다."""
    import lightgbm as lgb
    params = dict(objective=objective, num_leaves=7, min_data_in_leaf=2000, learning_rate=0.03, bagging_fraction=0.8,
                  bagging_freq=1, feature_fraction=1.0, lambda_l2=1.0, verbose=-1, seed=seed, num_threads=6)
    return lgb.train(params, lgb.Dataset(X, y), num_boost_round=300)


def walk(panel: pd.DataFrame, sessions: list[date], feats: list[str], target: str, bl: list[tuple[int, int]],
         *, objective: str = "regression", seeds: tuple[int, ...] = (0,), label: str = "") -> pd.DataFrame:
    """워크포워드 예측(entity_id, session, pred[, pred_se]). 학습은 블록 시작 − 퍼지까지만. 시드가 여럿이면 평균과 표준편차."""
    parts = []
    for first, last in bl:
        train_end = sessions[first - PURGE - 1]
        train = panel[(panel["session"] <= train_end) & panel[target].notna()]
        test = panel[(panel["session"] >= sessions[first]) & (panel["session"] <= sessions[last])].copy()
        if train.empty or test.empty:
            continue
        X, y = train[feats].to_numpy(np.float32), train[target].to_numpy(np.float32)
        preds = np.stack([fit(X, y, objective=objective, seed=s).predict(test[feats].to_numpy(np.float32)) for s in seeds])
        test["pred"] = preds.mean(axis=0)
        if len(seeds) > 1:
            test["pred_se"] = preds.std(axis=0, ddof=1)
        parts.append(test[["entity_id", "session", "pred", *(["pred_se"] if len(seeds) > 1 else [])]])
        print(f"  {label or target} 블록 {sessions[first]}~{sessions[last]} · 학습 {len(train):,}행", flush=True)
    return pd.concat(parts, ignore_index=True)


def market_data(store: Store, sessions: list[date]) -> tuple[pd.DataFrame, pd.Series, dict[date, set[str]]]:
    """t+1→t+2 수익, 벤치마크, 거래가능 명단 — 시행 AA 와 같다."""
    wide = _prices(store, sessions)
    ret = (wide.shift(-2) / wide.shift(-1) - 1.0)
    ret = ret.where(ret.abs() <= MAX_MOVE)
    idx = _index(store, sessions)
    bench = (idx.shift(-2) / idx.shift(-1) - 1.0)
    trad_frame = pd.read_pickle(CACHE / "tradable-KR.pkl")  # invariant-allow: data-access — 작업 캐시
    trad_frame["session"] = pd.to_datetime(trad_frame["session"]).dt.date
    trad = {day: set(part["entity_id"]) for day, part in trad_frame.groupby("session")}
    return ret, bench, trad


def portfolio(score: pd.DataFrame, ret: pd.DataFrame, trad: dict[date, set[str]], *,
              n_of: Callable[[date, pd.Series], int] | None = None,
              exclude: dict[date, set[str]] | None = None,
              every: int = 1) -> tuple[pd.Series, dict[str, float]]:
    """EMA5 평활·완충 3N·동일가중(시행 AB·AA 와 같다). N 은 날마다 n_of 가 정하고(기본 24), exclude 는 그날 후보에서 뺀다.

    ``every`` — 재조정 주기(세션). 1 이면 매일(기본, 지금까지의 모든 시행과 같다). 시행 AO(2026-09-24)가 10 을 채택했다 —
    재조정일이 아닌 날은 어제 비중이 드리프트한 채로 든다(`trial_rebalance_cadence.book` 과 같은 규칙).
    """
    wide = score.pivot_table(index="session", columns="entity_id", values="pred").sort_index().ewm(span=SPAN).mean()
    held: list[str] = []
    prev = None
    out, turns, ns = {}, [], []
    step = -1
    for day in wide.index:
        if day not in ret.index:
            continue
        step += 1
        if every > 1 and prev is not None and step % every != 0:
            w = prev
            dr = ret.loc[day].reindex(w.index).fillna(0.0)
            out[day] = float((w * dr).sum())
            turns.append(0.0)
            ns.append(len(w))
            drift = w * (1 + dr)
            prev = drift / drift.sum() if drift.sum() > 0 else w
            continue
        row = wide.loc[day].dropna()
        ok = trad.get(day)
        if ok:
            row = row[row.index.isin(ok)]
        if exclude and day in exclude:
            row = row[~row.index.isin(exclude[day])]
        if row.empty:
            continue
        n = n_of(day, row) if n_of else N
        held = pick_mult(held, row.sort_values(ascending=False).index, n, EXIT_MULT)
        w = pd.Series(1.0 / len(held), index=held)
        dr = ret.loc[day].reindex(w.index).fillna(0.0)
        t = float(w.subtract(prev, fill_value=0.0).abs().sum()) if prev is not None else 1.0
        out[day] = float((w * dr).sum() - ONE_WAY_COST * t)
        turns.append(t)
        ns.append(n)
        drift = w * (1 + dr)
        prev = drift / drift.sum() if drift.sum() > 0 else w
    return pd.Series(out).sort_index(), {"turn": float(np.mean(turns) * ANN), "n_mean": float(np.mean(ns)),
                                         "wide_share": float(np.mean([x > N for x in ns]))}


def summarize(daily: pd.Series, bench: pd.Series, pred: pd.DataFrame | None = None) -> dict[str, float]:
    """전체·국면별 지표 + (pred 가 있으면) 국면별 IC(h5)."""
    b = bench.reindex(daily.index).fillna(0.0)
    m = metrics(daily, b)
    m["up"], m["down"] = capture(daily, b)
    m["asym"] = m["up"] - m["down"]
    for label, part in (("box", daily[daily.index <= BOX_END]), ("rally", daily[daily.index > BOX_END])):
        pm = metrics(part, b.reindex(part.index))
        m[f"{label}_ann"], m[f"{label}_beta"] = float(part.mean() * ANN), pm["beta"]
    if pred is not None:
        ic = ic_module.daily_ic(pred[["entity_id", "session", "pred", "y5"]]
                                .rename(columns={"pred": "score", "y5": "target"}).dropna())
        ic.index = pd.to_datetime(pd.Series(ic.index)).dt.date.values
        m["ic"] = float(ic.mean())
        m["box_ic"] = float(ic[[d <= BOX_END for d in ic.index]].mean())
        m["rally_ic"] = float(ic[[d > BOX_END for d in ic.index]].mean())
    return m


def nw_t(a: pd.Series, b: pd.Series) -> float:
    return float(ic_module.newey_west_t((a - b).dropna(), lag=4))


def collapse_threshold(q: float = 0.10) -> float:
    """y5 rank-gauss 에서 하위 q 에 해당하는 값."""
    return float(norm.ppf(q))


def session_sets(frame: pd.DataFrame, col: str, q: float, *, top: bool) -> dict[date, set[str]]:
    """세션마다 col 의 상위(top=True)/하위 q 집합."""
    out = {}
    for day, part in frame.groupby("session"):
        v = part.set_index("entity_id")[col].dropna()
        if v.empty:
            continue
        cut = v.quantile(1 - q) if top else v.quantile(q)
        out[day] = set(v[v >= cut].index) if top else set(v[v <= cut].index)
    return out


def set_return(sets: dict[date, set[str]], frame: pd.DataFrame) -> dict[str, float]:
    """집합에 든 종목들의 h5 z-수익(y5 rank-gauss) 평균 — 전체·박스·급등."""
    y = frame.set_index(["session", "entity_id"])["y5"]
    vals = {}
    for day, s in sets.items():
        try:
            vals[day] = float(y.loc[day].reindex(list(s)).mean())
        except KeyError:
            continue
    v = pd.Series(vals)
    return {"all": float(v.mean()), "box": float(v[v.index <= BOX_END].mean()), "rally": float(v[v.index > BOX_END].mean())}


def mark(ok: bool) -> str:
    return "○" if ok else "×"


def record(store: Store, *, entity: str, source: str, family: str, digest: str, verdict: str, lines: list[str]) -> None:
    now = datetime.now(UTC)  # invariant-allow: wallclock — 시행 기록 시각
    store.append("research_trials", [{
        "entity_id": entity, "valid_from": now, "observed_at": now, "source": source, "market": "KR",
        "family": family, "n_trials": 1, "protocol_hash": digest,
        "detail": (f"{verdict} | " + " | ".join(lines))[:900],
    }], ingest_run_id=f"{source}-{now:%Y%m%dT%H%M%S}")


def scores_chunked(store, analyst: str, sessions: list[date], *, chunk_days: int = 120) -> pd.DataFrame:
    """signals 에서 Analyst 하나의 점수 표(세션 × 종목)를 **기간을 나눠** 읽는다.

    `trial_selection_ranker._scores` 는 4년치 전 Analyst 행을 한 번에 읽은 뒤 거른다 — 2026-09-24 시행 AM 판정이 RSS 5.3GB 로
    메모리 가드에 내려졌다. 같은 as_of·같은 정정본 선택 규칙으로 창만 나눈다(`lookback`·`until` 은 valid_from 창이다 — store.get).
    """
    now = datetime.combine(sessions[-1], time(16, 0), tzinfo=UTC)
    parts = []
    start = sessions[0]
    while start <= sessions[-1]:
        end = min(start + timedelta(days=chunk_days), sessions[-1] + timedelta(days=1))
        until = datetime.combine(end, time(0, 0), tzinfo=UTC)
        f = store.get("signals", as_of=now, lookback=(now - datetime.combine(start, time(0, 0), tzinfo=UTC)).days + 1,
                      until=until, market="KR", columns=["entity_id", "valid_from", "observed_at", "analyst", "score"])
        f = f[(f["analyst"] == analyst) & (f["valid_from"] < until)]
        f = f[f["valid_from"] >= datetime.combine(start, time(0, 0), tzinfo=UTC)]
        if not f.empty:
            f = f.sort_values("observed_at").groupby(["entity_id", "valid_from"], as_index=False).tail(1)
            f["session"] = f["valid_from"].dt.date
            parts.append(f.pivot_table(index="session", columns="entity_id", values="score", aggfunc="last").astype("float32"))
        del f
        start = end
    return pd.concat(parts).sort_index() if parts else pd.DataFrame()
