"""AI v2 U1 — 가우시안 HMM 국면 확률 (docs/design/ai-architecture-v2.md §2 L2).

    .venv/bin/python tools/v2_regime_hmm.py --market KR|US [--states 3]

규칙 `regime.classify`(계단식)의 대안 — 지수의 [일수익, 20일 수익, 20일 실현변동성]에 대각 공분산 가우시안 HMM 을 적합하고,
세션마다 **그날까지의 관측만으로** 거른 상태 확률 p(s_t | x_≤t) 를 낸다(전방 알고리즘, 스무딩 아님 — 미래를 안 본다).
파라미터는 **확장창·월 1회 재적합**(그 달의 세션은 전달 말까지 적합한 파라미터로 거른다). 상태 순서는 적합마다 평균 일수익 오름차순으로
맞춘다(상태 0 = 가장 나쁜 국면) — 재적합으로 이름이 뒤바뀌지 않게.
hmmlearn 이 없어 numpy 로 구현했다(Baum–Welch, log-space).

산출: data/_diag/v2/hmm-{market}.pkl — 세션(date) × [p0..p{K-1}, state_argmax]. 연구 캐시(창고 아님).
"""
from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime, time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy.special import logsumexp  # noqa: E402

from quant_rl_trading.store import Store  # noqa: E402

OUT = Path("data/_diag/v2")
INDEX = {"KR": "KR:IDX:KOSPI200", "US": "US:IDX:SP500"}
MIN_FIT = 250          # 첫 적합에 필요한 세션 수
EM_ITERS, EM_TOL = 200, 1e-6


def features(closes: pd.Series) -> pd.DataFrame:
    r = np.log(closes).diff()
    f = pd.DataFrame({"r1": r, "r20": r.rolling(20).sum(), "vol20": r.rolling(20).std() * np.sqrt(252)})
    return f.dropna()


def _log_emission(x: np.ndarray, mu: np.ndarray, var: np.ndarray) -> np.ndarray:
    """(T, K) 로그 가능도 — 대각 가우시안."""
    return -0.5 * (np.log(2 * np.pi * var)[None, :, :] + (x[:, None, :] - mu[None, :, :]) ** 2 / var[None, :, :]).sum(axis=2)


def fit(x: np.ndarray, k: int, seed: int = 0) -> dict:
    """Baum–Welch. 초기값은 r20 분위로 나눈 구간 평균 — 무작위 초기화의 흔들림을 줄인다."""
    t, d = x.shape
    order = np.argsort(x[:, 1])
    chunks = np.array_split(order, k)
    mu = np.stack([x[c].mean(axis=0) for c in chunks])
    var = np.stack([x[c].var(axis=0) + 1e-6 for c in chunks])
    logA = np.log(np.full((k, k), 0.05 / (k - 1)) + np.eye(k) * (0.95 - 0.05 / (k - 1)))
    logpi = np.log(np.full(k, 1.0 / k))
    prev = -np.inf
    for _ in range(EM_ITERS):
        le = _log_emission(x, mu, var)
        la = np.empty((t, k)); lb = np.empty((t, k))
        la[0] = logpi + le[0]
        for i in range(1, t):
            la[i] = logsumexp(la[i - 1][:, None] + logA, axis=0) + le[i]
        lb[-1] = 0.0
        for i in range(t - 2, -1, -1):
            lb[i] = logsumexp(logA + le[i + 1][None, :] + lb[i + 1][None, :], axis=1)
        ll = logsumexp(la[-1])
        gamma = np.exp(la + lb - ll)
        xi = np.exp(la[:-1, :, None] + logA[None, :, :] + (le[1:] + lb[1:])[:, None, :] - ll)
        logpi = np.log(gamma[0] + 1e-12)
        logA = np.log(xi.sum(axis=0) + 1e-12) - np.log(xi.sum(axis=(0, 2))[:, None] + 1e-12)
        w = gamma.sum(axis=0)[:, None]
        mu = gamma.T @ x / w
        var = gamma.T @ (x ** 2) / w - mu ** 2 + 1e-6
        if abs(ll - prev) < EM_TOL * max(1.0, abs(ll)):
            break
        prev = ll
    perm = np.argsort(mu[:, 0])                     # 평균 일수익 오름차순 — 상태 0 이 가장 나쁘다
    return {"mu": mu[perm], "var": var[perm], "logA": logA[np.ix_(perm, perm)], "logpi": logpi[perm], "ll": float(ll)}


def filtered(x: np.ndarray, params: dict, start_log: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """전방 필터 p(s_t | x_≤t). start_log 는 직전 달 마지막 필터 상태(없으면 초기분포)."""
    le = _log_emission(x, params["mu"], params["var"])
    k = le.shape[1]
    out = np.empty_like(le)
    prev = start_log
    for i in range(len(x)):
        prior = params["logpi"] if prev is None else logsumexp(prev[:, None] + params["logA"], axis=0)
        post = prior + le[i]
        post -= logsumexp(post)
        out[i] = post
        prev = post
    return np.exp(out), (prev if prev is not None else np.log(np.full(k, 1.0 / k)))


def run(closes: pd.Series, k: int) -> pd.DataFrame:
    f = features(closes)
    x_all = f.to_numpy()
    months = pd.PeriodIndex(pd.to_datetime(pd.Series(f.index)), freq="M")
    rows = []
    state_log = None
    for m in months.unique():
        idx = np.flatnonzero(months == m)
        first = idx[0]
        if first < MIN_FIT:
            continue
        params = fit(x_all[:first], k)               # 그 달 첫 세션 **전** 까지만 적합
        # 필터는 연속으로 잇는다 — 직전 달 마지막 필터 확률에서 출발(파라미터만 갈아끼운다).
        if state_log is None:
            _, state_log = filtered(x_all[max(0, first - 60):first], params)
        p, state_log = filtered(x_all[idx], params, state_log)
        for j, i in enumerate(idx):
            rows.append({"session": f.index[i], **{f"p{s}": float(p[j, s]) for s in range(k)}})
    out = pd.DataFrame(rows).set_index("session")
    out["state"] = out[[f"p{s}" for s in range(k)]].to_numpy().argmax(axis=1)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--market", required=True, choices=sorted(INDEX))
    parser.add_argument("--states", type=int, default=3)
    parser.add_argument("--until", default="2026-06-30", help="금고(2026-07-01~) 전까지만")
    args = parser.parse_args(argv)
    store = Store(root=Path("data"))
    until = date.fromisoformat(args.until)
    end = datetime.combine(until, time(23), tzinfo=UTC)
    idx = store.get("indices", as_of=end, lookback=(until - date(2020, 1, 1)).days, market=args.market,
                    columns=["entity_id", "valid_from", "close"])
    idx = idx[idx["entity_id"] == INDEX[args.market]].assign(day=lambda f: pd.to_datetime(f["valid_from"]).dt.date)
    closes = idx.groupby("day")["close"].last().sort_index()
    closes = closes[closes > 0]
    out = run(closes, args.states)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"hmm-{args.market}.pkl"
    out.to_pickle(path)
    share = out["state"].value_counts(normalize=True).sort_index()
    switches = int((out["state"].diff() != 0).sum() - 1)
    print(f"{args.market} {INDEX[args.market]} · 세션 {len(out)} ({out.index.min()}~{out.index.max()}) · 상태 비율 "
          + " · ".join(f"s{s} {v:.0%}" for s, v in share.items()) + f" · argmax 전환 {switches}회 → {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
