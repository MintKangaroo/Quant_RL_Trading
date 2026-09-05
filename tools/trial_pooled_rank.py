"""D7 — 목적함수 정렬: 타깃·피처를 일별 순위 가우시안으로 바꿔 최소제곱 = Spearman IC 최대화가 되게 한다.
D2 에서 치팅 선형 결합(0.054)이 fundamental 단독(0.076)보다 낮았던 것이 목적함수 불일치라면 여기서 역전이 사라져야 한다."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy.stats import norm  # noqa: E402

from tools.trial_pooled import FEATS, _nw, daily_ic, fit_gbm, fit_ridge, load_kr, load_us, predict_ridge  # noqa: E402
from tools.trial_pooled_deep import blocks_for, walk  # noqa: E402


def rank_gauss(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """세션×시장 안에서 순위 → 정규분위. Pearson 이 Spearman 이 된다."""
    out = df.copy()
    g = df.groupby(["market", "session"])
    for c in cols:
        r = g[c].rank(pct=True)
        # 결측(그 Analyst 가 그 종목을 안 낸 날)은 순위 중앙(0) — _z 와 같은 처리. 안 채우면 NaN 이 릿지를 통째로 죽인다.
        cnt = g[c].transform("count")
        out[c] = pd.Series(norm.ppf((r * cnt - 0.5) / cnt), index=df.index).fillna(0.0).astype(np.float32)
    return out


def main() -> int:
    kr, _ = load_kr(); us = load_us()
    kr = rank_gauss(kr, FEATS + ["target"]); us = rank_gauss(us, FEATS + ["target"])
    kr["is_us"] = 0.0; us["is_us"] = 1.0
    X = FEATS + ["is_us"]
    sessions = sorted(kr["session"].unique()); blocks = blocks_for(sessions)
    judge = kr[(kr["session"] >= blocks[0][1]) & (kr["session"] <= blocks[-1][2])]
    w = fit_ridge(judge[FEATS].to_numpy(np.float32), judge["target"].to_numpy(np.float32))
    d = judge.copy(); d["pred"] = predict_ridge(w, judge[FEATS].to_numpy(np.float32))
    ic = daily_ic(d, "pred"); icf = daily_ic(d, "fundamental")
    print(f"D7-치팅 선형(순위 타깃): IC {float(np.asarray(ic).mean()):+.4f} 대 fund {float(np.asarray(icf).mean()):+.4f} · 가중치 " + " ".join(f"{n}={v:+.3f}" for n, v in zip(FEATS, w[1:])), flush=True)
    for name, fitter, sel in (("RIDGE-KR", lambda X_, y: (lambda Z, w=fit_ridge(X_, y): predict_ridge(w, Z)), "kr"),
                              ("RIDGE-POOL", lambda X_, y: (lambda Z, w=fit_ridge(X_, y): predict_ridge(w, Z)), "pool"),
                              ("GBM-KR", lambda X_, y: (lambda Z, m=fit_gbm(X_, y): m.predict(Z)), "kr"),
                              ("GBM-POOL", lambda X_, y: (lambda Z, m=fit_gbm(X_, y): m.predict(Z)), "pool")):
        ic, t, dl, dt = walk(kr, us, blocks, fitter, X, sel)
        print(f"D7-{name:10} 워크포워드: IC {ic:+.4f} (t {t:+.2f}) · ΔIC vs fund {dl:+.4f} (NW t {dt:+.2f})", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
