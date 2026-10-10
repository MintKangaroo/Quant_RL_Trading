"""진단 2차 — 논문 검증 가격·거래량 신호 15종(docs/diag/ta-literature-round2-2026-10-10.md 의 고정 정의). 6/30 까지만 읽는다.

    QUANT_RL_DUCKDB_MEMORY_LIMIT=900MB .venv/bin/python tools/diag_ta_round2.py

산출: logs/diag-ta-round2-20261010.log · .csv, 그리고 다음 단계(가능성 테스트)가 쓰는 신호 캐시 data/_diag/ta-round2/signals.pkl.
"""
from __future__ import annotations

import math
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from quant_rl_trading.analysts import ic as ic_module
from quant_rl_trading.selector import candidates as candidates_module
from quant_rl_trading.selector import ksic
from quant_rl_trading.store import Store
from tools import diag_ta_features as T

OUT = Path("data/_diag/ta-round2")
SIGN = {"ivol_60": -1, "resid_mom": +1, "mom_12_1": +1, "fip": +1, "ind_mom_6": +1, "trend_factor": +1, "mad_21_200": +1,
        "downside_beta": +1, "coskew": -1, "price_delay": +1, "vol_cv_60": -1, "hs_season": +1, "log_price": +1,
        "limit_hits_60": -1, "rev_illiq": -1}
ROUND1_NEW = ("shadow_asym_20", "pv_corr_20", "skew_60")


def rm(x: pd.DataFrame, n: int) -> pd.DataFrame:
    return x.rolling(n, min_periods=n).mean()


def market_return(r: pd.DataFrame, uni: pd.DataFrame) -> pd.Series:
    return r.where(uni).clip(-0.5, 1.0).mean(axis=1)


def _r2(x: np.ndarray, yv: np.ndarray, sst: np.ndarray) -> np.ndarray:
    a = np.column_stack([np.ones(len(x)), x])
    beta, *_ = np.linalg.lstsq(a, yv, rcond=None)
    res = yv - a @ beta
    return 1 - (res ** 2).sum(axis=0) / sst


def price_delay(c: pd.DataFrame, m_daily: pd.Series) -> pd.DataFrame:
    """주간(5세션) 수익으로 52주 회귀. 시장 지연 0~4 를 넣은 R² 대비 당주만의 R². 주간 날짜에서 재고 그 사이는 앞 값."""
    idx = c.index
    wk = list(range(len(idx) - 1, -1, -5))[::-1]
    cw = c.iloc[wk]
    rw = cw / cw.shift(1) - 1.0
    mw = (1 + m_daily).cumprod().iloc[wk]
    mw = mw / mw.shift(1) - 1.0
    out = pd.DataFrame(np.nan, index=cw.index, columns=c.columns)
    for k in range(56, len(cw)):
        y = rw.iloc[k - 51:k + 1]
        lags = np.column_stack([mw.iloc[k - 51 - j:k + 1 - j].to_numpy() for j in range(5)])
        if np.isnan(lags).any():
            continue
        ok = y.notna().all(axis=0)
        if ok.sum() < 50:
            continue
        yv = y.loc[:, ok].to_numpy()
        yc = yv - yv.mean(axis=0)
        sst = (yc ** 2).sum(axis=0)

        r_full, r_one = _r2(lags, yv, sst), _r2(lags[:, :1], yv, sst)
        with np.errstate(divide="ignore", invalid="ignore"):
            out.iloc[k, np.flatnonzero(ok.to_numpy())] = 1 - r_one / r_full
    return out.reindex(idx).ffill(limit=5)


