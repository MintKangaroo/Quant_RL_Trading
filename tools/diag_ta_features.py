"""진단 — 기술적 지표 18종 IC 전수(docs/diag/ta-features-2026-10-10.md 의 고정 정의 그대로). 6/30 까지만 읽는다.

    QUANT_RL_DUCKDB_MEMORY_LIMIT=900MB .venv/bin/python tools/diag_ta_features.py
"""
from __future__ import annotations

import math
import sys
from datetime import UTC, date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from quant_rl_trading.analysts import ic as ic_module
from quant_rl_trading.store import Store
from quant_rl_trading.store.prices import read_prices

END = datetime(2026, 6, 30, 16, 0, tzinfo=UTC)
START_DECISION = date(2022, 9, 1)
MIN_VALUE = 1e9
SIGN = {"rsi_14": -1, "bb_pctb_20": -1, "bb_width_20": -1, "macd_hist": -1, "stoch_k_14": -1, "adx_14": -1,
        "high52_prox": +1, "max_ret_20": -1, "skew_60": -1, "ret_1d": -1, "overnight_20": +1, "intraday_20": -1,
        "upper_shadow_20": -1, "lower_shadow_20": +1, "obv_flow_20": +1, "pv_corr_20": -1, "amihud_20": +1, "up_days_10": -1}
REGIMES = (("하락", date(2022, 9, 1), date(2022, 12, 31)), ("박스", date(2023, 1, 1), date(2024, 12, 31)),
           ("급등", date(2025, 1, 1), date(2026, 6, 30)))


def load(store: Store, end: datetime = END, start: date = date(2020, 9, 1)) -> dict[str, pd.DataFrame]:
    """보정 OHLC·원 거래대금 넓은 표. 진단은 END(6/30) 고정, 실전 신호(`tools/ta_composite.py`)는 지금 시각을 준다."""
    p = read_prices(store, as_of=end, until=end, lookback=(end.date() - start).days,
                    columns=["open", "high", "low", "close", "value"], adjusted=True, market="KR")
    p = p[p["entity_id"].str.match(r"^KR:\d{6}$")]
    p["day"] = pd.to_datetime(p["valid_from"]).dt.date
    out = {c: p.pivot_table(index="day", columns="entity_id", values=c, aggfunc="last").sort_index() for c in ("open", "high", "low", "close", "value")}
    idx, cols = out["close"].index, out["close"].columns
    return {k: v.reindex(index=idx, columns=cols) for k, v in out.items()}


def rmean(x: pd.DataFrame, n: int) -> pd.DataFrame:
    return x.rolling(n, min_periods=n).mean()


