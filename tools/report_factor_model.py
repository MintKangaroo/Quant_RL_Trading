"""팩터 모델의 설명력을 찍는다 (portfolio §2·§7).

    .venv/bin/python tools/report_factor_model.py [--n 30] [--window 250]

고유분산 비율(잔차/총분산)이 높으면 팩터가 부족한 것이다(§7). 시장+섹터만
있는 지금은 그 비율이 높게 나올 것이고, 그것이 스타일 팩터를 붙여야 하는
근거다. LW 축소 강도와 공분산의 PSD 여부도 같이 낸다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.portfolio import factor_model as fm  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.selector.filters import (  # noqa: E402
    FilterParams,
    tradable_universe,
)
from tools.backfill import build_store  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=30, help="후보 종목 수")
    parser.add_argument("--window", type=int, default=fm.DEFAULT_WINDOW)
    parser.add_argument("--market", default="KR")
    parser.add_argument("--equity", type=float, default=5e8)
    args = parser.parse_args()

    store = build_store()
    as_of = LiveClock().now()
    params = FilterParams.from_store(store, as_of=as_of, market=args.market)
    uni = tradable_universe(
        store, as_of=as_of, market=args.market, params=params, equity=args.equity
    )
    entities = list(uni.kept)[: args.n]
    model = fm.estimate(
        store, as_of=as_of, entities=entities, market=args.market, window=args.window
    )
    if model is None:
        print("모델을 낼 수 없다 — 섹터·가격·지수 중 하나가 비었다")
        return 1

    cov = model.covariance.to_numpy()
    eig = np.linalg.eigvalsh(cov)
    share = model.idio_share()
    print(f"팩터 모델 · {args.market} · {model.covariance.shape[0]}종목 · "
          f"창 {args.window} · as_of {as_of:%F}")
    print(f"팩터 수(실린): {model.factor_covariance.shape[0]}  "
          f"(MKT + 섹터 {model.factor_covariance.shape[0]-1})")
    print(f"LW 축소 강도: {model.shrinkage:.3f}")
    print(f"공분산 최소 고유값: {eig.min():.2e}  "
          f"({'PSD' if eig.min() >= -1e-9 else '음의 고유값!'})")
    print(f"고유분산 비율: 중앙값 {share.median():.2f} · "
          f"25%{share.quantile(.25):.2f} · 75%{share.quantile(.75):.2f} · "
          f"최대 {share.max():.2f}")
    if share.median() > 0.5:
        print("  → 절반 넘게 설명 안 됨. 스타일 팩터(시총·밸류·모멘텀·변동성)가 필요하다.")
    vol = np.sqrt(np.diag(cov) * 252)
    print(f"연율변동성: 중앙값 {np.median(vol):.1%} · "
          f"최소 {vol.min():.1%} · 최대 {vol.max():.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
