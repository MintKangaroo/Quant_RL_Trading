"""장 × 전략 지도 — 진단(시행 아님). 판단 틀은 docs/diag/regime-strategy-map-2026-10.md §1~§4(계산 전 고정).

    .venv/bin/python tools/diag_regime_strategy_map.py load      # 과거 금고 + 본 창고 이어 붙인 넓은 표 → 캐시
    .venv/bin/python tools/diag_regime_strategy_map.py tg        # TG(기술적 합성) 점수: 과거 금고 굽기 + 본 창고 composite_panel
    .venv/bin/python tools/diag_regime_strategy_map.py regimes   # 대용 지수 · V6 · HMM · 사후 라벨
    .venv/bin/python tools/diag_regime_strategy_map.py map       # 전략 장부 · 지도 · 위약 · 전환 대 단일 → results.json

**날짜 상한 2026-06-30**: 모든 읽기 as_of = until = UNTIL, 읽은 뒤 `guard` 가 모든 표의 날짜를 다시 본다(넘으면 멈춘다).
산출: data/_diag/regime-strategy-map/ (연구 캐시, 창고 아님).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, date, datetime, time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy import stats as sps  # noqa: E402

from quant_rl_trading.analysts import ic as ic_module  # noqa: E402
from quant_rl_trading.analysts.regime import classify  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from quant_rl_trading.store.prices import read_prices  # noqa: E402
from tools.trial_overlay import ONE_WAY_COST  # noqa: E402

UNTIL = date(2026, 6, 30)            # 두 번째 금고(10/1~)와 인접한 7/1 이후는 한 행도 쓰지 않는다
VAULT_END = date(2021, 8, 10)
OUT = Path("data/_diag/regime-strategy-map")
FIRST_DECISION = date(2011, 1, 3)
EVERY = 5
COST = ONE_WAY_COST
MIN_VALUE = 1e9
TOP, MEGA_N, CAP_LIMIT = 200, 30, 0.30
PERIODS = (("2011~14", date(2011, 1, 1), date(2014, 12, 31)), ("2015~18", date(2015, 1, 1), date(2018, 12, 31)),
           ("2019~21", date(2019, 1, 1), date(2021, 12, 31)), ("2022~24", date(2022, 1, 1), date(2024, 12, 31)),
           ("2025~26H1", date(2025, 1, 1), UNTIL))
def _exposure_cfg() -> dict:
    """실전 V6 임계치 — config/quant_rl_trading.yaml 의 exposure 절에서 읽는다(하드코딩 금지, 불변식 10)."""
    import yaml
    cfg = yaml.safe_load((REPO_ROOT / "config/quant_rl_trading.yaml").read_text())["exposure"]
    return {"scale": dict(cfg["regime_scale"]), "floor": float(cfg["crisis_momentum_floor"]), "confirm": int(cfg["regime_confirm_sessions"])}


_EXP = _exposure_cfg()
V6_SCALE, V6_FLOOR, V6_CONFIRM = _EXP["scale"], _EXP["floor"], _EXP["confirm"]
HMM_MAP, HMM_STEP, HMM_BAND = (0.5, 0.8, 1.0), 0.05, 0.10
LAGS = (0, 1, 5, 10)
MIN_EP = 5
N_PLACEBO, N_PLACEBO_SW = 1000, 200


def guard(*frames) -> None:
    for f in frames:
        idx = f.index if not isinstance(f, pd.Series) or not isinstance(f.index, pd.MultiIndex) else f.index.get_level_values(0)
        if len(idx) and max(idx) > UNTIL:
            raise SystemExit(f"날짜 상한 위반: {max(idx)} > {UNTIL}")


def _as_of(d: date) -> datetime:
    return datetime.combine(d, time(23, 59), tzinfo=UTC)


def _wide(store: Store, end: date, start: date, adjusted: bool, cols: list[str]) -> dict[str, pd.DataFrame]:
    a = _as_of(end)
    p = read_prices(store, as_of=a, until=a, lookback=(end - start).days, columns=cols, adjusted=adjusted, market="KR")
    p = p[p["entity_id"].str.match(r"^KR:\d{6}$")]
    p["day"] = pd.to_datetime(p["valid_from"]).dt.date
    return {c: p.pivot_table(index="day", columns="entity_id", values=c, aggfunc="last").sort_index() for c in cols}


def _mcap(store: Store, end: date, start: date) -> pd.DataFrame:
    ms = store.get("market_stats", as_of=_as_of(end), until=_as_of(end), lookback=(end - start).days, market="KR",
                   columns=["entity_id", "valid_from", "metric", "value"])
    ms = ms[ms["metric"].astype(str).str.contains("market_cap")]
    ms["day"] = pd.to_datetime(ms["valid_from"]).dt.date
    return ms.pivot_table(index="day", columns="entity_id", values="value", aggfunc="last").sort_index()


def cmd_load() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rets, vals, caps, raws = [], [], [], []
    for root, start, end in ((Path("data/_past_vault"), date(2009, 12, 1), VAULT_END), (Path("data"), date(2021, 8, 1), UNTIL)):
        store = Store(root=root)
        w = _wide(store, end, start, True, ["close", "value"])
        c = w["close"]
        c = c[c.index <= end]
        rets.append((c / c.shift(1) - 1.0))
        vals.append(w["value"].reindex_like(c))
        # 이음매용 원 종가: 과거 금고는 마지막 열흘, 본 창고는 첫 열흘
        r_end = end if root.name == "_past_vault" else date(2021, 8, 20)
        raws.append(_wide(store, r_end, r_end.replace(day=1), False, ["close"])["close"])
        caps.append(_mcap(store, end, start))
        print(f"{root}: {c.index.min()}~{c.index.max()} 세션 {len(c)} 종목 {c.shape[1]}", flush=True)
    # 이음매 하루: 본 창고 첫 날 수익 = 원 종가 비율(과거 금고 마지막 날 원 종가 → 본 창고 첫 날 원 종가)
    first_main = rets[1].index.min()
    seam = (raws[1].loc[first_main] / raws[0].loc[VAULT_END].reindex(raws[1].columns) - 1.0)
    rets[1].loc[first_main] = seam.reindex(rets[1].columns)
    ret = pd.concat(rets).sort_index()
    ret = ret[~ret.index.duplicated(keep="last")].clip(-0.5, 1.0)
    value = pd.concat(vals).sort_index()
    value = value[~value.index.duplicated(keep="last")].reindex(index=ret.index, columns=ret.columns)
    mcap = pd.concat(caps).sort_index()
    mcap = mcap[~mcap.index.duplicated(keep="last")].reindex(index=ret.index, columns=ret.columns)
    guard(ret, value, mcap)
    pd.to_pickle({"ret": ret.astype("float32"), "value": value.astype("float32"), "mcap": mcap.astype("float32")}, OUT / "panel.pkl")
    print(f"이은 표: {ret.index.min()}~{ret.index.max()} 세션 {len(ret)} 종목 {ret.shape[1]} · 이음매 {first_main} 수익 중앙 {seam.median():+.4f}")


def cmd_tg() -> None:
    from tools.ta_composite import composite_panel
    vault = pd.read_pickle(Path("data/_past_vault/_bake/ta.pkl"))  # invariant-allow: data-access — 과거 금고 굽기(소진)
    vw = vault.pivot_table(index="session", columns="entity_id", values="r_hat", aggfunc="last")
    vw = vw[vw.index <= date(2021, 7, 30)]
    comp, _ = composite_panel(Store(root=Path("data")), end=datetime.combine(UNTIL, time(16), tzinfo=UTC), start=date(2021, 8, 1))
    comp = comp[comp.index <= UNTIL]
    tg = pd.concat([vw, comp]).sort_index()
    tg = tg[~tg.index.duplicated(keep="last")]
    guard(tg)
    tg.astype("float32").to_pickle(OUT / "tg.pkl")
    print(f"TG: 과거 {vw.index.min()}~{vw.index.max()} · 본 {comp.dropna(how='all').index.min()}~{comp.index.max()}")


def capped(w: pd.Series, limit: float = CAP_LIMIT) -> pd.Series:
    w = w / w.sum()
    for _ in range(50):
        over = w > limit
        if not over.any():
            break
        excess = (w[over] - limit).sum()
        w[over] = limit
        rest = ~over
        w[rest] += excess * w[rest] / w[rest].sum()
    return w


def load_panel():
    z = pd.read_pickle(OUT / "panel.pkl")  # invariant-allow: data-access — 진단 캐시
    ret, value, mcap = (z[k].astype("float64") for k in ("ret", "value", "mcap"))
    guard(ret, value, mcap)
    return ret, value, mcap


def cap_book_gross(ret: pd.DataFrame, mcap: pd.DataFrame) -> pd.Series:
    """대용 지수 — 매 세션 전날 시총 상위 200 시총가중(상한 30%)의 다음 날 수익(비용 전). 국면 판정 전용."""
    out = pd.Series(np.nan, index=ret.index)
    m = mcap.shift(1)
    for i, d in enumerate(ret.index[1:], start=1):
        top = m.iloc[i].dropna().nlargest(TOP)
        if len(top) < 50:
            continue
        w = capped(top.copy())
        r = ret.iloc[i].reindex(w.index).fillna(0.0)
        out.iloc[i] = float(r @ w)
    return out


def cmd_regimes() -> None:
    from tools.v2_regime_hmm import run as hmm_run
    ret, value, mcap = load_panel()
    idx_ret = cap_book_gross(ret, mcap).dropna()
    level = (1.0 + idx_ret).cumprod()
    days = list(level.index)
    floor = V6_FLOOR
    v6 = {}
    for i, d in enumerate(days):
        lo = pd.Timestamp(d) - pd.Timedelta(days=400)
        s = level.iloc[: i + 1]
        s = s[pd.to_datetime(s.index) > lo]
        v6[d] = classify(s, crisis_floor=floor)
    v6 = pd.Series(v6)
    print("V6 분포", v6.value_counts().to_dict(), flush=True)
    hmm = hmm_run(level, 3)
    print("HMM 세션", len(hmm), hmm["state"].value_counts().to_dict(), flush=True)
    fwd = level.shift(-60) / level - 1.0
    post = pd.Series(np.where(fwd > 0.05, "up", np.where(fwd < -0.05, "down", "flat")), index=level.index).where(fwd.notna())
    guard(level, v6, hmm, post)
    pd.to_pickle({"level": level, "v6": v6, "hmm": hmm, "post": post}, OUT / "regimes.pkl")


# ---------------------------------------------------------------------------------------------------------------- 전략

def book(weights: dict, ret: pd.DataFrame) -> tuple[pd.Series, float]:
    """결정일 d 의 비중 → d+2 부터 수익(d+1 종가 진입). 드리프트 무시, 재조정일에 |Δw| × 편도 비용. 현금은 0 수익. None 이면 그 구간 NaN."""
    days = list(ret.index)
    pos = {d: i for i, d in enumerate(days)}
    out = pd.Series(np.nan, index=days)
    prev = pd.Series(dtype=float)
    turns = []
    keys = sorted(weights)
    for j, d in enumerate(keys):
        w = weights[d]
        i0 = pos[d] + 2
        i1 = pos[keys[j + 1]] + 2 if j + 1 < len(keys) else len(days)
        if i0 >= len(days):
            break
        if w is None:
            prev = pd.Series(dtype=float)
            continue
        seg = ret.iloc[i0:i1].reindex(columns=w.index).fillna(0.0)
        out.iloc[i0:i1] = seg.to_numpy() @ w.to_numpy() if len(w) else 0.0
        u = w.index.union(prev.index)
        turn = float((w.reindex(u, fill_value=0) - prev.reindex(u, fill_value=0)).abs().sum())
        out.iloc[i0] -= turn * COST
        turns.append(turn / 2)
        prev = w
    return out, float(np.mean(turns) * 252 / EVERY) if turns else float("nan")


def ew(names) -> pd.Series:
    names = list(names)
    return pd.Series(1.0 / len(names), index=names) if names else pd.Series(dtype=float)


def strategy_weights(ret, value, mcap, tg, reg) -> tuple[list, dict]:
    days = list(ret.index)
    px = (1.0 + ret.fillna(0.0)).cumprod().where(ret.notna() | ret.shift(1).notna())
    hist = ret.notna().rolling(252, min_periods=1).sum()
    uni = (value.rolling(20, min_periods=20).mean() >= MIN_VALUE) & (hist >= 250)
    vol60 = ret.rolling(60, min_periods=40).std()
    mom12 = px.shift(21) / px.shift(252) - 1.0
    mom6 = px.shift(21) / px.shift(126) - 1.0
    rev5 = px / px.shift(5) - 1.0
    tgl = tg.reindex(index=days).shift(1)        # 하루 늦춤(결정 d 에 d−1 점수)
    v6, hmm = reg["v6"], reg["hmm"]
    last = days[-3]
    reb = [d for d in days if FIRST_DECISION <= d <= last][::EVERY]
    W = {k: {} for k in ("CAP", "EW", "EW200", "MEGA", "LOWVOL", "MOM12", "MOM6", "REV5", "EXCL10", "EXCL20", "CAPX10", "CAP_V6", "CAP_HMM", "CASH")}
    v6_scale = v6.map(V6_SCALE)
    v6_k = pd.concat([v6_scale.shift(i) for i in range(V6_CONFIRM)], axis=1).min(axis=1)   # 확인 N세션: 최근 N 국면 배수의 최솟값
    p = hmm[["p0", "p1", "p2"]]
    hmm_raw = (np.round((p.to_numpy() @ np.array(HMM_MAP)) / HMM_STEP) * HMM_STEP)
    hmm_k_raw = pd.Series(hmm_raw, index=hmm.index)
    hmm_k, cur = {}, None
    for d, k in hmm_k_raw.items():
        if cur is None or abs(k - cur) >= HMM_BAND - 1e-9:
            cur = k
        hmm_k[d] = cur
    hmm_k = pd.Series(hmm_k)
    for d in reb:
        u = uni.loc[d]
        names = list(u.index[u.to_numpy()])
        cap = mcap.loc[d].dropna()
        cap = cap[cap.index.isin(ret.columns[ret.loc[d].notna()])]
        top = cap.nlargest(TOP)
        W["CAP"][d] = capped(top.copy())
        W["EW"][d] = ew(names)
        W["EW200"][d] = ew(top.index)
        W["MEGA"][d] = capped(cap.nlargest(MEGA_N).copy())
        n5 = max(1, len(names) // 5)
        W["LOWVOL"][d] = ew(vol60.loc[d, names].dropna().nsmallest(n5).index)
        W["MOM12"][d] = ew(mom12.loc[d, names].dropna().nlargest(n5).index)
        W["MOM6"][d] = ew(mom6.loc[d, names].dropna().nlargest(n5).index)
        W["REV5"][d] = ew(rev5.loc[d, names].dropna().nsmallest(n5).index)
        s = tgl.loc[d].reindex(names).dropna() if d in tgl.index else pd.Series(dtype=float)
        if len(s) >= 0.5 * len(names) and len(s) > 50:
            cut10 = set(s.nsmallest(len(s) // 10).index)
            cut20 = set(s.nsmallest(len(s) // 5).index)
            W["EXCL10"][d] = ew([x for x in names if x not in cut10])
            W["EXCL20"][d] = ew([x for x in names if x not in cut20])
            keep = top[~top.index.isin(cut10)]
            W["CAPX10"][d] = capped(keep.copy())
        else:
            W["EXCL10"][d] = W["EXCL20"][d] = W["CAPX10"][d] = None
        k6 = float(v6_k.get(d, 1.0)) if d in v6_k.index else 1.0
        W["CAP_V6"][d] = W["CAP"][d] * (k6 if np.isfinite(k6) else 1.0)
        kh = float(hmm_k.get(d, 1.0)) if d in hmm_k.index else 1.0
        W["CAP_HMM"][d] = W["CAP"][d] * kh
        W["CASH"][d] = pd.Series(dtype=float)
    return reb, W


# ---------------------------------------------------------------------------------------------------------------- 지도

def decision_labels(lab: pd.Series, days: list, lag: int) -> pd.Series:
    """수익일 t 의 라벨 = 결정일 t−2 의 라벨(그 결정이 아는 것은 label(t−2−lag))."""
    s = lab.reindex(days).shift(lag)
    return s.shift(2)


def episodes(lab: pd.Series) -> pd.Series:
    """연속 같은 라벨 구간 번호."""
    return (lab != lab.shift(1)).cumsum().where(lab.notna())


def cell(ex: pd.Series, lab: pd.Series, state) -> dict:
    m = (lab == state) & ex.notna()
    e = ex[m]
    if len(e) < 20:
        return {"n_days": int(len(e))}
    ep = episodes(lab)[m]
    g = e.groupby(ep)
    sums, lens = g.sum(), g.size()
    long = sums[lens >= MIN_EP]
    sign = np.sign(e.mean())
    agree = float((np.sign(long) == sign).mean()) if len(long) else float("nan")
    if len(long) >= 3 and long.std() > 0:
        t_ep = float(long.mean() / (long.std() / np.sqrt(len(long))))
        p_ep = float(2 * sps.t.sf(abs(t_ep), len(long) - 1))
    else:
        t_ep, p_ep = float("nan"), float("nan")
    return {"n_days": int(len(e)), "ann": float(e.mean() * 252), "nw_t": float(ic_module.newey_west_t(e, lag=10)),
            "n_ep": int(len(sums)), "n_ep5": int(len(long)), "agree": agree, "t_ep": t_ep, "p_ep": p_ep}


def bh(p: np.ndarray, q: float = 0.10) -> np.ndarray:
    p = np.asarray(p, dtype=float)
    ok = np.isfinite(p)
    out = np.zeros(len(p), dtype=bool)
    pv = p[ok]
    if not len(pv):
        return out
    order = np.argsort(pv)
    ranked = pv[order] * len(pv) / (np.arange(len(pv)) + 1)
    passed = ranked <= q
    if passed.any():
        kmax = np.max(np.flatnonzero(passed))
        sel = np.zeros(len(pv), dtype=bool)
        sel[order[: kmax + 1]] = True
        out[np.flatnonzero(ok)] = sel
    return out


def placebo_p(ex: pd.DataFrame, lab: pd.Series, states: list, rng, n: int) -> dict:
    """원형 이동 위약 — 상태 안 평균 초과의 |위약| ≥ |관측| 비율. ex 는 전략×일."""
    valid = lab.notna().to_numpy()
    labv = lab.to_numpy()
    X = ex.to_numpy()                       # 일 × 전략
    N = len(labv)
    obs = {}
    for s in states:
        m = (labv == s)
        obs[s] = np.nanmean(np.where(m[:, None], X, np.nan), axis=0)
    cnt = {s: np.zeros(X.shape[1]) for s in states}
    idx_valid = np.flatnonzero(valid)
    lv = labv[idx_valid]
    for _ in range(n):
        k = int(rng.integers(250, len(lv) - 250))
        sh = np.full(N, None, dtype=object)
        sh[idx_valid] = np.roll(lv, k)
        for s in states:
            m = (sh == s)
            mu = np.nanmean(np.where(m[:, None], X, np.nan), axis=0)
            cnt[s] += (np.abs(mu) >= np.abs(obs[s]) - 1e-15)
    return {s: (cnt[s] + 1) / (n + 1) for s in states}


def perf(r: pd.Series, b: pd.Series, a: date, z: date) -> dict:
    m = (r.index >= a) & (r.index <= z)
    r, b = r[m], b[m]
    ok = r.notna() & b.notna()
    r, b = r[ok], b[ok]
    if len(r) < 60:
        return {}
    ex = r - b
    nav = (1 + r).cumprod()
    return {"ann": float((1 + r).prod() ** (252 / len(r)) - 1), "ex": float(ex.mean() * 252),
            "ir": float(ex.mean() / ex.std() * np.sqrt(252)) if ex.std() > 0 else float("nan"),
            "mdd": float((nav / nav.cummax() - 1).min())}


def switch_weights(reb, W, R, cap, lab_dec: pd.Series, mode: str, days: list, min_days=120) -> dict:
    """워크포워드 전환. R: 전략 → 비용 후 일수익. lab_dec: 수익일의 결정 라벨(지연 반영), 결정일 라벨은 lab_dec.shift(-2)."""
    keys = [k for k in W if k != "CAP"]
    ex = pd.DataFrame({k: R[k] - cap for k in keys})
    exv, labv = ex.to_numpy(), lab_dec.to_numpy()
    pos = {d: i for i, d in enumerate(days)}
    cur_lab = lab_dec.shift(-2)
    ep = episodes(lab_dec)
    out, chosen = {}, {}
    for d in reb:
        i = pos[d]
        s = cur_lab.iloc[i] if mode != "BS" else "all"
        pick = "CAP"
        if mode == "BS":
            past = exv[: i + 1]
            n_ok = np.sum(np.isfinite(past), axis=0)
            mu = np.where(n_ok >= 250, np.nanmean(past, axis=0), -np.inf)
            if np.isfinite(mu).any() and np.nanmax(mu) > 0:
                pick = keys[int(np.nanargmax(mu))]
        elif s is not None and pd.notna(s):
            m = (labv[: i + 1] == s)
            if m.sum() >= min_days and ep.iloc[: i + 1][m].nunique() >= 2:
                sub = exv[: i + 1][m]
                n_ok = np.sum(np.isfinite(sub), axis=0)
                mu = np.where(n_ok >= min_days, np.nanmean(sub, axis=0), -np.inf)
                if mode == "SWt":   # 상태 안 NW t(lag 10) ≥ 2 인 것만
                    for j in np.flatnonzero(np.isfinite(mu) & (mu > 0)):
                        col = pd.Series(sub[:, j]).dropna()
                        if float(ic_module.newey_west_t(col, lag=10)) < 2.0:
                            mu[j] = -np.inf
                if np.isfinite(mu).any() and np.nanmax(mu) > 0:
                    cand = keys[int(np.nanargmax(mu))]
                    if W[cand].get(d) is not None:
                        pick = cand
        chosen[d] = pick
        out[d] = W[pick][d]
    return out, chosen


def oracle_switch(reb, W, R, cap, lab_dec: pd.Series, days) -> tuple[dict, dict]:
    keys = [k for k in W if k != "CAP"]
    ex = pd.DataFrame({k: R[k] - cap for k in keys})
    best = {}
    for s in lab_dec.dropna().unique():
        mu = ex[lab_dec == s].mean()
        best[s] = mu.idxmax() if mu.max() > 0 else "CAP"
    cur = lab_dec.shift(-2)
    pos = {d: i for i, d in enumerate(days)}
    out = {}
    for d in reb:
        s = cur.iloc[pos[d]]
        k = best.get(s, "CAP")
        out[d] = W[k][d] if W[k].get(d) is not None else W["CAP"][d]
    return out, best


def cmd_map(args) -> None:
    rng = np.random.default_rng(20261013)
    ret, value, mcap = load_panel()
    tg = pd.read_pickle(OUT / "tg.pkl")  # invariant-allow: data-access — 진단 캐시
    reg = pd.read_pickle(OUT / "regimes.pkl")  # invariant-allow: data-access — 진단 캐시
    guard(tg, reg["v6"], reg["hmm"])
    days = list(ret.index)
    reb, W = strategy_weights(ret, value, mcap, tg, reg)
    R, TURN = {}, {}
    for k, w in W.items():
        R[k], TURN[k] = book(w, ret)
        print(f"장부 {k}: 회전 {TURN[k]:.1f}", flush=True)
    start = days.index(reb[0]) + 2
    R = {k: v.iloc[start:] for k, v in R.items()}
    rdays = list(R["CAP"].index)
    cap = R["CAP"]
    guard(cap)
    strat = [k for k in W if k != "CAP"]
    ex = pd.DataFrame({k: R[k] - cap for k in strat})
    labs = {"V6": reg["v6"], "HMM": reg["hmm"]["state"].map(lambda x: f"h{int(x)}"), "POST": reg["post"]}
    res = {"periods": [p[0] for p in PERIODS], "turnover": TURN, "map": [], "perf": {}, "switch_choices": {}}
    # 1) 지도
    fam = []
    for name, lab in labs.items():
        for lag in (LAGS if name != "POST" else (0,)):
            ld = decision_labels(lab, days, lag).reindex(rdays)
            states = sorted(ld.dropna().unique())
            pp = placebo_p(ex, ld, states, rng, N_PLACEBO if lag in (0, 5) or name == "POST" else 200)
            for s in states:
                per_state_period = {}
                for pn, a, z in PERIODS:
                    mm = (ld.index >= a) & (ld.index <= z) & (ld == s).to_numpy()
                    per_state_period[pn] = {k: (float(ex.loc[mm, k].mean() * 252) if ex.loc[mm, k].notna().sum() >= 20 else None) for k in strat}
                for j, k in enumerate(strat):
                    c = cell(ex[k], ld, s)
                    c.update({"def": name, "lag": lag, "state": s, "strategy": k, "p_placebo": float(pp[s][j]),
                              "periods": {pn: per_state_period[pn][k] for pn in per_state_period}})
                    res["map"].append(c)
                    if lag == 0 and name != "POST":
                        fam.append(len(res["map"]) - 1)
            print(f"지도 {name} 지연 {lag} 상태 {states}", flush=True)
    pe = np.array([res["map"][i].get("p_ep", np.nan) for i in fam])
    pz = np.array([res["map"][i].get("p_placebo", np.nan) for i in fam])
    be, bz = bh(pe), bh(pz)
    for j, i in enumerate(fam):
        res["map"][i]["bh_ep"], res["map"][i]["bh_placebo"] = bool(be[j]), bool(bz[j])
    res["family_size"] = len(fam)
    # 2) 전환 대 단일 대 지수+V6
    books = {"CAP": cap, "CAP_V6": R["CAP_V6"], "CAP_HMM": R["CAP_HMM"], "EW": R["EW"]}
    turns = {"CAP": TURN["CAP"], "CAP_V6": TURN["CAP_V6"], "CAP_HMM": TURN["CAP_HMM"], "EW": TURN["EW"]}
    Rfull = {k: v.reindex(days) for k, v in R.items()}
    capf = Rfull["CAP"]
    w_bs, ch = switch_weights(reb, W, Rfull, capf, pd.Series(index=days, dtype=object), "BS", days)
    books["BS"], turns["BS"] = book(w_bs, ret)
    res["switch_choices"]["BS"] = pd.Series(ch).value_counts().to_dict()
    for name in ("V6", "HMM"):
        for lag in (0, 5):
            ld = decision_labels(labs[name], days, lag)
            for mode in ("SW", "SWt"):
                w, ch = switch_weights(reb, W, Rfull, capf, ld, mode, days)
                key = f"{mode}-{name}-L{lag}"
                books[key], turns[key] = book(w, ret)
                res["switch_choices"][key] = pd.Series(ch).value_counts().to_dict()
                print(f"전환 {key} 회전 {turns[key]:.1f}", flush=True)
        ld0 = decision_labels(labs[name], days, 0)
        w, best = oracle_switch(reb, W, Rfull, capf, ld0, days)
        books[f"SW*-{name}"], turns[f"SW*-{name}"] = book(w, ret)
        res["switch_choices"][f"SW*-{name}"] = best
    ldp = decision_labels(labs["POST"], days, 0)
    w, best = oracle_switch(reb, W, Rfull, capf, ldp, days)
    books["SWpost"], turns["SWpost"] = book(w, ret)
    res["switch_choices"]["SWpost"] = best
    mu = {k: (Rfull[k] - capf).mean() for k in strat}
    bstar = max(mu, key=mu.get)
    books[f"BS*({bstar})"], turns[f"BS*({bstar})"] = Rfull[bstar], TURN[bstar]
    first, lastd = rdays[0], rdays[-1]
    for k, r in books.items():
        r = r.reindex(rdays)
        res["perf"][k] = {"all": perf(r, cap, first, lastd), "turn": turns[k], **{pn: perf(r, cap, a, z) for pn, a, z in PERIODS}}
    # 3) 전환 위약 — 셔플 라벨로 SW 를 다시
    pl = {}
    for name in ("V6", "HMM"):
        ld = decision_labels(labs[name], days, 0)
        valid = np.flatnonzero(ld.notna().to_numpy())
        vals = ld.to_numpy()[valid]
        dist = []
        for _ in range(args.placebo_sw):
            k = int(rng.integers(250, len(vals) - 250))
            sh = pd.Series(np.full(len(days), None, dtype=object), index=days)
            sh.iloc[valid] = np.roll(vals, k)
            w, _ = switch_weights(reb, W, Rfull, capf, sh, "SW", days)
            r, _ = book(w, ret)
            dist.append(float((r.reindex(rdays) - cap).mean() * 252))
        obs = res["perf"][f"SW-{name}-L0"]["all"]["ex"]
        pl[name] = {"obs": obs, "q": float(np.mean(np.array(dist) >= obs)), "p50": float(np.median(dist)), "p95": float(np.quantile(dist, 0.95)), "n": len(dist)}
        print(f"전환 위약 {name}: 관측 {obs:+.2%} · 위약 중앙 {pl[name]['p50']:+.2%} · 95% {pl[name]['p95']:+.2%} · 상위 비율 {pl[name]['q']:.3f}", flush=True)
    res["switch_placebo"] = pl
    # 4) 대용 국면 vs 실전 지수 국면 일치율(본 창고 구간) — 기록
    try:
        res["proxy_agreement"] = proxy_agreement(reg)
    except Exception as e:  # 기록용, 실패해도 지도는 남긴다
        res["proxy_agreement"] = {"error": repr(e)}
    guard(*[b.dropna() for b in books.values()])
    pd.to_pickle({"R": R, "books": books}, OUT / "books.pkl")
    (OUT / "results.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    print("저장", OUT / "results.json")


def proxy_agreement(reg) -> dict:
    store = Store(root=Path("data"))
    a = _as_of(UNTIL)
    idx = store.get("indices", as_of=a, until=a, lookback=(UNTIL - date(2020, 1, 1)).days, market="KR", columns=["entity_id", "valid_from", "close"])
    out = {}
    for eid in ("KR:IDX:KRX 300", "KR:IDX:KOSPI200"):
        s = idx[idx["entity_id"] == eid].assign(day=lambda f: pd.to_datetime(f["valid_from"]).dt.date).groupby("day")["close"].last().sort_index()
        s = s[(s > 0) & (s.index <= UNTIL)]
        if len(s) < 300:
            out[eid] = None
            continue
        lab = {}
        for i, d in enumerate(s.index):
            w = s.iloc[: i + 1]
            w = w[pd.to_datetime(w.index) > pd.Timestamp(d) - pd.Timedelta(days=400)]
            lab[d] = classify(w, crisis_floor=V6_FLOOR)
        lab = pd.Series(lab)
        v6 = reg["v6"].reindex(lab.index)
        m = (lab != "unknown") & v6.notna() & (v6 != "unknown")
        out[eid] = {"n": int(m.sum()), "agree": float((lab[m] == v6[m]).mean())}
    return out


def cmd_report() -> None:
    """results.json → 문서용 표(마크다운)."""
    res = json.loads((OUT / "results.json").read_text())
    M = pd.DataFrame(res["map"])
    pct = lambda x: "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x * 100:+.1f}"  # noqa: E731
    print(f"1차 가족 {res['family_size']} 조합 · BH(에피소드) 통과 {int(M['bh_ep'].fillna(False).sum())} · BH(위약) 통과 {int(M['bh_placebo'].fillna(False).sum())}")
    for name in ("V6", "HMM", "POST"):
        sub = M[(M["def"] == name) & (M["lag"] == 0)]
        print(f"\n### {name} 지연 0 — CAP 대비 비용 후 연초과 %p (에피소드≥5 수 · 부호 일치 · 에피소드 p · 위약 p)\n")
        states = sorted(sub["state"].unique())
        print("| 전략 | " + " | ".join(f"{s} ({int(sub[sub.state == s]['n_days'].max())}일 · {int(sub[sub.state == s]['n_ep5'].max())}회)" for s in states) + " |")
        print("|---|" + "---|" * len(states))
        for k in sub["strategy"].unique():
            cells = []
            for s in states:
                r = sub[(sub.state == s) & (sub.strategy == k)].iloc[0]
                star = "**" if r.get("bh_ep") is True and r.get("bh_placebo") is True else ""
                cells.append(f"{star}{pct(r.get('ann'))}{star} · {r.get('agree', float('nan')) * 100:.0f}% · p {r.get('p_ep', float('nan')):.2f}/{r.get('p_placebo', float('nan')):.2f}")
            print(f"| {k} | " + " | ".join(cells) + " |")
    print("\n### 지연 견고성 — 지연 0 에서 BH 둘 다 통과한 조합의 지연별 연초과\n")
    hit = M[(M["lag"] == 0) & (M["bh_ep"] == True) & (M["bh_placebo"] == True)]  # noqa: E712
    for _, r in hit.iterrows():
        row = M[(M["def"] == r["def"]) & (M["state"] == r["state"]) & (M["strategy"] == r["strategy"])].sort_values("lag")
        print(f"- {r['def']} {r['state']} {r['strategy']}: " + " · ".join(f"L{int(x.lag)} {pct(x.ann)}" for _, x in row.iterrows())
              + " · 구간 " + " / ".join(f"{k} {pct(v)}" for k, v in r["periods"].items()))
    print("\n### 전환 대 단일 대 지수+V6 (비용 후)\n")
    print("| 장부 | 연수익 | CAP 대비 연초과 | IR | MDD | 연회전 | " + " | ".join(f"{p} 초과" for p in res["periods"]) + " |")
    print("|---|---|---|---|---|---|" + "---|" * len(res["periods"]))
    for k, v in res["perf"].items():
        a = v["all"]
        print(f"| {k} | {pct(a.get('ann'))}% | {pct(a.get('ex'))} | {a.get('ir', float('nan')):+.2f} | {pct(a.get('mdd'))}% | {v['turn']:.1f} | "
              + " | ".join(pct(v.get(p, {}).get("ex")) for p in res["periods"]) + " |")
    print("\n전환 위약:", json.dumps(res["switch_placebo"], ensure_ascii=False))
    print("선택 분포:", json.dumps(res["switch_choices"], ensure_ascii=False))
    print("대용 국면 일치:", json.dumps(res.get("proxy_agreement"), ensure_ascii=False))
    print("회전:", {k: round(v, 1) for k, v in res["turnover"].items()})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["load", "tg", "regimes", "map", "report"])
    ap.add_argument("--placebo-sw", type=int, default=N_PLACEBO_SW)
    args = ap.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)
    {"load": cmd_load, "tg": cmd_tg, "regimes": cmd_regimes, "report": cmd_report}.get(args.step, lambda: cmd_map(args))()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