def features(d: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    o, h, lo, c, v = d["open"], d["high"], d["low"], d["close"], d["value"]
    r = c / c.shift(1) - 1.0
    f: dict[str, pd.DataFrame] = {}
    up, dn = r.clip(lower=0), (-r).clip(lower=0)
    rs = up.ewm(alpha=1 / 14, min_periods=14).mean() / dn.ewm(alpha=1 / 14, min_periods=14).mean()
    f["rsi_14"] = 100 - 100 / (1 + rs)
    ma20, sd20 = rmean(c, 20), c.rolling(20, min_periods=20).std()
    f["bb_pctb_20"] = (c - (ma20 - 2 * sd20)) / (4 * sd20)
    f["bb_width_20"] = 4 * sd20 / ma20
    macd = c.ewm(span=12, min_periods=26).mean() - c.ewm(span=26, min_periods=26).mean()
    f["macd_hist"] = (macd - macd.ewm(span=9, min_periods=9).mean()) / c
    hh, ll = h.rolling(14, min_periods=14).max(), lo.rolling(14, min_periods=14).min()
    f["stoch_k_14"] = (c - ll) / (hh - ll)
    tr = pd.concat([h - lo, (h - c.shift(1)).abs(), (lo - c.shift(1)).abs()]).groupby(level=0).max().reindex(c.index)
    upm, dnm = h - h.shift(1), lo.shift(1) - lo
    pdm = upm.where((upm > dnm) & (upm > 0), 0.0)
    ndm = dnm.where((dnm > upm) & (dnm > 0), 0.0)
    atr = tr.ewm(alpha=1 / 14, min_periods=14).mean()
    pdi, ndi = 100 * pdm.ewm(alpha=1 / 14, min_periods=14).mean() / atr, 100 * ndm.ewm(alpha=1 / 14, min_periods=14).mean() / atr
    f["adx_14"] = (100 * (pdi - ndi).abs() / (pdi + ndi)).ewm(alpha=1 / 14, min_periods=14).mean()
    f["high52_prox"] = c / h.rolling(252, min_periods=252).max()
    f["max_ret_20"] = r.rolling(20, min_periods=20).max()
    f["skew_60"] = r.rolling(60, min_periods=60).skew()
    f["ret_1d"] = r
    f["overnight_20"] = np.log(o / c.shift(1)).rolling(20, min_periods=20).sum()
    f["intraday_20"] = np.log(c / o).rolling(20, min_periods=20).sum()
    body_hi, body_lo = np.maximum(o, c), np.minimum(o, c)
    f["upper_shadow_20"] = rmean((h - body_hi) / c, 20)
    f["lower_shadow_20"] = rmean((body_lo - lo) / c, 20)
    f["obv_flow_20"] = (np.sign(r) * v).rolling(20, min_periods=20).sum() / v.rolling(20, min_periods=20).sum()
    lv = np.log(v.where(v > 0))
    mx, my = rmean(r, 20), rmean(lv, 20)
    cov = rmean(r * lv, 20) - mx * my
    f["pv_corr_20"] = cov / (r.rolling(20, min_periods=20).std(ddof=0) * lv.rolling(20, min_periods=20).std(ddof=0))
    f["amihud_20"] = rmean(r.abs() / (v / 1e8), 20)
    f["up_days_10"] = (r > 0).astype(float).where(r.notna()).rolling(10, min_periods=10).sum()
    # 통제(8월 측정분): reversal_5 · momentum_60 · −vol60 · log 거래대금20
    f["_rev5"] = c / c.shift(5) - 1.0
    f["_mom60"] = c / c.shift(60) - 1.0
    f["_lowvol"] = -r.ewm(span=60, min_periods=60).std()
    f["_liq"] = np.log(rmean(v, 20))
    return {k: x.replace([np.inf, -np.inf], np.nan) for k, x in f.items()}


def rowwise_ic(x: pd.DataFrame, y: pd.DataFrame) -> pd.Series:
    m = x.notna() & y.notna()
    xr, yr = x.where(m).rank(axis=1), y.where(m).rank(axis=1)
    xc, yc = xr.sub(xr.mean(axis=1), axis=0), yr.sub(yr.mean(axis=1), axis=0)
    ic = (xc * yc).sum(axis=1) / np.sqrt((xc ** 2).sum(axis=1) * (yc ** 2).sum(axis=1))
    return ic[m.sum(axis=1) >= 100].dropna()


def decile_excess(x: pd.DataFrame, y: pd.DataFrame) -> tuple[float, float]:
    m = x.notna() & y.notna()
    pr = x.where(m).rank(axis=1, pct=True)
    yy = y.where(m)
    mean = yy.mean(axis=1)
    top = yy.where(pr > 0.9).mean(axis=1) - mean
    bot = yy.where(pr <= 0.1).mean(axis=1) - mean
    return float(top.mean()), float(bot.mean())


def residualize(x: pd.DataFrame, controls: list[pd.DataFrame]) -> pd.DataFrame:
    out = pd.DataFrame(np.nan, index=x.index, columns=x.columns)
    for day in x.index:
        cols = [x.loc[day]] + [c.loc[day] for c in controls]
        frame = pd.concat(cols, axis=1).dropna()
        if len(frame) < 100:
            continue
        z = (frame - frame.mean()) / frame.std().replace(0, np.nan)
        z = z.dropna(axis=1)
        if z.shape[1] < 2 or z.columns[0] != frame.columns[0]:
            continue
        a = np.column_stack([np.ones(len(z)), z.iloc[:, 1:].to_numpy()])
        beta, *_ = np.linalg.lstsq(a, z.iloc[:, 0].to_numpy(), rcond=None)
        out.loc[day, z.index] = z.iloc[:, 0].to_numpy() - a @ beta
    return out


def bh(pvals: dict[str, float], q: float = 0.10) -> set[str]:
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    k = 0
    for i, (_, p) in enumerate(items, start=1):
        if p <= q * i / len(items):
            k = i
    return {name for name, _ in items[:k]}


def main() -> int:
    store = Store(root=Path("data"))
    d = load(store)
    c, v = d["close"], d["value"]
    label = (c.shift(-6) / c.shift(-1) - 1.0)
    label = label.where(label.abs() <= 0.5)
    uni = (rmean(v, 20) >= MIN_VALUE) & (c.notna().rolling(252, min_periods=1).sum() >= 252)
    days = [x for x in c.index if x >= START_DECISION]
    label = label.where(uni).loc[days]
    label = label[label.notna().sum(axis=1) >= 100]
    days = list(label.index)
    print(f"패널 {c.index[0]}~{c.index[-1]} · {c.shape[1]}종목 · 결정일 {days[0]}~{days[-1]} {len(days)}일 · 평균 우주 {label.notna().sum(axis=1).mean():.0f}")
    f = {k: x.where(uni).loc[days] for k, x in features(d).items()}
    controls = [f["_rev5"], f["_mom60"], f["_lowvol"], f["_liq"]]
    rows, pvals = [], {}
    for name, sign in SIGN.items():
        ic = rowwise_ic(f[name], label)
        t = float(ic_module.newey_west_t(ic, lag=4))
        pvals[name] = math.erfc(abs(t) / math.sqrt(2))
        reg = {rn: float(ic[(ic.index >= a) & (ic.index <= b)].mean()) for rn, a, b in REGIMES}
        top, bot = decile_excess(f[name], label)
        corr = {cn: float(rowwise_ic(f[name], cv).mean()) for cn, cv in zip(("rev5", "mom60", "lowvol", "liq"), controls, strict=True)}
        rows.append({"name": name, "sign": sign, "ic": float(ic.mean()), "t": t, **reg, "top": top, "bot": bot, **{f"ρ_{k}": x for k, x in corr.items()}})
    passed = bh(pvals)
    cand = [r["name"] for r in rows if np.sign(r["ic"]) == r["sign"] and abs(r["t"]) >= 2 and r["name"] in passed]
    for r in rows:
        r["후보"] = r["name"] in cand
        if r["후보"]:
            pic = rowwise_ic(residualize(f[r["name"]], controls), label)
            r["편IC"], r["편t"] = float(pic.mean()), float(ic_module.newey_west_t(pic, lag=4))
    out = pd.DataFrame(rows).set_index("name")
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 30)
    print(out.round(4).to_string())
    print(f"\nBH 10% 통과: {sorted(passed)}\n채택 후보(부호 일치·|t|≥2·BH): {cand}")
    out.to_csv("logs/diag-ta-features-20261010.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
