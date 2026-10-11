"""진단 — 실전 운영 신뢰도(롤링 IC) 하락의 원인: 장세인가 잡음인가 노후인가, 그리고 0 동결 규칙은 과거에 득이었나.

**진단이지 시행이 아니다** — 합격선이 없고 `research_trials` 에 적지 않는다. 창고에 쓰지 않는다.
배경(사용자 10/11): ranker confidence 9/29 0.060 → 10/8 0.028, risk 는 10/2 부터 0. 문서: docs/diag/confidence-regime-2026-10-11.md

    QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB QUANT_RL_DUCKDB_THREADS=2 nice -n 10 .venv/bin/python tools/diag_confidence_regime.py

**금고 규칙** — 수익·라벨은 END(2026-06-30) 까지만. 모든 읽기는 ``as_of=END``·``until=END``. 결정일 t 의 5일 라벨은
c(t+6)/c(t+1)(analysts/ic.build_targets 그대로) 이라 t+6 이 END 안일 때만 생긴다. 포트 수익은 c(t+2)/c(t+1) 이라 t+2 ≤ END.
그 밖으로 나가면 ``guard`` 가 멈춘다. data/_past_vault 는 열지 않는다.

정의(실전과 같다, analysts/ic.rolling_confidence):
- 점수 = 창고 ``signals`` 의 ranker·risk 점수(실전이 읽는 바로 그 행 — ranker 는 월말 워크포워드 모델 백필, observed_at = 세션).
- 시점 T 의 롤링 IC = T 까지 최근 60 신호 세션 중 **T 에 라벨이 이미 확정된 세션**(t+6 ≤ T) 의 일별 Spearman IC 평균.
  confidence = max(0, 롤링 IC). 창고에 저장된 ``confidence`` 열(당시 계산값)과 대조한다.

1. 경로 — 롤링 IC 의 0 이하 구간, 그때 시장 상태(K200·KOSPI 60세션 수익, 중대형−소형 TMI 스프레드, K200 60세션 변동성).
2. 국면 회귀 — 롤링 IC ~ 시장 60세션 수익 + 대형 상대 + 변동성 (NW lag 59). 일별 IC ~ 같은 5일 창의 시장 상태 (NW lag 4).
3. 잡음 — 롤링 IC 분포·표준편차, 일별 IC 의 NW 장기분산으로 본 54세션 평균의 표준오차.
4. 노후 — 5세션 간격 결정일에서 나이 k 개월 모델의 원 예측 IC. 같은 날을 여러 나이 모델로 채점(쌍대 비교).
5. 동결 — 상위 24 동일가중(완충 72, 전날 신호→다음날 종가 진입) 매매 vs confidence 0 이면 전날 보유 유지. 비용 편도 0.205%.
   + 지속성: T 의 롤링 IC 가 T 뒤 60세션 IC 를 예측하나.
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from quant_rl_trading.analysts import ic as ic_module
from quant_rl_trading.analysts.ranker import BASE_ANALYSTS, SCORE_FEATURES, RankerModel, model_dir, rank_gauss
from quant_rl_trading.store import Store
from quant_rl_trading.store.prices import read_prices

END = datetime(2026, 6, 30, 16, 0, tzinfo=UTC)
END_DAY = date(2026, 6, 30)
START = date(2021, 10, 1)
WINDOW = 60
H = ic_module.HORIZON_DAYS  # 5
LAG = ic_module.ENTRY_LAG_DAYS  # 1
TOP_N, EXIT_RANK = 24, 72
COST_ONE_WAY = 0.00205
AGES = (0, 1, 2, 3, 6, 9, 12)
AGE_START = date(2023, 7, 1)
LIVE = {"ranker": {"2026-09-29": 0.060, "2026-10-08": 0.028}}
INDEX = {"k200": "KR:IDX:KOSPI200", "kospi": "KR:IDX:KOSPI", "large": "KR:IDX:KRX 중대형 TMI", "small": "KR:IDX:KRX 소형 TMI"}
OUT_DIR = Path("logs")
OUT_CSV = OUT_DIR / "diag-confidence-regime-20261011.csv"
OUT_JSON = OUT_DIR / "diag-confidence-regime-20261011.json"


def guard(days, what: str) -> None:
    days = [d for d in days if d is not None]
    if days and max(days) > END_DAY:
        raise SystemExit(f"금고 침범({what}): {max(days)} > {END_DAY} — 멈춘다")


# --------------------------------------------------------------------------- 통계


def nw_var(x: np.ndarray, lag: int) -> float:
    x = x - x.mean()
    n = x.size
    v = float(x @ x / n)
    for k in range(1, min(lag, n - 1) + 1):
        v += 2.0 * (1 - k / (lag + 1)) * float(x[k:] @ x[:-k] / n)
    return v


def ols_nw(y: pd.Series, X: pd.DataFrame, lag: int) -> pd.DataFrame:
    """OLS + Newey-West HAC 표준오차(statsmodels 없이)."""
    d = pd.concat([y.rename("y"), X], axis=1).dropna()
    Y = d["y"].to_numpy(float)
    Z = np.column_stack([np.ones(len(d)), d[X.columns].to_numpy(float)])
    n, k = Z.shape
    ZZi = np.linalg.inv(Z.T @ Z)
    b = ZZi @ Z.T @ Y
    u = Y - Z @ b
    g = Z * u[:, None]
    S = g.T @ g
    for j in range(1, min(lag, n - 1) + 1):
        w = 1 - j / (lag + 1)
        G = g[j:].T @ g[:-j]
        S += w * (G + G.T)
    V = ZZi @ S @ ZZi
    se = np.sqrt(np.diag(V))
    r2 = 1 - float(u @ u) / float(((Y - Y.mean()) ** 2).sum())
    out = pd.DataFrame({"coef": b, "se": se, "t": b / se}, index=["const", *X.columns])
    out.attrs.update(n=n, r2=r2)
    return out


# --------------------------------------------------------------------------- 읽기


def load_signals(store: Store, analysts: set[str], start: date, *, columns=("entity_id", "valid_from", "analyst", "score", "confidence"),
                 chunk_days: int = 90, on_chunk=None) -> pd.DataFrame | None:
    """signals 를 90일 조각으로. ``on_chunk`` 가 있으면 조각마다 넘기고 모으지 않는다(메모리)."""
    parts = []
    cur = datetime(start.year, start.month, start.day, tzinfo=UTC)
    while cur < END:
        nxt = min(cur + timedelta(days=chunk_days), END)
        f = store.get("signals", as_of=END, lookback=(END - cur).days + 1, until=nxt, market="KR", columns=list(columns))
        if not f.empty:
            f = f[f["analyst"].astype(str).isin(analysts) & f["entity_id"].astype(str).str.match(r"^KR:\d{6}$")]
            f = f[(f["valid_from"] >= pd.Timestamp(cur)) & (f["valid_from"] < pd.Timestamp(nxt))] if nxt < END else f[f["valid_from"] >= pd.Timestamp(cur)]
            f = f.copy()
            f["session"] = f["valid_from"].dt.date
            f["entity_id"] = f["entity_id"].astype("category")
            f["analyst"] = f["analyst"].astype("category")
            if on_chunk is not None:
                on_chunk(f)
            else:
                parts.append(f.drop(columns=["valid_from"]))
        cur = nxt
    if on_chunk is not None:
        return None
    out = pd.concat(parts, ignore_index=True)
    out = out.drop_duplicates(["entity_id", "analyst", "session"], keep="last")
    guard(list(out["session"].unique()), "signals")
    return out


def load_indices(store: Store) -> pd.DataFrame:
    f = store.get("indices", as_of=END, until=END, lookback=(END.date() - date(2021, 1, 1)).days, market="KR",
                  entity=list(INDEX.values()), columns=["entity_id", "valid_from", "close", "revision"])
    f["day"] = f["valid_from"].dt.date
    f = f.sort_values(["valid_from", "revision"])
    w = f.pivot_table(index="day", columns="entity_id", values="close", aggfunc="last").sort_index()
    guard(list(w.index), "indices")
    return w.rename(columns={v: k for k, v in INDEX.items()})


def load_close(store: Store) -> pd.DataFrame:
    p = read_prices(store, as_of=END, until=END, lookback=(END.date() - date(2022, 6, 1)).days, columns=["close"], adjusted=True, market="KR")
    p = p[p["entity_id"].str.match(r"^KR:\d{6}$")]
    p["day"] = p["valid_from"].dt.date
    w = p.pivot_table(index="day", columns="entity_id", values="close", aggfunc="last").sort_index()
    guard(list(w.index), "prices")
    return w


# --------------------------------------------------------------------------- 1. 롤링 IC


def rolling_path(daily: pd.Series, sessions: list[date], cal: list[date]) -> pd.Series:
    """실전 rolling_confidence 를 날짜별로 되감는다: T 까지 최근 60 신호 세션 중 t+(LAG+H) ≤ T 인 세션의 IC 평균."""
    pos = {d: i for i, d in enumerate(cal)}
    out = {}
    for i, T in enumerate(sessions):
        win = sessions[max(0, i - WINDOW + 1): i + 1]
        if T not in pos:
            continue
        ok = [t for t in win if t in pos and pos[t] + LAG + H <= pos[T] and t in daily.index]
        if len(ok) >= 2:
            out[T] = float(daily.loc[ok].mean())
    return pd.Series(out, dtype=float)


def market_state(idx: pd.DataFrame, cal: list[date]) -> pd.DataFrame:
    idx = idx.reindex([d for d in cal if d in idx.index])
    r = idx.pct_change()
    st = pd.DataFrame(index=idx.index)
    st["k200_60"] = idx["k200"] / idx["k200"].shift(WINDOW) - 1
    st["kospi_60"] = idx["kospi"] / idx["kospi"].shift(WINDOW) - 1
    st["ls_60"] = (idx["large"] / idx["large"].shift(WINDOW)) - (idx["small"] / idx["small"].shift(WINDOW))
    st["vol_60"] = r["k200"].rolling(WINDOW).std() * np.sqrt(252)
    # 일별 IC 용 — 라벨 창(t+1→t+6) 의 같은 지표
    fwd = lambda s: s.shift(-(LAG + H)) / s.shift(-LAG) - 1  # noqa: E731
    st["k200_f5"] = fwd(idx["k200"])
    st["ls_f5"] = fwd(idx["large"]) - fwd(idx["small"])
    st["absk_f5"] = st["k200_f5"].abs()
    return st


def episodes(path: pd.Series, cond: pd.Series) -> list[tuple[date, date, int]]:
    out, start, n, prev = [], None, 0, None
    for d, c in cond.items():
        if c and start is None:
            start, n = d, 0
        if c:
            n += 1
            prev = d
        if not c and start is not None:
            out.append((start, prev, n))
            start = None
    if start is not None:
        out.append((start, prev, n))
    return out


# --------------------------------------------------------------------------- 4. 노후


def month_end_models(root: Path) -> dict[date, RankerModel]:
    out = {}
    for sc in sorted(model_dir(root).glob("ranker-v0.1.0-*.json")):
        m = RankerModel.load(sc)
        out[m.trained_through] = m
    return out


def aging(store: Store, targets: pd.DataFrame, ranker_sets: dict[date, set], stored: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    import lightgbm as lgb

    models = month_end_models(Path(store.root))
    ends = sorted(models)
    boosters = {d: lgb.Booster(model_file=str(models[d].path)) for d in ends}
    # 5세션 간격 결정일(라벨 겹침 없음)
    cand = sorted(d for d in ranker_sets if d >= AGE_START)
    pick = set(cand[::H])
    tgt = targets[targets["session"].isin(pick)].set_index(["session", "entity_id"])["target"]
    stored_score = stored.set_index(["session", "entity_id"])["score"]
    stored_days = set(stored["session"].unique())
    rows, recon = [], []

    def on_chunk(f: pd.DataFrame) -> None:
        f = f[f["session"].isin(pick)]
        if f.empty:
            return
        for S, g in f.groupby("session", observed=True):
            g = g[g["entity_id"].astype(str).isin(ranker_sets.get(S, set()))].copy()
            if g.empty:
                continue
            g["feature"] = g["analyst"].astype(str).map(BASE_ANALYSTS)
            wide = g.pivot_table(index=g["entity_id"].astype(str), columns="feature", values="score", aggfunc="last", observed=True)
            for c in SCORE_FEATURES:
                if c not in wide.columns:
                    wide[c] = np.nan
            wide = rank_gauss(wide, SCORE_FEATURES)
            wide["is_us"] = 0.0
            X = wide.loc[:, [*SCORE_FEATURES, "is_us"]].to_numpy(np.float32)
            live_end = max(d for d in ends if d < S)  # usable_from = trained_through + 1
            try:
                y = tgt.loc[S].reindex(wide.index)
            except KeyError:
                continue
            for k in AGES:
                want = live_end
                for _ in range(k):  # k 개월 전 월말
                    prev = [d for d in ends if d < want.replace(day=1)]
                    want = prev[-1] if prev else None
                    if want is None:
                        break
                if want is None:
                    continue
                pred = pd.Series(boosters[want].predict(X), index=wide.index)
                ok = y.notna()
                if ok.sum() < 30:
                    continue
                rows.append({"session": S, "age": k, "model": want, "ic": float(pred[ok].corr(y[ok], method="spearman"))})
                if k == 0:
                    ss = stored_score.loc[S].reindex(wide.index) if S in stored_days else None
                    if ss is not None:
                        recon.append({"session": S, "rho_stored_vs_raw": float(pred.corr(ss, method="spearman"))})

    base = set(BASE_ANALYSTS) - {"flow_us"}
    load_signals(store, base, AGE_START, columns=("entity_id", "valid_from", "analyst", "score"), on_chunk=on_chunk)
    return pd.DataFrame(rows), pd.DataFrame(recon)


# --------------------------------------------------------------------------- 5. 동결


def simulate(score: pd.DataFrame, close: pd.DataFrame, freeze: pd.Series, sessions: list[date]) -> pd.DataFrame:
    """T 신호 → T+1 종가 진입 → T+2 종가까지 하루 보유(c(T+2)/c(T+1)). freeze[T]=True 면 전날 보유 유지(주문 없음)."""
    cal = list(close.index)
    pos = {d: i for i, d in enumerate(cal)}
    ret = close.pct_change(fill_method=None)
    held: list[str] = []
    out = []
    for T in sessions:
        if T not in pos or pos[T] + 2 >= len(cal):
            continue
        d1, d2 = cal[pos[T] + 1], cal[pos[T] + 2]
        guard([d2], "portfolio")
        s = score.get(T)
        trade = not bool(freeze.get(T, False))
        new = held
        if trade and s is not None and len(s):
            rk = s.rank(ascending=False)
            keep = [e for e in held if e in rk.index and rk[e] <= EXIT_RANK]
            fill = [e for e in s.sort_values(ascending=False).index if e not in keep][: max(0, TOP_N - len(keep))]
            new = keep + fill
        turn = len(set(new) ^ set(held)) / (2 * TOP_N) if held else 1.0
        r = ret.loc[d2]
        port = float(r.reindex(new).fillna(0.0).mean()) if new else 0.0
        uni = float(r.reindex(s.index).mean()) if s is not None and len(s) else float(r.mean())
        out.append({"session": T, "ret": port - 2 * turn * COST_ONE_WAY * (1 if held else 0.5), "gross": port, "uni": uni,
                    "turn": turn, "frozen": not trade})
        held = new
    return pd.DataFrame(out).set_index("session")


def perf(df: pd.DataFrame, mask: pd.Series | None = None) -> dict:
    d = df if mask is None else df[mask.reindex(df.index).fillna(False)]
    ex = d["ret"] - d["uni"]
    n = len(d)
    if n == 0:
        return {"n": 0}
    return {"n": n, "ann_ex": float(ex.mean() * 252), "ir": float(ex.mean() / ex.std() * np.sqrt(252)) if ex.std() > 0 else np.nan,
            "ann_ex_gross": float((d["gross"] - d["uni"]).mean() * 252),
            "ann_ret": float(d["ret"].mean() * 252), "turn_ann": float(d["turn"].sum() / n * 252)}


def smoothed(rk: pd.DataFrame, span: int = 5) -> pd.DataFrame:
    """실전 9/4 이후 규칙(ranker.smoothing_span 5)을 과거 원점수에 입힌 것: z_t = α·raw_t + (1−α)·z_{t−1}, 직전 없으면 raw.
    창고 과거 ranker 점수는 평활 전(원 예측 재현 ρ 0.9996)이라, 실전 창의 IC 가 평활 점수로 재진다는 차이를 잰다."""
    z = rk.pivot_table(index="session", columns="entity_id", values="score", aggfunc="last").sort_index()
    z = 2.0 * np.arctanh(z.clip(-0.999999, 0.999999))
    a = 2.0 / (span + 1.0)
    out = z.copy()
    prev = None
    for i, d in enumerate(z.index):
        row = z.iloc[i]
        if prev is not None:
            row = row.where(prev.isna(), a * row + (1 - a) * prev)
        out.iloc[i] = row
        prev = row
    return out.stack().rename("score").reset_index()


# --------------------------------------------------------------------------- main


def main() -> int:
    store = Store(root=Path("data"))
    print("signals(ranker·risk) 읽기", flush=True)
    sig = load_signals(store, {"ranker", "risk"}, START)
    print(f"  {len(sig):,}행", flush=True)
    print("라벨(build_targets, as_of=END)", flush=True)
    targets = ic_module.build_targets(store, as_of=END, lookback=(END.date() - START).days + 30, market="KR")
    targets["entity_id"] = targets["entity_id"].astype(str)
    idx = load_indices(store)
    cal = sorted(idx.index)
    pos = {d: i for i, d in enumerate(cal)}
    lab_max = max(targets["session"])
    guard([cal[pos[lab_max] + LAG + H]] if pos[lab_max] + LAG + H < len(cal) else [END_DAY + timedelta(days=1)], "labels")
    state = market_state(idx, cal)

    result: dict = {"live": LIVE}
    paths, dailies = {}, {}
    for a in ("ranker", "risk"):
        s = sig[sig["analyst"] == a].copy()
        s["entity_id"] = s["entity_id"].astype(str)
        m = s[["entity_id", "session", "score"]].merge(targets, on=["entity_id", "session"], how="inner")
        daily = ic_module.daily_ic(m)
        sessions = sorted(s["session"].unique())
        path = rolling_path(daily, sessions, cal)
        stored_conf = s.groupby("session")["confidence"].median()
        dailies[a], paths[a] = daily, path
        both = pd.concat([path.clip(lower=0).rename("re"), stored_conf.rename("st")], axis=1).dropna()
        both = both[both.index >= sessions[min(len(sessions) - 1, WINDOW)]]
        result[a] = {
            "first_session": str(sessions[0]), "sessions": len(sessions), "daily_ic_mean": float(daily.mean()),
            "daily_ic_sd": float(daily.std()), "daily_ic_nw_t": ic_module.newey_west_t(daily, lag=H - 1),
            "check_recomputed_vs_stored_conf": {"n": len(both), "corr": float(both.corr().iloc[0, 1]),
                                                "mad": float((both["re"] - both["st"]).abs().median())},
            "stored_conf_zero_sessions": int((stored_conf[stored_conf.index >= sessions[min(len(sessions) - 1, WINDOW)]] <= 1e-12).sum()),
        }
        # --- 3. 잡음
        p = path[path.index >= sessions[min(len(sessions) - 1, WINDOW)]]
        n_eff = WINDOW - LAG - H
        se54 = float(np.sqrt(nw_var(daily.to_numpy(float), H - 1) / n_eff))
        result[a]["noise"] = {
            "rolling_mean": float(p.mean()), "rolling_sd": float(p.std()), "rolling_min": float(p.min()), "rolling_max": float(p.max()),
            "q05": float(p.quantile(0.05)), "q10": float(p.quantile(0.10)), "q25": float(p.quantile(0.25)), "q50": float(p.median()),
            "share_le_0": float((p <= 0).mean()), "share_le_0028": float((p <= 0.028).mean()),
            "se_mean54_nw": se54, "se_mean54_iid": float(daily.std() / np.sqrt(n_eff)),
            "z_0028": float((0.028 - daily.mean()) / se54), "z_0": float((0 - daily.mean()) / se54),
            "drop_32bp_in_7_sessions_share": float(((p - p.shift(7)) <= -0.032).mean()),
        }
        # --- 1. 0 이하 구간
        rows = []
        for thr, a0, b0, n in [(0.0, *e) for e in episodes(p, p <= 0)] + [(0.03, *e) for e in episodes(p, p <= 0.03)]:
            w = state.loc[a0:b0]
            rows.append({"thr": thr, "start": str(a0), "end": str(b0), "sessions": n, "min_ic": float(p.loc[a0:b0].min()),
                         "k200_60": float(w["k200_60"].mean()), "kospi_60": float(w["kospi_60"].mean()),
                         "ls_60": float(w["ls_60"].mean()), "vol_60": float(w["vol_60"].mean())})
        result[a]["episodes_low"] = rows
        allst = state.loc[p.index]
        result[a]["state_all_mean"] = {c: float(allst[c].mean()) for c in ("k200_60", "kospi_60", "ls_60", "vol_60")}
        # --- 2. 국면 회귀
        X = state[["k200_60", "ls_60", "vol_60"]].reindex(p.index)
        reg = ols_nw(p, X, lag=WINDOW - 1)
        Xz = (X - X.mean()) / X.std()
        regz = ols_nw(p, Xz, lag=WINDOW - 1)
        Xd = state[["k200_f5", "ls_f5", "absk_f5"]].reindex(daily.index)
        regd = ols_nw(daily, Xd, lag=H - 1)
        Xdz = (Xd - Xd.mean()) / Xd.std()
        regdz = ols_nw(daily, Xdz, lag=H - 1)
        result[a]["reg_rolling"] = {"coef": reg["coef"].to_dict(), "t": reg["t"].to_dict(), "coef_per_sd": regz["coef"].to_dict(),
                                    "n": reg.attrs["n"], "r2": reg.attrs["r2"]}
        result[a]["reg_daily"] = {"coef": regd["coef"].to_dict(), "t": regd["t"].to_dict(), "coef_per_sd": regdz["coef"].to_dict(),
                                  "n": regd.attrs["n"], "r2": regd.attrs["r2"]}
        # 국면별 평균 일별 IC
        ph = {"하락 2022H2": (date(2022, 7, 1), date(2022, 12, 31)), "박스 2023~2024": (date(2023, 1, 1), date(2024, 12, 31)),
              "급등 2025~2026H1": (date(2025, 1, 1), END_DAY)}
        result[a]["phase_daily_ic"] = {}
        for k, (lo, hi) in ph.items():
            d = daily[(daily.index >= lo) & (daily.index <= hi)]
            result[a]["phase_daily_ic"][k] = {"n": len(d), "mean": float(d.mean()) if len(d) else None,
                                              "nw_t": ic_module.newey_west_t(d, lag=H - 1) if len(d) > 5 else None}
        # 대형주 주도(ls_60 상위 사분위)·급등(k200_60 상위 사분위) 조건부 롤링 IC
        cond = {}
        for c in ("k200_60", "ls_60", "vol_60"):
            q = X[c].quantile([0.25, 0.75])
            cond[c] = {"low_q": float(p[X[c] <= q.iloc[0]].mean()), "high_q": float(p[X[c] >= q.iloc[1]].mean())}
        result[a]["rolling_by_quartile"] = cond
        # --- 지속성: T 의 롤링 IC → T 뒤 60 신호 세션(라벨 기준 겹침 없음) IC 평균
        ss = sorted(daily.index)
        fpos = {d: i for i, d in enumerate(ss)}
        fwd = {}
        for T in p.index:
            if T not in fpos:
                continue
            i = fpos[T]
            nxt = ss[i + 1: i + 1 + WINDOW]
            if len(nxt) == WINDOW:
                fwd[T] = float(daily.loc[nxt].mean())
        fwd = pd.Series(fwd)
        pr = pd.concat([p.rename("now"), fwd.rename("next")], axis=1).dropna()
        regp = ols_nw(pr["next"], pr[["now"]], lag=2 * WINDOW)
        result[a]["persistence"] = {"n": len(pr), "corr": float(pr.corr().iloc[0, 1]), "slope": float(regp.loc["now", "coef"]),
                                    "slope_t": float(regp.loc["now", "t"]),
                                    "next_when_now_le_0": float(pr.loc[pr["now"] <= 0, "next"].mean()) if (pr["now"] <= 0).any() else None,
                                    "next_when_now_le_0028": float(pr.loc[pr["now"] <= 0.028, "next"].mean()) if (pr["now"] <= 0.028).any() else None,
                                    "next_when_now_gt_0028": float(pr.loc[pr["now"] > 0.028, "next"].mean()),
                                    "share_next_le_0_given_now_le_0028": float((pr.loc[pr["now"] <= 0.028, "next"] <= 0).mean()) if (pr["now"] <= 0.028).any() else None}

    both = pd.concat([paths["ranker"], paths["risk"]], axis=1).dropna()
    result["ranker_vs_risk_rolling_corr"] = float(both.corr().iloc[0, 1])
    # 평활(EMA5) 점수의 IC — 실전 창은 9/4 부터 평활 점수
    rk0 = sig[sig["analyst"] == "ranker"].copy()
    rk0["entity_id"] = rk0["entity_id"].astype(str)
    sm = smoothed(rk0[["session", "entity_id", "score"]])
    dsm = ic_module.daily_ic(sm.merge(targets, on=["entity_id", "session"], how="inner"))
    psm = rolling_path(dsm, sorted(sm["session"].unique()), cal)
    psm = psm[psm.index >= paths["ranker"].dropna().index[WINDOW]]
    diff = (dsm - dailies["ranker"]).dropna()
    result["smoothing"] = {"daily_ic_raw": float(dailies["ranker"].mean()), "daily_ic_ema5": float(dsm.mean()),
                           "diff_mean": float(diff.mean()), "diff_nw_t": ic_module.newey_west_t(diff, lag=H - 1),
                           "rolling_ema5_mean": float(psm.mean()), "rolling_ema5_sd": float(psm.std()), "rolling_ema5_min": float(psm.min()),
                           "rolling_ema5_q05": float(psm.quantile(0.05)), "share_le_0028": float((psm <= 0.028).mean())}
    paths["ranker_ema5"] = psm

    # CSV — 날짜별 경로
    csv = pd.concat([paths["ranker"].rename("ranker_roll"), paths["ranker_ema5"].rename("ranker_ema5_roll"), paths["risk"].rename("risk_roll"),
                     dailies["ranker"].rename("ranker_daily"), dailies["risk"].rename("risk_daily"),
                     state[["k200_60", "kospi_60", "ls_60", "vol_60"]]], axis=1)
    csv = csv[(csv.index >= START) & (csv.index <= END_DAY)]
    OUT_DIR.mkdir(exist_ok=True)

    # --- 4. 노후
    print("노후(나이별 모델 재채점)", flush=True)
    rk = sig[sig["analyst"] == "ranker"].copy()
    rk["entity_id"] = rk["entity_id"].astype(str)
    sets = {d: set(g) for d, g in rk.groupby("session")["entity_id"]}
    ag, recon = aging(store, targets, sets, rk[["session", "entity_id", "score"]])
    piv = ag.pivot_table(index="session", columns="age", values="ic")
    full = piv.dropna()
    result["aging"] = {"sessions_all_ages": len(full), "mean_ic_by_age": {int(k): float(v) for k, v in full.mean().items()},
                       "diff_vs_age0": {int(k): {"mean": float((full[k] - full[0]).mean()),
                                                 "t": float((full[k] - full[0]).mean() / ((full[k] - full[0]).std() / np.sqrt(len(full))))}
                                        for k in full.columns if k != 0},
                       "mean_ic_by_age_unbalanced": {int(k): {"n": int(piv[k].notna().sum()), "mean": float(piv[k].mean())} for k in piv.columns},
                       "recon_rho_stored_vs_raw_median": float(recon["rho_stored_vs_raw"].median()) if len(recon) else None,
                       "recon_rho_by_year": {str(y): float(g.median()) for y, g in recon.set_index("session")["rho_stored_vs_raw"].groupby(lambda d: d.year)} if len(recon) else {}}

    # --- 5. 동결
    print("동결 시뮬레이션", flush=True)
    close = load_close(store)
    score = {d: g.set_index("entity_id")["score"] for d, g in rk.groupby("session")}
    sessions = sorted(d for d in score if d >= sorted(score)[0])
    conf_st = rk.groupby("session")["confidence"].median()
    base = simulate(score, close, pd.Series(False, index=sessions), sessions)
    variants = {"A 계속 매매": base}
    roll = paths["ranker"]
    rules = {"B 저장 confidence=0 동결(백필 경계 결함 7세션 — 진짜 0 아님)": conf_st <= 1e-12, "C 재계산 롤링≤0 동결": roll <= 0,
             "D 롤링≤0.028 동결(가상)": roll <= 0.028, "E 롤링 하위 10% 동결(가상)": roll <= roll.quantile(0.10)}
    for name, r in rules.items():
        r = r.reindex(sessions).fillna(False)
        variants[name] = simulate(score, close, r, sessions) if r.any() else None
    result["freeze"] = {}
    for name, df in variants.items():
        if df is None:
            result["freeze"][name] = {"note": "동결 세션 0 — A 와 같다"}
            continue
        mask = rules.get(name)
        result["freeze"][name] = {"all": perf(df), "frozen_sessions": int(df["frozen"].sum())}
    # 동결 세션들에서 '계속 매매' 대 '동결' 의 차이(같은 날 비교) + 동결 구간 이후 60세션
    for name, r in rules.items():
        df = variants.get(name)
        if df is None:
            continue
        m = r.reindex(df.index).fillna(False)
        diff = (df["ret"] - base["ret"]).reindex(df.index)
        result["freeze"][name]["diff_vs_A_ann_all"] = float(diff.mean() * 252)
        result["freeze"][name]["diff_vs_A_nw_t"] = ic_module.newey_west_t(diff, lag=WINDOW)
        gd = (df["gross"] - base["gross"]).reindex(df.index)
        result["freeze"][name]["diff_vs_A_gross_ann"] = float(gd.mean() * 252)
        result["freeze"][name]["diff_vs_A_gross_nw_t"] = ic_module.newey_west_t(gd, lag=WINDOW)
        result["freeze"][name]["A_gross_excess_on_rule_days_ann"] = float((base["gross"] - base["uni"])[m].mean() * 252) if m.any() else None
        # 동결 세션 + 그 뒤 보유가 갈라져 있는 날 전부가 diff 에 들어 있다
        result["freeze"][name]["A_excess_on_rule_days_ann"] = float((base["ret"] - base["uni"])[m].mean() * 252) if m.any() else None
        result["freeze"][name]["A_excess_off_rule_days_ann"] = float((base["ret"] - base["uni"])[~m].mean() * 252)
    csv = csv.join(base[["ret", "uni", "turn"]].add_prefix("A_"), how="left")
    csv.index.name = "session"
    csv.to_csv(OUT_CSV)
    ag.to_csv(OUT_DIR / "diag-confidence-regime-aging-20261011.csv", index=False)
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
