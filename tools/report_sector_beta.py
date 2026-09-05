"""섹터 상·하방 베타를 찍는다 — 방어 관계를 눈으로 본다 (portfolio §1·§7).

    .venv/bin/python tools/report_sector_beta.py [--window 250] [--market KR]

하방 베타가 낮게 측정된 섹터가 **실제로** 방어적인지, 아니면 그냥 현금처럼
둘 다 낮은 것인지 가른다 (`SectorBeta.is_defensive`). 롤링 시계열도 같이 내서
낮게 측정된 섹터가 최근 하락 구간에서 덜 빠졌는지 사후로 볼 수 있게 한다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.portfolio import sector_beta as sb  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from tools.backfill import build_store  # noqa: E402


def _fmt(value: float) -> str:
    return f"{value:.2f}" if np.isfinite(value) else "NaN"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--window", type=int, default=sb.DEFAULT_WINDOW)
    parser.add_argument("--market", default="KR")
    parser.add_argument("--rolling", action="store_true", help="롤링 시계열도 낸다")
    args = parser.parse_args()

    store = build_store()
    as_of = LiveClock().now()
    betas = sb.estimate(store, as_of=as_of, market=args.market, window=args.window)
    if not betas:
        print("베타를 낼 수 없다 — 섹터·가격·지수 중 하나가 비었다")
        return 1

    ranked = sorted(
        betas.items(),
        key=lambda kv: np.inf if not np.isfinite(kv[1].down_beta) else kv[1].down_beta,
    )
    print(f"섹터 상·하방 베타 · {args.market} · 창 {args.window}세션 · as_of {as_of:%F}")
    print(f"{'섹터':<18}{'하방':>7}{'상방':>7}{'하락일':>7}{'상승일':>7}"
          f"{'종목':>6}  방어")
    for sector, beta in ranked:
        print(f"{sector:<18}{_fmt(beta.down_beta):>7}{_fmt(beta.up_beta):>7}"
              f"{beta.n_down:>7}{beta.n_up:>7}{beta.n_members:>6}"
              f"  {'○' if beta.is_defensive else ''}")

    defensive = [s for s, b in betas.items() if b.is_defensive]
    print(f"\n비대칭 방어 섹터(하방<0.7·상방>하방): "
          f"{', '.join(defensive) if defensive else '없다 — 낮은 섹터는 현금형'}")

    if args.rolling:
        series = sb.rolling_betas(
            store, as_of=as_of, market=args.market, window=args.window
        )
        print("\n롤링 하방 베타 (최근일 → 과거, 20세션 간격):")
        pivot = series.pivot(index="as_of_session", columns="sector", values="down_beta")
        print(pivot.round(2).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
