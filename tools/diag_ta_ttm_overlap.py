"""겹침 진단(기록) — TA 합성 하위 20% 빼기 대 TTM 하위 20% 빼기, 2025-03~2026-06 TTM 매일 예측이 있는 유동 ~300 안에서.

TA 합성 = diag_ta_possibility.ALL9 를 그 300 안 백분위로. TTM = 하루 늦춤(d 결정에 d−1 r̂). 결정 d → d+1 종가 진입, 5세션 재조정, 편도 0.41%.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from quant_rl_trading.analysts import ic as ic_module
from tools import diag_ta_features as T
from tools import diag_ta_possibility as P


def main() -> int:
    z = pd.read_pickle(P.CACHE)  # invariant-allow: data-access — 진단 작업 캐시
    sig, label, close = z["signals"], z["label"], z["close"]
    keys = pd.read_pickle("data/_diag/tsfm-daily/inputs/keys-KR-D.pkl")  # invariant-allow: data-access — 진단 작업 캐시
    pr = np.load("data/_diag/tsfm-daily/preds/ttm.npz")["price"]
    t = keys[["session", "entity_id"]].copy()
    t["r"] = pr[:, -1] / pr[:, 0] - 1.0
    ttm = t.pivot_table(index="session", columns="entity_id", values="r").sort_index()
    ttm_lag = ttm.shift(1)                                         # d 결정 = d−1 밤 예측
    days = [d for d in ttm.index[1:] if d in label.index]
    cols = ttm.columns
    mask = ttm_lag.reindex(index=days, columns=cols).notna()
    ta = P.composite({k: v.reindex(index=days, columns=cols) for k, v in sig.items()}, P.ALL9, mask)
    tt = ttm_lag.reindex(index=days, columns=cols).where(mask)
    lab = label.reindex(index=days, columns=cols)
    print(f"창 {days[0]}~{days[-1]} {len(days)}세션 · 평균 {mask.sum(axis=1).mean():.0f}종목")
    print(f"TA 합성 대 TTM 횡단면 순위상관 평균 {T.rowwise_ic(ta, tt).mean():+.3f}")
    for nm, x, ctrl in (("TA 합성", ta, tt), ("TTM", tt, ta)):
        ic = T.rowwise_ic(x, lab)
        pic = T.rowwise_ic(T.residualize(x, [ctrl]), lab)
        print(f"{nm:<8} IC {ic.mean():+.4f} (t {ic_module.newey_west_t(ic, lag=4):+.2f}) · 상대 통제 편 IC {pic.mean():+.4f} (t {ic_module.newey_west_t(pic, lag=4):+.2f})")
    ret = (close / close.shift(1) - 1.0).clip(-0.5, 1.0).loc[days[0]:].reindex(columns=cols)
    reb = days[::P.EVERY]
    ew = lambda n: pd.Series(1.0 / len(n), index=list(n)) if len(n) else pd.Series(dtype=float)  # noqa: E731
    both = (ta.rank(axis=1, pct=True) + tt.rank(axis=1, pct=True)) / 2
    W = {k: {} for k in ("base", "TA", "TTM", "합집합", "평균순위")}
    jac = []
    for d in reb:
        names = list(mask.loc[d][mask.loc[d]].index)
        a, b, c2 = ta.loc[d].dropna(), tt.loc[d].dropna(), both.loc[d].dropna()
        ca, cb = set(a.nsmallest(int(len(a) * 0.2)).index), set(b.nsmallest(int(len(b) * 0.2)).index)
        cc = set(c2.nsmallest(int(len(c2) * 0.2)).index)
        jac.append(len(ca & cb) / max(len(ca | cb), 1))
        W["base"][d] = ew(names)
        W["TA"][d] = ew([x for x in names if x not in ca])
        W["TTM"][d] = ew([x for x in names if x not in cb])
        W["합집합"][d] = ew([x for x in names if x not in ca | cb])
        W["평균순위"][d] = ew([x for x in names if x not in cc])
    print(f"하위 20% 겹침(자카드) 평균 {np.mean(jac):.2f}")
    R = {k: P.book(v, ret) for k, v in W.items()}
    for k in ("TA", "TTM", "합집합", "평균순위"):
        ex = (R[k][0] - R["base"][0]).dropna()
        print(f"  유동300 − {k:<5} 하위: 비용 후 연 초과 {ex.mean() * 252:+.2%} · IR {ex.mean() / ex.std() * np.sqrt(252):+.2f} · 연회전 {R[k][1]:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
