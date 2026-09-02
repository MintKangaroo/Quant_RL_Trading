"""베타 제거·변동성 타기팅 오버레이 — docs/protocols/beta-overlay-2026-09.md 대로 잰다.

    .venv/bin/python tools/trial_overlay.py
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

from quant_rl_trading.store import Store  # noqa: E402
from quant_rl_trading.store.prices import read_prices  # noqa: E402

PROTOCOL = Path("docs/protocols/beta-overlay-2026-09.md")
CACHE = Path("data/_diag")
MARKET = "KR"
INDEX = "KR:IDX:KOSPI200"
HOLDOUT_START = date(2026, 7, 1)
TOP_N = 24
ONE_WAY_COST = 0.0041
MAX_MOVE = 0.5
WARMUP = 60
BETA_WINDOW = 60
VOL_SPAN = 20
VOL_TARGET = 0.15
HEDGE_CARRY = 0.01
HEDGE_TRADE_COST = 0.001
ANN = 252


def _pkl(name: str) -> pd.DataFrame:
    frame = pd.read_pickle(CACHE / f"{name}-{MARKET}.pkl")
    frame["session"] = pd.to_datetime(frame["session"]).dt.date
    return frame


def _prices(store: Store, sessions: list[date]) -> pd.DataFrame:
    now = datetime.combine(sessions[-1], time(16, 0), tzinfo=UTC)
    span = (sessions[-1] - sessions[0]).days + 40
    prices = read_prices(store, as_of=now, lookback=span, columns=["close"], adjusted=True, market=MARKET)
    prices["day"] = pd.to_datetime(prices["valid_from"]).dt.date
    return prices.pivot_table(index="day", columns="entity_id", values="close", aggfunc="last").sort_index()


def _index(store: Store, sessions: list[date]) -> pd.Series:
    now = datetime.combine(sessions[-1], time(16, 0), tzinfo=UTC)
    span = (sessions[-1] - sessions[0]).days + 40
    frame = store.get("indices", as_of=now, lookback=span, market=MARKET, columns=["entity_id", "valid_from", "close"])
    frame = frame[frame["entity_id"] == INDEX]
    frame["day"] = pd.to_datetime(frame["valid_from"]).dt.date
    return frame.groupby("day")["close"].last().sort_index()


def proxy_returns(store: Store, sessions: list[date], risk_floor: float) -> tuple[pd.Series, pd.Series]:
    """대리 전략의 일수익(비용 후)과 같은 날 지수 수익. 인덱스 = 결정 세션 t (보유는 t+1→t+2)."""
    fund = _pkl("scores-fundamental")
    risk = _pkl("scores-risk")
    trad = _pkl("tradable")
    wide = _prices(store, sessions)
    ret = (wide.shift(-2) / wide.shift(-1) - 1.0)
    ret = ret.where(ret.abs() <= MAX_MOVE)
    idx = _index(store, sessions)
    idx_ret = (idx.shift(-2) / idx.shift(-1) - 1.0)

    prev: pd.Series | None = None
    strat, bench = {}, {}
    for day in sessions:
        f = fund[fund["session"] == day].set_index("entity_id")["score"]
        r = risk[risk["session"] == day].set_index("entity_id")["score"]
        ok = set(trad[trad["session"] == day]["entity_id"])
        f = f[f.index.isin(ok) & f.index.isin(r.index)]
        if f.empty or day not in ret.index:
            continue
        r = r.reindex(f.index)
        keep = r[r >= r.quantile(risk_floor)].index
        top = f.loc[keep].nlargest(TOP_N)
        if top.empty:
            continue
        w = pd.Series(1.0 / len(top), index=top.index)
        day_ret = ret.loc[day].reindex(w.index).fillna(0.0)
        turnover = (w.subtract(prev, fill_value=0.0)).abs().sum() if prev is not None else w.sum()
        strat[day] = float((w * day_ret).sum() - ONE_WAY_COST * turnover)
        bench[day] = float(idx_ret.get(day, np.nan))
        # 다음 날 비교용 드리프트
        prev = w * (1.0 + day_ret)
        prev = prev / prev.sum() if prev.sum() > 0 else w
    s = pd.Series(strat).sort_index()
    b = pd.Series(bench).sort_index().reindex(s.index)
    return s, b


def overlays(s: pd.Series, b: pd.Series) -> dict[str, pd.Series]:
    """오버레이 적용. 모든 추정치는 직전 세션까지의 정보만 쓴다."""
    out = {"BASE": s.copy()}
    # 롤링 베타 (t−1 까지)
    cov = s.rolling(BETA_WINDOW).cov(b).shift(1)
    var = b.rolling(BETA_WINDOW).var().shift(1)
    beta = (cov / var).fillna(0.0)
    for name, h in (("H50", 0.5), ("H100", 1.0)):
        ratio = (h * beta).clip(lower=0.0)
        hedged = s - ratio * b.fillna(0.0) - HEDGE_CARRY / ANN * ratio - HEDGE_TRADE_COST * ratio.diff().abs().fillna(0.0)
        out[name] = hedged
    # 변동성 타기팅 (t−1 까지의 EWMA)
    sigma = s.ewm(span=VOL_SPAN).std().shift(1) * np.sqrt(ANN)
    scale = (VOL_TARGET / sigma).clip(upper=1.0).fillna(1.0)
    out["V15"] = s * scale
    sigma_h = out["H50"].ewm(span=VOL_SPAN).std().shift(1) * np.sqrt(ANN)
    scale_h = (VOL_TARGET / sigma_h).clip(upper=1.0).fillna(1.0)
    out["HV"] = out["H50"] * scale_h
    return out


def metrics(r: pd.Series, b: pd.Series) -> dict[str, float]:
    r = r.dropna()
    b = b.reindex(r.index).fillna(0.0)
    nav = (1.0 + r).cumprod()
    mdd = float((nav / nav.cummax() - 1.0).min())
    vol = float(r.std() * np.sqrt(ANN))
    ann = float(r.mean() * ANN)
    ex = r - b
    te = float(ex.std() * np.sqrt(ANN))
    beta = float(np.cov(r, b)[0, 1] / b.var()) if b.var() > 0 else float("nan")
    return {
        "ann": ann, "vol": vol, "sharpe": ann / vol if vol > 0 else float("nan"), "mdd": mdd,
        "beta": beta, "ir": float(ex.mean() * ANN / te) if te > 0 else float("nan"), "n": int(r.size),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="data")
    args = parser.parse_args(argv)
    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16]
    print(f"=== 베타 제거·변동성 타기팅 — {PROTOCOL} (해시 {digest}) ===")
    store = Store(root=Path(args.root))
    cal = _pkl("calendar")
    sessions = sorted(d for d in cal["session"] if d < HOLDOUT_START)
    probe = datetime.combine(sessions[-1], time(16, 0), tzinfo=UTC)
    risk_floor = float(store.config("selector.risk_floor_percentile", as_of=probe))
    print(f"세션 {len(sessions)}개 {sessions[0]}~{sessions[-1]} · risk 하한 컷 {risk_floor:.0%} · 상위 {TOP_N}")

    s, b = proxy_returns(store, sessions, risk_floor)
    variants = overlays(s, b)
    judge_idx = s.index[WARMUP:]
    print(f"판정 창 {judge_idx[0]}~{judge_idx[-1]} ({len(judge_idx)}세션, 워밍업 {WARMUP} 제외)")
    half = len(judge_idx) // 2
    halves = (judge_idx[:half], judge_idx[half:])

    rows = {}
    for name, r in variants.items():
        m = metrics(r.loc[judge_idx], b)
        h1 = metrics(r.loc[halves[0]], b)["sharpe"]
        h2 = metrics(r.loc[halves[1]], b)["sharpe"]
        rows[name] = {**m, "sharpe_h1": h1, "sharpe_h2": h2}
    df = pd.DataFrame(rows).T
    bench = metrics(b.loc[judge_idx], b)
    print(f"KOSPI200: 연 {bench['ann']:+.1%} · 변동성 {bench['vol']:.1%} · Sharpe {bench['sharpe']:+.2f} · MDD {bench['mdd']:+.1%}")
    print()
    print(f"{'변형':6} {'연수익':>8} {'변동성':>7} {'Sharpe':>7} {'MDD':>8} {'β':>6} {'IR':>6} {'Sh전반':>7} {'Sh후반':>7}")
    for name, m in df.iterrows():
        print(f"{name:6} {m['ann']:+8.1%} {m['vol']:7.1%} {m['sharpe']:+7.2f} {m['mdd']:+8.1%} {m['beta']:6.2f} {m['ir']:+6.2f} {m['sharpe_h1']:+7.2f} {m['sharpe_h2']:+7.2f}")
    base = df.loc["BASE"]
    print()
    for name, m in df.iterrows():
        if name == "BASE":
            continue
        c1 = m["sharpe"] >= base["sharpe"] + 0.2
        c2 = m["mdd"] >= base["mdd"] * 0.8
        c3 = m["sharpe_h1"] >= base["sharpe_h1"] and m["sharpe_h2"] >= base["sharpe_h2"]
        verdict = "파일럿 후보" if (c1 and c2 and c3) else "기록만"
        print(f"{name:6} ① Sharpe+0.2 {'○' if c1 else '×'}  ② MDD −20% {'○' if c2 else '×'}  ③ 전·후반 {'○' if c3 else '×'}  → {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
