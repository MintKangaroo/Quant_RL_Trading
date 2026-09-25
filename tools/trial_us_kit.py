"""미장 선정 시행(AU·AV·AW)이 같이 쓰는 부품 — 시행 AT 와 **같은 패널·같은 루프 GBM·같은 실전 척도 합성(M1)**.

AT 는 예측을 남기지 않았다. 여기서 시드별 예측을 한 번 만들어 `data/_diag/us-selection/` 에 캐시하고, 세 시행이 그것을 읽는다
(국장 AM → AO 가 loop 캐시를 나눠 쓴 것과 같다). 규칙이 도구마다 갈리지 않게 합성·포트·지표를 한 곳에 둔다.
"""
from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from quant_rl_trading.analysts.regime import LOOKBACK_DAYS, classify
from quant_rl_trading.store import Store
from quant_rl_trading.store.prices import read_prices
from tools.trial_overlay import ANN, MAX_MOVE, metrics
from tools.trial_pooled_rank import rank_gauss
from tools.trial_ranker_kit import fit
from tools.trial_selection_smoothing import pick_mult
from tools.trial_us_index_minus_losers import FEATS, load_scores
from tools.trial_us_missing_fundamental import (
    BENCH,
    BLOCK,
    BOX_END,
    H,
    JUDGE_END,
    JUDGE_START,
    MIN_TRAIN,
    PURGE,
    SEEDS,
    UNIVERSE,
    combine,
)

CACHE = Path("data/_diag/us-selection")
SPX = "US:IDX:SP500"


def build(store: Store) -> None:
    """시드별 예측·수익·벤치를 캐시한다. 이미 있으면 안 한다."""
    if (CACHE / f"pred-seed{SEEDS[-1]}.pkl").exists():
        return
    CACHE.mkdir(parents=True, exist_ok=True)
    now = datetime.combine(JUDGE_END, time(23), tzinfo=UTC)
    span = (JUDGE_END - JUDGE_START).days + 60
    prices = read_prices(store, as_of=now, lookback=span + 40, columns=["close", "volume"], adjusted=True, market="US")
    prices["day"] = pd.to_datetime(prices["valid_from"]).dt.date
    close = prices.pivot_table(index="day", columns="entity_id", values="close", aggfunc="last").sort_index()
    volume = prices.pivot_table(index="day", columns="entity_id", values="volume", aggfunc="last").sort_index()
    del prices
    bench_close = close.pop(BENCH)
    volume = volume.drop(columns=[BENCH], errors="ignore")
    dv = (close * volume).rolling(20, min_periods=10).mean()
    dv = dv[(dv.index >= JUDGE_START) & (dv.index <= JUDGE_END)]
    in_universe = dv.rank(axis=1, ascending=False) <= UNIVERSE
    keep = in_universe.stack()
    keep = keep[keep].reset_index()
    keep.columns = ["session", "entity_id", "_"]
    # 실전과 같게 **보통주·ADR 만**(universe.instrument_types_us, 2026-09-25). 증권 종류는 사실상 바뀌지 않으므로 수집 첫날 스냅샷을
    # 과거에도 쓴다 — 한계로 등록 문서에 적었다.
    kinds = store.get("instrument_types", as_of=datetime.now(UTC), lookback=10, market="US",  # invariant-allow: wallclock — 최신 분류
                      columns=["entity_id", "instrument", "test_issue"])
    ok = set(kinds[kinds["instrument"].isin(["common", "adr", "other"]) & ~kinds["test_issue"].astype(bool)]["entity_id"])
    keep = keep[keep["entity_id"].isin(ok)]
    panel = load_scores(keep[["entity_id", "session"]])
    panel["has_fund"] = panel["fundamental"].notna()
    panel["fund_raw"] = panel["fundamental"].astype(float)
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
    parts: dict[int, list[pd.DataFrame]] = {s: [] for s in SEEDS}
    for first, last in bl:
        train = panel[(panel["session"] <= sessions[first - PURGE - 1]) & panel["y5"].notna()]
        test = panel[(panel["session"] >= sessions[first]) & (panel["session"] <= sessions[last])]
        X, yy = train[FEATS].to_numpy(np.float32), train["y5"].to_numpy(np.float32)
        for s in SEEDS:
            t = test[["entity_id", "session", "fund_raw", "has_fund"]].copy()
            t["pred"] = fit(X, yy, seed=s).predict(test[FEATS].to_numpy(np.float32))
            parts[s].append(t)
        print(f"  블록 {sessions[first]}~{sessions[last]}", flush=True)
    ret = (close.shift(-2) / close.shift(-1) - 1.0)
    ret = ret.where(ret.abs() <= MAX_MOVE)
    ret.astype(np.float32).to_pickle(CACHE / "ret.pkl")
    (bench_close.shift(-2) / bench_close.shift(-1) - 1.0).to_pickle(CACHE / "bench.pkl")
    for s in SEEDS:
        pd.concat(parts[s], ignore_index=True).to_pickle(CACHE / f"pred-seed{s}.pkl")


