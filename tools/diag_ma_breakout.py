"""진단 — 장기이평선 돌파 매매법(docs/diag/ma-breakout-2026-10-10.md 의 고정 규칙 그대로). 6/30 까지만 읽는다.

    QUANT_RL_DUCKDB_MEMORY_LIMIT=900MB .venv/bin/python tools/diag_ma_breakout.py
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from quant_rl_trading.analysts import ic as ic_module
from quant_rl_trading.store import Store
from quant_rl_trading.store.prices import read_prices

END = datetime(2026, 6, 30, 16, 0, tzinfo=UTC)          # 금고 창(7/1~) 을 열지 않는다
COST = 0.0082                                           # 왕복
MIN_VALUE = 1e9
GAP = 60
HORIZONS = (5, 20, 60)
REGIMES = (("하락 2021-08~2022-12", "2021-08-01", "2022-12-31"), ("박스 2023~2024", "2023-01-01", "2024-12-31"),
           ("급등 2025-01~2026-06", "2025-01-01", "2026-06-30"))


def load(store: Store) -> tuple[pd.DataFrame, pd.DataFrame]:
    p = read_prices(store, as_of=END, until=END, lookback=(END.date() - datetime(2020, 9, 1).date()).days,
                    columns=["close", "value"], adjusted=True, market="KR")
    p["day"] = pd.to_datetime(p["valid_from"]).dt.date
    p = p[p["entity_id"].str.match(r"^KR:\d{6}$")]
    close = p.pivot_table(index="day", columns="entity_id", values="close", aggfunc="last").sort_index()
    value = p.pivot_table(index="day", columns="entity_id", values="value", aggfunc="last").sort_index().reindex_like(close)
    return close, value


def events(close: pd.DataFrame, value: pd.DataFrame, ma_n: int, *, conditions: bool, confirm: bool) -> list[tuple[int, str]]:
    ma = close.rolling(ma_n, min_periods=ma_n).mean()
    cross = (close > ma) & (close.shift(1) <= ma.shift(1))
    ok = cross & (close.notna().rolling(ma_n + 20, min_periods=1).sum() >= ma_n + 20)
    if conditions:
        vol = value >= 2.0 * value.shift(1).rolling(20, min_periods=20).mean()
        flat = (ma / ma.shift(20) - 1.0).abs() <= 0.02
        ok &= vol & flat
    liq = value.rolling(20, min_periods=20).mean() >= MIN_VALUE
    ok &= liq
    above = close > ma
    out, last = [], {}
    arr = ok.to_numpy()
    cols = list(close.columns)
    for i, j in zip(*np.nonzero(arr), strict=True):
        e = cols[j]
        if i - last.get(e, -10**9) < GAP:
            continue
        if confirm:
            if i + 3 >= len(close) or not above.iloc[i + 1:i + 4, j].all():
                continue
            entry = i + 3
        else:
            entry = i + 1
        last[e] = i
        out.append((entry, e))
    return out


def outcomes(close: pd.DataFrame, value: pd.DataFrame, evs: list[tuple[int, str]]) -> pd.DataFrame:
    liq = (value.rolling(20, min_periods=20).mean() >= MIN_VALUE)
    rows = []
    for entry, e in evs:
        for h in HORIZONS:
            if entry + h >= len(close):
                continue
            r = close[e].iloc[entry + h] / close[e].iloc[entry] - 1.0
            uni = liq.iloc[entry]
            ew = (close.iloc[entry + h] / close.iloc[entry] - 1.0)[uni[uni].index].clip(-0.9, 5.0).mean()
            if np.isfinite(r):
                rows.append({"day": close.index[entry], "entity": e, "h": h, "ex": r - ew})
    return pd.DataFrame(rows)


def report(name: str, f: pd.DataFrame) -> None:
    print(f"\n== {name}: 신호 {f[f['h'] == 20]['entity'].count()}건")
    for h in HORIZONS:
        g = f[f["h"] == h]
        if g.empty:
            continue
        daily = g.groupby("day")["ex"].mean()
        t = float(ic_module.newey_west_t(daily, lag=max(h - 1, 1))) if len(daily) > 10 else float("nan")
        parts = []
        for rname, a, b in REGIMES:
            m = g[(g["day"] >= pd.Timestamp(a).date()) & (g["day"] <= pd.Timestamp(b).date())]["ex"]
            parts.append(f"{rname.split()[0]} {m.mean():+.2%}({len(m)})" if len(m) else f"{rname.split()[0]} -")
        print(f"  {h:>2}세션: 초과 {g['ex'].mean():+.2%} · 비용 후 {g['ex'].mean() - COST:+.2%} · 적중 {(g['ex'] > 0).mean():.0%} · "
              f"NW t {t:+.2f} · " + " · ".join(parts))


def main() -> int:
    store = Store(root=Path("data"))
    close, value = load(store)
    print(f"패널 {close.index[0]}~{close.index[-1]} · {close.shape[1]}종목")
    for ma_n in (120, 200):
        tag = "주" if ma_n == 120 else "기록"
        report(f"[{tag}] {ma_n}일 · 조건+3세션 확인(t+3 진입)", outcomes(close, value, events(close, value, ma_n, conditions=True, confirm=True)))
        report(f"[{tag}] {ma_n}일 · 조건, 확인 없음(t+1 진입)", outcomes(close, value, events(close, value, ma_n, conditions=True, confirm=False)))
        report(f"[대조] {ma_n}일 · 교차만(t+1 진입)", outcomes(close, value, events(close, value, ma_n, conditions=False, confirm=False)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
