"""가능성 테스트 — 합성 TA 점수로 포트 넷(docs/diag/ta-literature-round2-2026-10-10.md '가능성 테스트' 의 고정 규칙). 탐색 창 상한 추정.

    .venv/bin/python tools/diag_ta_possibility.py      # 입력: data/_diag/ta-round2/signals.pkl (diag_ta_round2 산출)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from tools import diag_ta_features as T

CACHE = Path("data/_diag/ta-round2/signals.pkl")
COST = 0.0041
EVERY = 5
ALL9 = {"shadow_asym_20": -1, "pv_corr_20": -1, "skew_60": -1, "mom_12_1": +1, "fip": +1, "trend_factor": +1,
        "vol_cv_60": -1, "hs_season": +1, "log_price": +1}
K6 = {k: ALL9[k] for k in ("shadow_asym_20", "pv_corr_20", "skew_60", "mom_12_1", "fip", "vol_cv_60")}


def composite(sig: dict[str, pd.DataFrame], spec: dict[str, int], mask: pd.DataFrame) -> pd.DataFrame:
    parts = [(sig[k].where(mask) * s).rank(axis=1, pct=True) for k, s in spec.items()]
    stack = np.nanmean(np.stack([p.to_numpy() for p in parts]), axis=0)
    out = pd.DataFrame(stack, index=parts[0].index, columns=parts[0].columns)
    return out.where(mask)


def book(weights: dict, ret: pd.DataFrame) -> tuple[pd.Series, float]:
    """결정일 d 의 비중 → d+2 부터의 일수익(d+1 종가 진입). 드리프트 무시, 재조정일에 |Δw| × 편도 비용."""
    days = list(ret.index)
    pos = {d: i for i, d in enumerate(days)}
    out = pd.Series(0.0, index=days)
    prev = pd.Series(dtype=float)
    turns = []
    keys = sorted(weights)
    for j, d in enumerate(keys):
        w = weights[d]
        i0 = pos[d] + 2
        i1 = pos[keys[j + 1]] + 2 if j + 1 < len(keys) else len(days)
        if i0 >= len(days):
            break
        seg = ret.iloc[i0:i1].reindex(columns=w.index).fillna(0.0)
        out.iloc[i0:i1] = seg.to_numpy() @ w.to_numpy()
        turn = float((w.reindex(w.index.union(prev.index), fill_value=0) - prev.reindex(w.index.union(prev.index), fill_value=0)).abs().sum())
        out.iloc[i0] -= turn * COST
        turns.append(turn / 2)
        prev = w
    return out, float(np.mean(turns) * 252 / EVERY)


def stats(name: str, p: pd.Series, b: pd.Series, turn: float) -> dict[str, object]:
    ex = (p - b).dropna()
    ex = ex[ex.index >= T.START_DECISION]
    ann, ir = ex.mean() * 252, ex.mean() / ex.std() * np.sqrt(252)
    reg = {rn: float(ex[(ex.index >= a) & (ex.index <= z)].mean() * 252) for rn, a, z in T.REGIMES}
    blocks = ex.groupby(np.arange(len(ex)) // 20).sum()
    return {"포트": name, "연초과": ann, "IR": ir, "연회전": turn, **reg, "이긴20세션": float((blocks > 0).mean())}


def main() -> int:
    z = pd.read_pickle(CACHE)  # invariant-allow: data-access — 진단 작업 캐시
    sig, label, k200, close, uni = z["signals"], z["label"], z["k200"], z["close"], z["uni"]
    days = list(label.index)
    ret = (close / close.shift(1) - 1.0).clip(-0.5, 1.0)
    ret = ret.loc[days[0]:]
    uni = uni.reindex(index=days, columns=close.columns).fillna(False)
    k200 = k200.reindex(index=days, columns=close.columns).fillna(False)
    comp_all, comp_k = composite(sig, ALL9, uni), composite(sig, K6, k200 & uni)
    reb = days[::EVERY]
    ew = lambda names: pd.Series(1.0 / len(names), index=list(names)) if len(names) else pd.Series(dtype=float)  # noqa: E731
    W = {k: {} for k in ("uni", "A", "B", "k200", "C", "D")}
    for d in reb:
        u = uni.loc[d]
        un = list(u[u].index)
        ca = comp_all.loc[d].dropna()
        W["uni"][d] = ew(un)
        W["A"][d] = ew([x for x in un if x not in set(ca.nsmallest(int(len(ca) * 0.2)).index)])
        W["B"][d] = ew(list(ca.nlargest(24).index))
        kk = k200.loc[d] & u
        kn = list(kk[kk].index)
        ck = comp_k.loc[d].dropna()
        W["k200"][d] = ew(kn)
        W["C"][d] = ew([x for x in kn if x not in set(ck.nsmallest(int(len(ck) * 0.2)).index)])
        W["D"][d] = ew(list(ck.nlargest(30).index))
    R = {k: book(v, ret) for k, v in W.items()}
    rows = [stats("A 우주 − 하위20%", R["A"][0], R["uni"][0], R["A"][1]),
            stats("B 우주 상위24", R["B"][0], R["uni"][0], R["B"][1]),
            stats("C K200 − 하위20%", R["C"][0], R["k200"][0], R["C"][1]),
            stats("D K200 상위30", R["D"][0], R["k200"][0], R["D"][1])]
    out = pd.DataFrame(rows).set_index("포트")
    pd.set_option("display.width", 200)
    print(out.round(3).to_string())
    for k in ("uni", "k200"):
        s = R[k][0][R[k][0].index >= T.START_DECISION]
        print(f"기준 {k} 연수익 {s.mean() * 252:+.1%}")
    out.to_csv("logs/diag-ta-possibility-20261010.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
