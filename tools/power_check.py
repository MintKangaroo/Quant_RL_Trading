"""검정력 계산기 — 등록 전 "이 창·이 포트에서 최소 몇 %p 차이까지 가려내나"(self-improvement.md §10 ③, 사용자 10/10).

    .venv/bin/python tools/power_check.py                       # 기본 표: 창 31·62·250·2600세션 × 포트 24·100 × 빼기/교체
    .venv/bin/python tools/power_check.py --n 24 --every 10 --sessions 62

방법 — **효과가 0 인 가짜 처리(플라시보)** 로 잡음만 잰다. 자료는 금고 전(2026-06-30 까지) 국장, 유동 10억·이력 252 우주.
- 빼기형(TF·TG 류): 대조 = 무작위 점수 상위 N(재조정 every, 동일가중, 편도 0.41%) · 처리 = 같은 포트에서 우주 무작위 q 를 뺀 것.
- 교체형(TR·TK 류): 처리 = 대조 점수와 순위상관 ρ 인 점수로 고른 상위 N(모델이 조금 바뀐 것).
- 원리 시험(종목 단위): 무작위로 뺀 종목의 5세션 초과(대상 동일가중 대비) 세션 평균.
플라시보를 R 번 반복해 **연환산 차의 표준편차**(포트) · **5세션 초과의 표준오차**(원리)를 재고, 최소 탐지 효과 = (z₀.₉₇₅ + z₀.₈) × 표준오차.
창 길이 T 는 플라시보 표본을 T 세션 조각으로 잘라 조각마다 잰다(겹침·자기상관이 그대로 들어간다).
"""
from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from quant_rl_trading.store import Store
from tools import diag_ta_features as T

END = datetime(2026, 6, 30, 16, 0, tzinfo=UTC)
COST = 0.0041
Z = 1.959964 + 0.841621


def panel(store: Store) -> tuple[np.ndarray, np.ndarray, list[date]]:
    d = T.load(store, end=END)
    c, v = d["close"], d["value"]
    uni = (T.rmean(v, 20) >= T.MIN_VALUE) & (c.notna().rolling(252, min_periods=1).sum() >= 252)
    r = (c / c.shift(1) - 1.0).clip(-0.5, 1.0)
    keep = [x for x in c.index if x >= date(2022, 9, 1)]
    return r.loc[keep].to_numpy(), uni.loc[keep].to_numpy(), keep


def book(r: np.ndarray, picks: list[np.ndarray], reb: list[int]) -> np.ndarray:
    """결정일 i 의 동일가중 → i+2 부터의 일수익(d+1 종가 진입), 재조정마다 |Δw| × 편도 비용."""
    out = np.zeros(len(r))
    prev = np.zeros(r.shape[1])
    for j, i in enumerate(reb):
        w = np.zeros(r.shape[1])
        if len(picks[j]):
            w[picks[j]] = 1.0 / len(picks[j])
        i0, i1 = i + 2, (reb[j + 1] + 2 if j + 1 < len(reb) else len(r))
        if i0 >= len(r):
            break
        seg = np.nan_to_num(r[i0:i1])
        out[i0:i1] = seg @ w
        out[i0] -= np.abs(w - prev).sum() * COST
        prev = w
    return out


def placebo(r: np.ndarray, uni: np.ndarray, *, n: int, every: int, kind: str, q: float, rho: float,
            rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    reb = list(range(0, len(r) - 2, every))
    ctrl, treat, mech = [], [], np.full(len(r), np.nan)
    fwd = np.full_like(r, np.nan)
    lr = np.log1p(np.nan_to_num(r))
    for i in range(len(r) - 6):
        fwd[i] = np.expm1(lr[i + 2:i + 7].sum(axis=0))
    for i in reb:
        idx = np.flatnonzero(uni[i])
        s = rng.standard_normal(len(idx))
        top = idx[np.argsort(-s)[:n]]
        ctrl.append(top)
        if kind == "floor":
            cut = set(rng.choice(idx, int(len(idx) * q), replace=False))
            order = idx[np.argsort(-s)]
            treat.append(np.array([x for x in order if x not in cut][:n]))      # 뺀 자리는 다음 순위로 채운다(판정 portfolio 와 같다)
            if i < len(r) - 6:
                f = fwd[i][idx]
                mech[i] = np.nanmean(fwd[i][list(cut)]) - np.nanmean(f)
        else:
            s2 = rho * s + np.sqrt(1 - rho ** 2) * rng.standard_normal(len(idx))
            treat.append(idx[np.argsort(-s2)[:n]])
    diff = book(r, treat, reb) - book(r, ctrl, reb)
    return diff, mech


def mde(series: list[np.ndarray], sessions: int, annual: bool) -> float:
    """플라시보마다 길이 sessions 조각의 평균(연환산)을 모아 그 표준편차 × (z₀.₉₇₅+z₀.₈)."""
    vals = []
    for x in series:
        x = x[~np.isnan(x)]
        for k in range(0, len(x) - sessions + 1, max(sessions // 2, 1)):
            vals.append(x[k:k + sessions].mean() * (252 if annual else 1))
    return float(Z * np.std(vals)) if len(vals) > 3 else float("nan")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, nargs="*", default=[24, 100])
    ap.add_argument("--every", type=int, default=10)
    ap.add_argument("--sessions", type=int, nargs="*", default=[31, 62, 250])
    ap.add_argument("--reps", type=int, default=20)
    ap.add_argument("--q", type=float, default=0.20)
    ap.add_argument("--rho", type=float, default=0.95)
    args = ap.parse_args(argv)
    r, uni, days = panel(Store(root=Path("data")))
    print(f"자료 {days[0]}~{days[-1]} · {len(days)}세션(금고 전) · 플라시보 {args.reps}회 · 재조정 {args.every} · 편도 {COST:.2%}")
    print("최소 탐지 효과(80% 검정력·양측 5%) — 이보다 작은 진짜 효과는 그 창에서 대부분 놓친다\n")
    print("| 포트 | 처리 | " + " | ".join(f"{s}세션" for s in args.sessions) + " |")
    print("|---|---|" + "---|" * len(args.sessions))
    mech_rows = []
    for n in args.n:
        for kind in ("floor", "swap"):
            diffs, mechs = [], []
            for rep in range(args.reps):
                d, m = placebo(r, uni, n=n, every=args.every, kind=kind, q=args.q, rho=args.rho, rng=np.random.default_rng(rep))
                diffs.append(d[2:])
                mechs.append(m[::5])
            label = f"빼기 하위 {args.q:.0%}" if kind == "floor" else f"교체 ρ {args.rho}"
            print(f"| 상위 {n} | {label} | " + " | ".join(f"연 ±{mde(diffs, s, True):.1%}p" for s in args.sessions) + " |")
            if kind == "floor" and not mech_rows:
                mech_rows = [mde(mechs, max(s // 5, 2), False) for s in args.sessions]
    print("\n원리 시험(뺀 종목 5세션 초과, 5세션 간격 표본): " + " · ".join(
        f"{s}세션 ±{m:.2%}" for s, m in zip(args.sessions, mech_rows, strict=True)))
    print("\n읽는 법: 등록하려는 시행의 기대 효과(탐색 결과의 절반으로 깎은 값)가 이 표의 값보다 작으면, 그 창의 금고에 걸지 말고 더 긴 창(과거 금고)이나 전방 장부로 보낸다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