def trend_factor(c: pd.DataFrame, label: pd.DataFrame, uni: pd.DataFrame) -> pd.DataFrame:
    lens = (5, 10, 20, 50, 100, 200)
    ratios = {L: (rm(c, L) / c).where(uni) for L in lens}
    days = list(c.index)
    betas = {}
    for i, d in enumerate(days):
        y = label.loc[d]
        x = pd.concat([ratios[L].loc[d] for L in lens], axis=1)
        frame = pd.concat([y.rename("y"), x], axis=1).dropna()
        if len(frame) < 200:
            continue
        z = (frame - frame.mean()) / frame.std().replace(0, np.nan)
        z = z.dropna(axis=1)
        if z.shape[1] != len(lens) + 1:
            continue
        a = np.column_stack([np.ones(len(z)), z.iloc[:, 1:].to_numpy()])
        b, *_ = np.linalg.lstsq(a, z["y"].to_numpy(), rcond=None)
        betas[i] = b[1:]
    out = pd.DataFrame(np.nan, index=c.index, columns=c.columns)
    keys = sorted(betas)
    for i, d in enumerate(days):
        past = [betas[k] for k in keys if i - 6 - 250 <= k <= i - 6]      # 라벨 닫힌 날만(라벨 = d+1→d+6)
        if len(past) < 120:
            continue
        bbar = np.mean(past, axis=0)
        x = pd.concat([ratios[L].loc[d] for L in lens], axis=1)
        xz = (x - x.mean()) / x.std()
        out.loc[d] = xz.to_numpy() @ bbar
    return out


