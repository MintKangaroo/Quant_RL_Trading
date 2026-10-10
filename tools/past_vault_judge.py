"""과거 금고 판정 — docs/protocols/past-vault-2026-10.md(해시 고정)의 기준 그대로. TF · TB · TG.

    .venv/bin/python tools/past_vault_judge.py                 # 2026-10-13 16:45 KST 뒤에만, 해시가 맞을 때만 봉인 창고를 연다
    .venv/bin/python tools/past_vault_judge.py --rehearse      # 리허설: 본 창고 탐색 구간(2022-09~2026-06)·TG 만 — 배관 확인용, 판정 아님

입력: 봉인 창고 prices·market_stats, 굽기 `data/_past_vault/_bake/{ta,ttm}.pkl`(tools/past_vault_bake.py).
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import UTC, date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from quant_rl_trading.analysts import ic as ic_module
from quant_rl_trading.replay.clock import LiveClock
from quant_rl_trading.store import Store
from quant_rl_trading.store.prices import read_prices
from tools import diag_ta_possibility as P
from tools import vault_judge as vj

PROTOCOL = Path("docs/protocols/past-vault-2026-10.md")
PROTOCOL_HASH = "3f1d10dbc30d2ee8"
OPEN_AT = datetime(2026, 10, 13, 16, 45, tzinfo=ZoneInfo("Asia/Seoul"))
ROOT = Path("data/_past_vault")
BAKE = ROOT / "_bake"
FIRST, LAST = date(2011, 1, 3), date(2021, 7, 30)
PERIODS = (("A 2011~2014", date(2011, 1, 1), date(2014, 12, 31)), ("B 2015~2018", date(2015, 1, 1), date(2018, 12, 31)),
           ("C 2019~2021-07", date(2019, 1, 1), date(2021, 7, 31)))
MIN_VALUE = 1e9
COST = 0.0041
T_GATE, IR_GATE = -3.0, 0.5
TOP_CAP, CAP_LIMIT = 200, 0.30
TRIALS = {"TF": "ttm", "TB": "ttm", "TG": "ta"}


def capped(w: pd.Series, limit: float) -> pd.Series:
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


def load_market(store: Store, end: date, start: date) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    as_of = datetime.combine(end, time(23, 59), tzinfo=UTC)
    p = read_prices(store, as_of=as_of, until=as_of, lookback=(end - start).days, columns=["close", "value"], adjusted=True, market="KR")
    p = p[p["entity_id"].str.match(r"^KR:\d{6}$")]
    p["day"] = pd.to_datetime(p["valid_from"]).dt.date
    close = p.pivot_table(index="day", columns="entity_id", values="close", aggfunc="last").sort_index()
    value = p.pivot_table(index="day", columns="entity_id", values="value", aggfunc="last").sort_index().reindex_like(close)
    ms = store.get("market_stats", as_of=as_of, lookback=(end - start).days, market="KR", columns=["entity_id", "valid_from", "metric", "value"])
    ms = ms[ms["metric"].astype(str).str.contains("market_cap")]
    ms["day"] = pd.to_datetime(ms["valid_from"]).dt.date
    mcap = ms.pivot_table(index="day", columns="entity_id", values="value", aggfunc="last").reindex(index=close.index, columns=close.columns)
    return close, value, mcap


def judge_one(kind: str, scores: pd.DataFrame, close: pd.DataFrame, value: pd.DataFrame, mcap: pd.DataFrame,
              first: date, last: date, periods: tuple) -> tuple[list[str], str, dict]:
    uni = (value.rolling(20, min_periods=20).mean() >= MIN_VALUE) & (close.notna().rolling(252, min_periods=1).sum() >= 252)
    # 점수 대상 = 그 점수 날의 대상(등록 '대상') — TTM 은 대상 밖 r̂ 를 지운다.
    sc = scores.copy()
    sc["ok"] = [bool(uni.at[d, e]) if (d in uni.index and e in uni.columns) else False for d, e in zip(sc["session"], sc["entity_id"], strict=True)]
    sc = sc[sc["ok"]]
    days = [d for d in close.index if first <= d <= last]
    lagged = vj.tsfm_lagged(sc[["session", "entity_id", "r_hat"]], days)
    excl = vj.tf_exclusions(kind if kind in vj.TF_RULES else "TF", lagged)
    ret_fwd = (close.shift(-2) / close.shift(-1) - 1.0)
    ret_fwd = ret_fwd.where(ret_fwd.abs() <= 0.5)
    trad = {d: set(uni.columns[uni.loc[d].to_numpy()]) for d in days}
    mech = vj.excluded_excess(excl, ret_fwd, trad)
    mt = float(ic_module.newey_west_t(mech, lag=4))
    mper = {n: float(mech[(mech.index >= a) & (mech.index <= b)].mean()) for n, a, b in periods}
    daily = (close / close.shift(1) - 1.0).clip(-0.5, 1.0).loc[days[0]:]
    reb = days[::5]
    ew = lambda n: pd.Series(1.0 / len(n), index=list(n)) if len(n) else pd.Series(dtype=float)  # noqa: E731
    W = {k: {} for k in ("base", "treat", "big", "big_t")}
    for d in reb:
        names = sorted(trad[d])
        cut = excl.get(d, set())
        W["base"][d], W["treat"][d] = ew(names), ew([x for x in names if x not in cut])
        cap = mcap.loc[d].dropna() if d in mcap.index else pd.Series(dtype=float)
        top = cap.nlargest(TOP_CAP)
        W["big"][d] = capped(top.copy(), CAP_LIMIT) if len(top) else pd.Series(dtype=float)
        keep = top[~top.index.isin(cut)]
        W["big_t"][d] = capped(keep.copy(), CAP_LIMIT) if len(keep) else pd.Series(dtype=float)
    R = {k: P.book(v, daily) for k, v in W.items()}

    def ex(a: str, b: str) -> tuple[float, float, dict]:
        e = (R[a][0] - R[b][0]).dropna()
        e = e[(e.index >= first) & (e.index <= last)]
        per = {n: float(e[(e.index >= x) & (e.index <= y)].mean() * 252) for n, x, y in periods}
        return float(e.mean() * 252), float(e.mean() / e.std() * np.sqrt(252)), per
    b_ann, b_ir, b_per = ex("treat", "base")
    g_ann, g_ir, g_per = ex("big_t", "big")
    c = (mech.mean() < 0 and mt <= T_GATE and all(v < 0 for v in mper.values()),
         b_ann > 0 and b_ir >= IR_GATE and all(v > 0 for v in b_per.values()),
         g_ann >= 0)
    mk = vj.mark
    lines = [f"{kind}: ①원리 뺀 종목 5세션 초과 {mech.mean():+.2%}(NW t {mt:+.2f}, {len(mech)}세션) · 구간 "
             + " / ".join(f"{k.split()[0]} {v:+.2%}" for k, v in mper.items()) + f" {mk(c[0])}",
             f"    ②넓은 포트 연 {b_ann:+.1%}p · IR {b_ir:+.2f} · 구간 " + " / ".join(f"{k.split()[0]} {v:+.1%}" for k, v in b_per.items())
             + f" · 회전 {R['treat'][1]:.1f} {mk(c[1])}",
             f"    ③대형주 200 시총가중 연 {g_ann:+.1%}p · IR {g_ir:+.2f} · 구간 " + " / ".join(f"{k.split()[0]} {v:+.1%}" for k, v in g_per.items()) + f" {mk(c[2])}"]
    verdict = f"과거 금고 통과(원리 t {mt:+.2f})" if all(c) else "기각"
    return lines, verdict, {"t": mt, "pass": all(c)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rehearse", action="store_true")
    args = ap.parse_args(argv)
    got = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16]
    if got != PROTOCOL_HASH:
        print(f"등록 문서 해시 {got} ≠ 고정 {PROTOCOL_HASH} — 열지 않는다", file=sys.stderr)
        return 2
    if args.rehearse:
        from tools.ta_composite import composite_panel
        store = Store(root=Path("data"))
        comp, _ = composite_panel(store, end=datetime(2026, 6, 30, 16, tzinfo=UTC))
        long = comp.stack().rename("r_hat").reset_index()
        long.columns = ["session", "entity_id", "r_hat"]
        close, value, mcap = load_market(store, date(2026, 6, 30), date(2020, 9, 1))
        per = (("박스 2022-09~2024", date(2022, 9, 1), date(2024, 12, 31)), ("급등 2025~2026-06", date(2025, 1, 1), date(2026, 6, 30)))
        lines, verdict, _ = judge_one("TG", long, close, value, mcap, date(2022, 9, 1), date(2026, 6, 19), per)
        print("[리허설 — 판정 아님, 본 창고 탐색 구간]")
        print("\n".join(lines))
        print(f"  → (리허설) {verdict}")
        return 0
    now = LiveClock().now()
    if now < OPEN_AT:
        print(f"개봉 시각({OPEN_AT:%F %H:%M} KST) 전 — 열지 않는다", file=sys.stderr)
        return 2
    store = Store(root=ROOT)
    close, value, mcap = load_market(store, date(2021, 8, 10), date(2009, 12, 1))
    results = {}
    for kind, src in TRIALS.items():
        f = BAKE / f"{src}.pkl"
        if not f.exists():
            print(f"{f} 없음 — tools/past_vault_bake.py --what {src} 가 먼저다", file=sys.stderr)
            return 3
        lines, verdict, info = judge_one(kind, pd.read_pickle(f), close, value, mcap, FIRST, LAST, PERIODS)  # invariant-allow: data-access — 봉인 창고 굽기
        results[kind] = info
        print("\n".join(lines))
        print(f"  → {kind}: {verdict}", flush=True)
    passed = [k for k, v in results.items() if v["pass"]]
    if passed:
        best = min(passed, key=lambda k: results[k]["t"])
        print(f"\n통과 {passed} · 원리 t 가 가장 강한 것: {best} (10/13 쓰임은 등록 문서 그대로 — early 결과와 함께 리드가 보고)")
    else:
        print("\n셋 다 기각")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
