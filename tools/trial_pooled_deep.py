"""합침 랭커 심층 진단 — "왜 배운 결합이 손 부호를 못 이기나" 를 가르는 실험 묶음 (베타, 회차 미차감).

  D1 피처별 IC(판정 블록, 시장별)              — 어느 신호에 정보가 있나
  D2 in-sample 최적 선형 결합 IC(치팅)          — 이 피처 공간의 천장
  D3 표본 곡선: 합침 학습 10/25/50/100% → IC    — 데이터를 더 모으면 오르나
  D4 라벨 지평: h5 vs h20 (국장)                 — 잡음 적은 라벨이면 배우나
  D5 잔차 학습: fundamental 위에 나머지로 ΔIC    — 룰을 앵커로 두면 더해지나
  D6 교차 전이: 미장만 학습 → 국장 판정          — 시장 밖 표본이 옮겨지나
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from tools.trial_pooled import (  # noqa: E402
    BLOCK, CACHE, FEATS, MIN_TRAIN, PURGE, _nw, _z, daily_ic, fit_gbm, fit_ridge, load_kr, load_us, predict_ridge,
)


def blocks_for(sessions):
    out = []; end = MIN_TRAIN
    while end + PURGE + BLOCK <= len(sessions):
        out.append((sessions[end - 1], sessions[end + PURGE], sessions[end + PURGE + BLOCK - 1])); end += BLOCK
    return out


def walk(kr, us, blocks, fitter, cols, train_sel="pool", frac=1.0, seed=0, target="target"):
    parts = []
    rng = np.random.default_rng(seed)
    for (train_end, ps, pe) in blocks:
        tr_kr = kr[kr["session"] <= train_end]; tr_us = us[us["session"] <= train_end]
        train = {"kr": tr_kr, "us": tr_us, "pool": pd.concat([tr_kr, tr_us], ignore_index=True)}[train_sel]
        if frac < 1.0:
            train = train.sample(frac=frac, random_state=int(rng.integers(1e9)))
        te = kr[(kr["session"] >= ps) & (kr["session"] <= pe)].copy()
        f = fitter(train[cols].to_numpy(np.float32), train[target].to_numpy(np.float32))
        te["pred"] = f(te[cols].to_numpy(np.float32))
        parts.append(te[["entity_id", "session", "target", "fundamental", "pred"]])
    d = pd.concat(parts, ignore_index=True)
    ic_p = daily_ic(d, "pred"); ic_f = daily_ic(d, "fundamental"); c = ic_p.index.intersection(ic_f.index)
    delta = ic_p.loc[c] - ic_f.loc[c]
    return float(ic_p.mean()), _nw(ic_p, 4), float(delta.mean()), _nw(delta, 4)


def ridge_fitter(X, y):
    w = fit_ridge(X, y); return lambda Z: predict_ridge(w, Z)


def gbm_fitter(X, y):
    m = fit_gbm(X, y); return lambda Z: m.predict(Z)


def main() -> int:
    kr, kr_all = load_kr(); us = load_us()
    for df in (kr, us):
        df[FEATS] = _z(df, FEATS)
    kr["is_us"] = 0.0; us["is_us"] = 1.0
    X = FEATS + ["is_us"]
    sessions = sorted(kr["session"].unique()); blocks = blocks_for(sessions)
    judge = kr[(kr["session"] >= blocks[0][1]) & (kr["session"] <= blocks[-1][2])]
    judge_us = us[(us["session"] >= blocks[0][1]) & (us["session"] <= blocks[-1][2])]
    print(f"판정 {blocks[0][1]}~{blocks[-1][2]} · 국장 {judge['session'].nunique()}세션 · 미장 {judge_us['session'].nunique()}세션", flush=True)

    print("\n== D1 피처별 IC(h5), 판정 블록 ==")
    for f in FEATS:
        a = daily_ic(judge, f); b = daily_ic(judge_us, f)
        print(f"  {f:12} 국장 {a.mean():+.4f} (t {_nw(a,4):+.2f})   미장 {b.mean():+.4f} (t {_nw(b,4):+.2f})")

    print("\n== D2 in-sample 최적 선형 결합(치팅 상한) ==")
    for label, d in (("국장 판정블록", judge), ("미장 판정블록", judge_us)):
        w = fit_ridge(d[FEATS].to_numpy(np.float32), d["target"].to_numpy(np.float32))
        dd = d.copy(); dd["pred"] = predict_ridge(w, d[FEATS].to_numpy(np.float32))
        ic = daily_ic(dd, "pred"); ic_f = daily_ic(dd, "fundamental")
        print(f"  {label}: 치팅 선형 IC {ic.mean():+.4f} 대 fund {ic_f.mean():+.4f} · 가중치 " + " ".join(f"{n}={v:+.3f}" for n, v in zip(FEATS, w[1:])))

    print("\n== D3 표본 곡선 (GBM, 합침 학습 → 국장 판정) ==", flush=True)
    for frac in (0.1, 0.25, 0.5, 1.0):
        ic, t, d, dt = walk(kr, us, blocks, gbm_fitter, X, "pool", frac=frac)
        print(f"  표본 {frac:>4.0%}: IC {ic:+.4f} (t {t:+.2f}) · ΔIC {d:+.4f} (NW t {dt:+.2f})", flush=True)

    print("\n== D4 라벨 지평 h20 (국장 단독·합침 불가 — 미장 h20 없음) ==", flush=True)
    t20 = pd.read_pickle(CACHE / "targets-KR-h20.pkl"); t20["session"] = pd.to_datetime(t20["session"]).dt.date
    kr20 = kr.drop(columns=["target"]).merge(t20, on=["entity_id", "session"], how="inner")
    for name, fitter in (("RIDGE", ridge_fitter), ("GBM", gbm_fitter)):
        ic, t, d, dt = walk(kr20, us.iloc[0:0], blocks, fitter, FEATS, "kr")
        print(f"  {name}-KR h20: IC {ic:+.4f} (t {t:+.2f}) · ΔIC vs fund {d:+.4f} (NW t {dt:+.2f})", flush=True)
    icf = daily_ic(kr20[(kr20['session'] >= blocks[0][1]) & (kr20['session'] <= blocks[-1][2])], "fundamental")
    print(f"  fund h20 IC {icf.mean():+.4f} (t {_nw(icf,19):+.2f})")

    print("\n== D5 잔차 학습 (타깃 − β·fundamental 을 나머지 5개로, 합침) ==", flush=True)
    others = [f for f in FEATS if f != "fundamental"] + ["is_us"]
    def resid_fitter(Xf, y):
        # Xf 의 마지막 열이 fundamental 이라고 가정하지 않는다 — 호출부에서 fundamental 을 따로 넘긴다
        return None
    for label, sel in (("합침", "pool"), ("국장", "kr")):
        parts = []
        for (train_end, ps, pe) in blocks:
            tr = {"kr": kr[kr["session"] <= train_end], "pool": pd.concat([kr[kr["session"] <= train_end], us[us["session"] <= train_end]], ignore_index=True)}[sel]
            beta = float(np.polyfit(tr["fundamental"], tr["target"], 1)[0])
            resid = tr["target"] - beta * tr["fundamental"]
            m = fit_gbm(tr[others].to_numpy(np.float32), resid.to_numpy(np.float32))
            te = kr[(kr["session"] >= ps) & (kr["session"] <= pe)].copy()
            te["pred"] = beta * te["fundamental"] + m.predict(te[others].to_numpy(np.float32))
            parts.append(te[["entity_id", "session", "target", "fundamental", "pred"]])
        d = pd.concat(parts, ignore_index=True); ic_p = daily_ic(d, "pred"); ic_f = daily_ic(d, "fundamental")
        c = ic_p.index.intersection(ic_f.index); delta = ic_p.loc[c] - ic_f.loc[c]
        print(f"  {label}: IC {ic_p.mean():+.4f} · ΔIC vs fund {delta.mean():+.4f} (NW t {_nw(delta,4):+.2f})", flush=True)

    print("\n== D6 교차 전이 (미장만 학습 → 국장 판정) ==", flush=True)
    for name, fitter in (("RIDGE", ridge_fitter), ("GBM", gbm_fitter)):
        ic, t, d, dt = walk(kr, us, blocks, fitter, FEATS, "us")
        print(f"  {name}-US→KR: IC {ic:+.4f} (t {t:+.2f}) · ΔIC {d:+.4f} (NW t {dt:+.2f})", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