def signals(store: Store, d: dict[str, pd.DataFrame], uni: pd.DataFrame, label_full: pd.DataFrame) -> dict[str, pd.DataFrame]:
    c, v = d["close"], d["value"]
    r = c / c.shift(1) - 1.0
    m = market_return(r, uni)
    f: dict[str, pd.DataFrame] = {}
    # 1 · 2 · 8 · 9 — 시장모형 롤링 모멘트
    def beta_var(n: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
        mr, mm = rm(r, n), m.rolling(n, min_periods=n).mean()
        cov = rm(r.mul(m, axis=0), n).sub(mr.mul(mm, axis=0))
        var_m = m.rolling(n, min_periods=n).var(ddof=0)
        return cov.div(var_m, axis=0), r.rolling(n, min_periods=n).var(ddof=0), var_m
    b60, vr60, vm60 = beta_var(60)
    f["ivol_60"] = np.sqrt((vr60 - b60.pow(2).mul(vm60, axis=0)).clip(lower=0))
    b252, _, _ = beta_var(252)
    alpha252 = rm(r, 252).sub(b252.mul(m.rolling(252, min_periods=252).mean(), axis=0))
    e = r - alpha252.shift(1) - b252.shift(1).mul(m, axis=0)
    es = e.shift(21).rolling(231, min_periods=200)
    f["resid_mom"] = es.sum() / es.std()
    f["mom_12_1"] = c.shift(21) / c.shift(252) - 1.0
    pos = (r > 0).astype(float).where(r.notna()).shift(21).rolling(231, min_periods=200).mean()
    neg = (r < 0).astype(float).where(r.notna()).shift(21).rolling(231, min_periods=200).mean()
    id_ = np.sign(f["mom_12_1"]) * (neg - pos)
    f["fip"] = f["mom_12_1"] * (-id_)
    rr = r.where((m < 0).to_numpy()[:, None] & r.notna().to_numpy())        # 시장 하락일만
    mmb = (rr * 0).add(m, axis=0)                                          # 같은 칸의 시장 수익
    n_dn = rr.notna().astype(float).rolling(252, min_periods=252).sum()
    s_rm = (rr * mmb).fillna(0).rolling(252, min_periods=252).sum()
    s_r, s_m = rr.fillna(0).rolling(252, min_periods=252).sum(), mmb.fillna(0).rolling(252, min_periods=252).sum()
    s_mm = (mmb ** 2).fillna(0).rolling(252, min_periods=252).sum()
    cov_dn = s_rm / n_dn - (s_r / n_dn) * (s_m / n_dn)
    var_dn = s_mm / n_dn - (s_m / n_dn) ** 2
    f["downside_beta"] = (cov_dn / var_dn).where(n_dn >= 40)
    mu_r, mu_m = rm(r, 252), m.rolling(252, min_periods=252).mean()
    e_rm2, e_rm = rm(r.mul(m ** 2, axis=0), 252), rm(r.mul(m, axis=0), 252)
    e_m2 = (m ** 2).rolling(252, min_periods=252).mean()
    num = e_rm2 - e_rm.mul(2 * mu_m, axis=0) - mu_r.mul(e_m2, axis=0) + mu_r.mul(2 * mu_m ** 2, axis=0)
    f["coskew"] = num.div(m.rolling(252, min_periods=252).var(ddof=0), axis=0) / r.rolling(252, min_periods=252).std(ddof=0)
    # 5 업종 모멘텀
    # 업종은 참조 속성(data-contract §3) — sectors 가 2026-08 에야 적재돼 6/30 시점 조회는 0행이다. 진단에선 지금 분류를 쓴다(문서에 적음).
    from datetime import UTC, datetime
    sec = ksic.roll_up_map(candidates_module.sector_map(store, as_of=datetime.now(UTC), entities=list(c.columns), market="KR",  # invariant-allow: wallclock
                                                        source="dart_company", lookback=None))
    print(f"업종 매핑 {len(sec)}종목 · {len(set(sec.values()))}군")
    r126 = (c / c.shift(126) - 1.0).where(uni)
    smap = pd.Series(sec)
    ind = pd.DataFrame(np.nan, index=c.index, columns=c.columns)
    for members in smap.groupby(smap).groups.values():
        cols = [x for x in members if x in c.columns]
        if len(cols) >= 5:
            ind.loc[:, cols] = np.repeat(r126[cols].mean(axis=1).to_numpy()[:, None], len(cols), axis=1)
    f["ind_mom_6"] = ind
    f["trend_factor"] = trend_factor(c, label_full, uni)
    f["mad_21_200"] = rm(c, 21) / rm(c, 200)
    f["price_delay"] = price_delay(c, m)
    f["vol_cv_60"] = v.rolling(60, min_periods=60).std() / rm(v, 60)
    hs = []
    for k in (1, 2, 3, 4):
        hs.append(c.shift(252 * k - 21) / c.shift(252 * k) - 1.0)
    f["hs_season"] = pd.concat(hs).groupby(level=0).mean().reindex(c.index)
    f["log_price"] = np.log(d["close_raw"]) if "close_raw" in d else np.log(c)   # 원주가 — 정정 2026-10-10(보정 종가는 미래 분할을 본다)
    f["limit_hits_60"] = (r.abs() >= 0.29).astype(float).where(r.notna()).rolling(60, min_periods=60).sum()
    amihud = rm(r.abs() / (v / 1e8), 20)
    f["rev_illiq"] = (c / c.shift(5) - 1.0) * amihud.rank(axis=1, pct=True)
    return {k: x.replace([np.inf, -np.inf], np.nan) for k, x in f.items()}


def k200_mask(store: Store, days: list[date], cols: pd.Index) -> pd.DataFrame:
    im = store.get("index_members", as_of=T.END, lookback=(T.END.date() - date(2022, 1, 1)).days, market="KR")
    im = im[im["index_id"].astype(str).str.contains("KOSPI200")].copy()
    im["d"] = pd.to_datetime(im["valid_from"]).dt.date
    snaps = {d0: set(g["entity_id"].astype(str)) for d0, g in im.groupby("d")}
    keys = sorted(snaps)
    out = pd.DataFrame(False, index=days, columns=cols)
    for d0 in days:
        prev = [k for k in keys if k <= d0]
        if prev:
            out.loc[d0, out.columns.isin(snaps[prev[-1]])] = True
    return out


def main() -> int:
    store = Store(root=Path("data"))
    d = T.load(store)
    c, v = d["close"], d["value"]
    label_full = (c.shift(-6) / c.shift(-1) - 1.0)
    label_full = label_full.where(label_full.abs() <= 0.5)
    label20_full = (c.shift(-21) / c.shift(-1) - 1.0)
    label20_full = label20_full.where(label20_full.abs() <= 1.0)
    uni = (T.rmean(v, 20) >= T.MIN_VALUE) & (c.notna().rolling(252, min_periods=1).sum() >= 252)
    lab_u = label_full.where(uni)
    days = [x for x in c.index if x >= T.START_DECISION]
    label = lab_u.loc[days]
    label = label[label.notna().sum(axis=1) >= 100]
    days = list(label.index)
    label20 = label20_full.where(uni).loc[days]
    print(f"결정일 {days[0]}~{days[-1]} {len(days)}일")
    f1 = T.features(d)
    sig = signals(store, d, uni, lab_u)
    sig["shadow_asym_20"] = f1["upper_shadow_20"] - f1["lower_shadow_20"]
    sig["pv_corr_20"], sig["skew_60"] = f1["pv_corr_20"], f1["skew_60"]
    sig = {k: x.where(uni).loc[days] for k, x in sig.items()}
    controls = [f1[k].where(uni).loc[days] for k in ("_rev5", "_mom60", "_lowvol", "_liq")]
    k200 = k200_mask(store, days, c.columns)
    rows, pvals = [], {}
    for name, sign in SIGN.items():
        ic = T.rowwise_ic(sig[name], label)
        t = float(ic_module.newey_west_t(ic, lag=4))
        if np.isfinite(t):
            pvals[name] = math.erfc(abs(t) / math.sqrt(2))      # NaN 은 BH 에 넣지 않는다(10/10 결함: 빈 신호가 통과 목록에 섞였다)
        ic20 = T.rowwise_ic(sig[name], label20)
        reg = {rn: float(ic[(ic.index >= a) & (ic.index <= b)].mean()) for rn, a, b in T.REGIMES}
        top, bot = T.decile_excess(sig[name], label)
        rows.append({"name": name, "sign": sign, "ic": float(ic.mean()), "t": t, "ic20": float(ic20.mean()),
                     "t20": float(ic_module.newey_west_t(ic20, lag=19)), **reg, "top": top, "bot": bot})
    r1 = pd.read_csv("logs/diag-ta-features-20261010.csv", index_col=0)
    for n1, row in r1.iterrows():
        pvals[f"r1:{n1}"] = math.erfc(abs(float(row["t"])) / math.sqrt(2))
    passed = T.bh(pvals)
    for r in rows:
        r["후보"] = bool(np.sign(r["ic"]) == r["sign"] and abs(r["t"]) >= 2 and r["name"] in passed)
        if r["후보"]:
            pic = T.rowwise_ic(T.residualize(sig[r["name"]], controls), label)
            r["편IC"], r["편t"] = float(pic.mean()), float(ic_module.newey_west_t(pic, lag=4))
    out = pd.DataFrame(rows).set_index("name")
    new = [n for n, r in out.iterrows() if r["후보"] and abs(r.get("편t", 0) or 0) >= 2 and np.sign(r["편IC"]) == r["sign"]]
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 30)
    print(out.round(4).to_string())
    print(f"\n33 누적 BH 통과(2차): {sorted(x for x in passed if not x.startswith('r1:'))}\n2차 새 재료(편 IC 부호·|t|≥2): {new}")
    print("\n== K200 안 IC(그 시점 구성종목) — 새 재료 + 1차 새 재료")
    lab_k = label.where(k200)
    for name in [*new, *ROUND1_NEW]:
        ic = T.rowwise_ic(sig[name].where(k200), lab_k)
        ick = ic[ic.index.isin(lab_k.index[lab_k.notna().sum(axis=1) >= 100])]
        print(f"  {name:<16} K200 IC {ick.mean():+.4f} t {ic_module.newey_west_t(ick, lag=4):+.2f} ({len(ick)}일)")
    out.to_csv("logs/diag-ta-round2-20261010.csv")
    OUT.mkdir(parents=True, exist_ok=True)
    pd.to_pickle({"signals": {k: sig[k] for k in [*new, *ROUND1_NEW]}, "label": label, "k200": k200,
                  "close": c.loc[:days[-1]], "value": v.loc[:days[-1]], "uni": uni.loc[days], "new": new}, OUT / "signals.pkl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
