"""AI v2 U2 — 수익 상관 군집 (docs/design/ai-architecture-v2.md §2 L2).

    .venv/bin/python tools/v2_clusters.py --market KR|US [--top 300] [--clusters 12]

매월 첫 세션, **그 전 60세션** 일수익에서 시장 요인(첫 주성분)을 뺀 잔차 상관으로 계층 군집(Ward)을 만든다. 우주는 그 시점 20일 평균 거래대금 상위 N.
쓰임: L1 기울이기의 군집 중립화(업종 쏠림으로 벤치와 어긋나는 것 방지) · 리스크 패리티의 섹터 대체 — KSIC 는 분류 모르는 종목이 많고 미장엔 없다.
산출: data/_diag/v2/clusters-{market}.pkl — (month_start, entity_id, cluster). 연구 캐시(창고 아님). 미래를 안 본다(전 60세션만).
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
from scipy.cluster.hierarchy import fcluster, linkage  # noqa: E402
from scipy.spatial.distance import squareform  # noqa: E402

from quant_rl_trading.store import Store  # noqa: E402
from quant_rl_trading.store.prices import read_prices  # noqa: E402

OUT = Path("data/_diag/v2")
WINDOW, MIN_OBS = 60, 50


def cluster_month(returns: pd.DataFrame, n_clusters: int) -> pd.Series:
    """**시장 요인을 빼고** 묶는다 — 원수익 상관은 시장 한 요인이 지배해 가장 큰 군집이 59~79% 가 됐다(9/26 첫 시험).
    결측을 종목 평균(0)으로 채우고 표준화한 뒤 첫 주성분(시장)을 걷어낸 잔차의 상관으로, Ward 연결(거리 √(2(1−ρ)))."""
    r = returns.dropna(axis=1, thresh=MIN_OBS)
    if r.shape[1] < n_clusters * 2:
        return pd.Series(dtype=int)
    z = ((r - r.mean()) / r.std(ddof=0).replace(0, np.nan)).fillna(0.0).to_numpy()
    u, s, vt = np.linalg.svd(z, full_matrices=False)
    resid = z - np.outer(u[:, 0] * s[0], vt[0])
    corr = np.corrcoef(resid, rowvar=False)
    corr = np.nan_to_num(corr, nan=0.0).clip(-1, 1)
    dist = np.sqrt(np.maximum(2.0 * (1.0 - corr), 0.0))
    np.fill_diagonal(dist, 0.0)
    link = linkage(squareform(dist, checks=False), method="ward")
    return pd.Series(fcluster(link, t=n_clusters, criterion="maxclust"), index=r.columns)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--market", required=True, choices=("KR", "US"))
    parser.add_argument("--top", type=int, default=300)
    parser.add_argument("--clusters", type=int, default=12)
    parser.add_argument("--start", default="2022-07-01")
    parser.add_argument("--until", default="2026-06-30", help="금고(2026-07-01~) 전까지만")
    args = parser.parse_args(argv)
    store = Store(root=Path("data"))
    until = date.fromisoformat(args.until)
    start = date.fromisoformat(args.start)
    end = datetime.combine(until, time(23), tzinfo=UTC)
    prices = read_prices(store, as_of=end, lookback=(until - start).days + 150, columns=["close", "volume"], adjusted=True, market=args.market)
    prices["day"] = pd.to_datetime(prices["valid_from"]).dt.date
    close = prices.pivot_table(index="day", columns="entity_id", values="close", aggfunc="last").sort_index()
    volume = prices.pivot_table(index="day", columns="entity_id", values="volume", aggfunc="last").sort_index()
    del prices
    ret = close.pct_change(fill_method=None).where(lambda x: x.abs() < 0.5)
    dv = (close * volume).rolling(20, min_periods=10).mean()
    days = [d for d in close.index if start <= d <= until]
    firsts = pd.Series(days, index=pd.PeriodIndex(pd.to_datetime(pd.Series(days)), freq="M")).groupby(level=0).first()
    rows, sizes = [], []
    for month_start in firsts:
        pos = close.index.get_loc(month_start)
        if pos < WINDOW + 1:
            continue
        prev_day = close.index[pos - 1]                       # 그 달 첫 세션 **전** 까지
        universe = dv.loc[prev_day].dropna().nlargest(args.top).index
        window = ret.iloc[pos - WINDOW:pos][universe]
        c = cluster_month(window, args.clusters)
        rows += [{"month_start": month_start, "entity_id": e, "cluster": int(k)} for e, k in c.items()]
        if len(c):
            sizes.append(c.value_counts().max() / len(c))
    out = pd.DataFrame(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"clusters-{args.market}.pkl"
    out.to_pickle(path)
    print(f"{args.market} 군집 {out['month_start'].nunique()}개월 · 월 평균 {len(out) / max(1, out['month_start'].nunique()):.0f}종목 · "
          f"가장 큰 군집 비중 평균 {np.mean(sizes):.0%} → {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