def scores(seed: int) -> pd.DataFrame:
    """M1 합성 점수(세션 × 종목) — AT 채택 규칙, 실전 척도."""
    p = pd.read_pickle(CACHE / f"pred-seed{seed}.pkl")  # invariant-allow: data-access — 작업 캐시
    p["rank"] = p.groupby("session")["pred"].transform(
        lambda x: np.tanh(((x.rank() - 0.5) / x.count() - 0.5) * 2.0 * np.sqrt(3.0) / 2.0))
    p["c"] = combine("M1", p["fund_raw"], p["has_fund"], p["rank"])
    return p.pivot_table(index="session", columns="entity_id", values="c").sort_index()


def market() -> tuple[pd.DataFrame, pd.Series]:
    return pd.read_pickle(CACHE / "ret.pkl"), pd.read_pickle(CACHE / "bench.pkl")  # invariant-allow: data-access — 작업 캐시


def book(score: pd.DataFrame, ret: pd.DataFrame, cost: float, *, n: int = 24, exit_mult: int = 2,
         every: int = 1, scale: pd.Series | None = None) -> tuple[pd.Series, dict]:
    """상위 n 동일가중 · 완충 exit_mult·n · every 세션마다 재조정(그 사이 드리프트) · scale = 세션별 노출 배수(나머지 현금, 수익 0)."""
    held: list[str] = []
    prev = None
    last_k = None
    out, turns = {}, []
    for i, day in enumerate(d for d in score.index if d in ret.index):
        k = float(scale.get(day, 1.0)) if scale is not None else 1.0
        if prev is None or i % every == 0:
            row = score.loc[day].dropna()
            if row.empty:
                continue
            held = pick_mult(held, row.sort_values(ascending=False).index, n, exit_mult)
            w = pd.Series(k / len(held), index=held)
        elif k != last_k:
            # 보유일에 노출 배수가 바뀌면 명단·상대 비중은 그대로 크기만 — 실전 보유일 규칙(selector.md §5 7번)과 같다.
            w = prev / prev.sum() * k if prev.sum() > 0 else prev
        else:
            w = prev
        last_k = k
        dr = ret.loc[day].reindex(w.index).fillna(0.0)
        t = float(w.subtract(prev, fill_value=0.0).abs().sum()) if prev is not None else float(w.sum())
        out[day] = float((w * dr).sum() - cost * t)
        turns.append(t)
        d = w * (1 + dr)
        total = 1.0 - float(w.sum()) + float(d.sum())   # 현금(수익 0) + 주식
        prev = d / total if total > 0 else w
    return pd.Series(out).sort_index(), {"turn": float(np.mean(turns) * ANN)}


def summarize(daily: pd.Series, bench: pd.Series, extra: dict) -> dict:
    m = metrics(daily, bench.reindex(daily.index).fillna(0.0))
    m["box_ann"] = float(daily[daily.index <= BOX_END].mean() * ANN)
    m["rally_ann"] = float(daily[daily.index > BOX_END].mean() * ANN)
    return {**m, **extra}


def spx_regime(store: Store, sessions: list, crisis_floor: float) -> pd.Series:
    """세션별 S&P500 국면(regime.classify, 실전 RegimeAnalyst 와 같은 400일 창)."""
    end = datetime.combine(sessions[-1], time(23), tzinfo=UTC)
    idx = store.get("indices", as_of=end, lookback=(sessions[-1] - JUDGE_START).days + LOOKBACK_DAYS + 10, market="US",
                    columns=["entity_id", "valid_from", "close"])
    idx = idx[idx["entity_id"] == SPX].assign(day=lambda f: pd.to_datetime(f["valid_from"]).dt.date)
    closes = idx.groupby("day")["close"].last().sort_index()
    out = {}
    for day in sessions:
        window = closes[(closes.index > day - timedelta(days=LOOKBACK_DAYS)) & (closes.index <= day)]
        out[day] = classify(window, crisis_floor=crisis_floor)
    return pd.Series(out)
