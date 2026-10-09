"""TSFM 매일 예측 진단 — 제로샷 진단(docs/diag/tsfm-zeroshot.md)의 국장 결과(IC 0.06~0.13)를 포트로 옮겨 본다 (2026-10-09, 탐색).

    .venv/bin/python tools/diag_tsfm_daily.py extract            # 국장 상위 300 · 매일(2025-03-03~) 입력·라벨
    .venv-bench/bin/python bench/tsfm_daily_infer.py               # Chronos-Bolt·TTM, L256, 가격만
    .venv/bin/python tools/diag_tsfm_daily.py score                # IC · 10분위 · 상위 N 포트(비용 후) · 반전과의 겹침

입력·라벨 규칙은 제로샷 진단의 `build_rows` 그대로(같은 함수), 다른 것은 세션 간격 1 하나다. 창은 두 모델 D₀(2025-01-08 · 2025-02-25) 뒤.
금고(2026-07-01~)는 읽지 않는다. 창고에 쓰지 않는다.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from tools import diag_tsfm_zeroshot as z  # noqa: E402

OUT = Path("data/_diag/tsfm-daily")
START, END = date(2025, 3, 3), date(2026, 6, 30)
MODELS = ("chronos_bolt", "ttm")
COST = 0.0041


def extract() -> None:
    from quant_rl_trading.store import Store
    (OUT / "inputs").mkdir(parents=True, exist_ok=True)
    close, dv = z.load_market(Store(), "KR")
    idx = list(close.index)
    pos = [i for i, d in enumerate(idx) if START <= d and i + z.H < len(idx) and idx[i + z.H] <= END]
    keys, p, _s, lab = z.build_rows(close, dv, "KR", pos)
    assert lab["session"].max() < z.VAULT_START and max(idx[i + z.H] for i in pos) < z.VAULT_START
    np.savez(OUT / "inputs" / "ctx-KR-D.npz", price=p)
    keys.to_pickle(OUT / "inputs" / "keys-KR-D.pkl")
    lab.to_pickle(OUT / "labels-KR-D.pkl")
    print(f"세션 {keys['session'].nunique()} · 행 {len(keys):,} · {keys['session'].min()}~{keys['session'].max()}", flush=True)


def score() -> None:
    from quant_rl_trading.analysts import ic as icm
    keys = pd.read_pickle(OUT / "inputs" / "keys-KR-D.pkl")
    lab = pd.read_pickle(OUT / "labels-KR-D.pkl")
    df = keys[["session", "entity_id", "rev5", "mom20"]].copy()
    df["r"] = lab["r"].to_numpy()
    for m in MODELS:
        f = OUT / "preds" / f"{m}.npz"
        if f.exists():
            pr = np.load(f)["price"]
            df[m] = pr[:, -1] / pr[:, 0] - 1.0          # r̂ = p̂(t+6)/p̂(t+1) − 1 (제로샷 진단과 같다)
    sig = [m for m in MODELS if m in df] + ["rev5"]
    print(f"세션 {df['session'].nunique()} · 세션당 {int(df.groupby('session').size().median())}종목", flush=True)
    for s in sig:
        ic = df.groupby("session").apply(lambda g: g[s].rank().corr(g["r"].rank()))
        print(f"IC {s}: {ic.mean():+.4f} · NW t(lag 5) {icm.newey_west_t(ic, lag=5):+.2f} · 양수 {(ic > 0).mean():.0%}", flush=True)
    for m in [x for x in MODELS if x in df]:
        rho = df.groupby("session").apply(lambda g: g[m].rank().corr(g["rev5"].rank())).mean()
        # 반전을 뺀 잔차의 IC — 세션마다 순위 회귀 잔차
        def resid_ic(g):
            a, b = g[m].rank(), g["rev5"].rank()
            e = a - np.polyval(np.polyfit(b, a, 1), b)
            return e.corr(g["r"].rank())
        ric = df.groupby("session").apply(resid_ic)
        print(f"{m}: 반전5 와 순위상관 {rho:+.2f} · 반전 뺀 잔차 IC {ric.mean():+.4f} (t {icm.newey_west_t(ric, lag=5):+.2f})", flush=True)
        q = df.groupby("session")[m].transform(lambda x: pd.qcut(x.rank(method="first"), 10, labels=False))
        exc = df["r"] - df.groupby("session")["r"].transform("mean")
        dec = exc.groupby(q).mean()
        print(f"  10분위 5일 초과(대 300 평균): " + " ".join(f"D{int(k) + 1} {v:+.2%}" for k, v in dec.items()), flush=True)
        # 상위 N, 5세션마다 겹치지 않게(각 세션 다음 5일) — 순 비용: 회전 비율 × 편도 × 2
        sess = sorted(df["session"].unique())[::5]
        for n in (20, 30, 50):
            rows, prev = [], set()
            for s in sess:
                g = df[df["session"] == s].dropna(subset=[m, "r"])
                top = set(g.nlargest(n, m)["entity_id"])
                turn = 1.0 if not prev else len(top - prev) / n
                rows.append({"s": s, "gross": g[g["entity_id"].isin(top)]["r"].mean() - g["r"].mean(),
                             "cost": turn * COST * 2, "abs": g[g["entity_id"].isin(top)]["r"].mean(), "uni": g["r"].mean()})
                prev = top
            f = pd.DataFrame(rows)
            net = f["gross"] - f["cost"]
            k = 245 / 5
            print(f"  상위 {n}(5일 보유): 대 300 평균 비용 전 {f['gross'].mean() * k:+.1%}/년 · 후 {net.mean() * k:+.1%}/년 "
                  f"(IR {net.mean() / net.std() * np.sqrt(k):+.2f}) · 회전/회 {(f['cost'] / COST / 2).mean():.0%} · 포트 절대 {f['abs'].mean() * k:+.1%}/년 · 300평균 {f['uni'].mean() * k:+.1%}/년",
                  flush=True)


if __name__ == "__main__":
    {"extract": extract, "score": score}[sys.argv[1]]()
