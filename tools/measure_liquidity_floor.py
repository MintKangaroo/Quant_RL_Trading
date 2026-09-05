"""유동성 하한을 자본에서 유도할 때 유니버스가 얼마나 줄어드는지 잰다.

    .venv/bin/python tools/measure_liquidity_floor.py

## 왜 재나 (portfolio-construction.md §부록)

지금 `universe.min_turnover_20d_kr` 은 **상수 5억**이다. 자본이 20만원이던
시절엔 "못 파는 종목을 거른다" 였는데, 자본이 5억이 되자 같은 상수가 전혀
다른 의미가 됐다 — 하한을 통과한 종목의 43% 가 애초에 담을 수 없다
(목표금액이 `max_adv_ratio` 상한을 넘어 잘린다).

용량 항등식:

    목표금액이 안 잘리려면  ADV ≥ (목표비중 / max_adv_ratio) × 자본

`max_position_weight`(0.15) 를 목표비중에 넣으면 배수 5 (= 25억), 실현 최대
비중 ~4.79% 를 넣으면 1.6 (= 8억). **부록은 값을 정하지 않았다** — 하한을
올리면 유니버스가 줄고, 그 트레이드오프를 재서 정하기로 했다. 이 스크립트가
그 측정이다.

세 가지를 각 후보 배수에서 찍는다:

1. 하한을 통과하는 종목 수 (유니버스 크기)
2. 그중 **최대 비중 종목이 잘리지 않는** 종목의 비율 (용량 충족률)
3. 지금 상수 5억 대비 몇 종목이 새로 빠지나
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.selector.filters import (  # noqa: E402
    TURNOVER_WINDOW,
    FilterParams,
    tradable_universe,
)
from quant_rl_trading.store.prices import read_prices  # noqa: E402
from tools.backfill import build_store  # noqa: E402

MARKET = "KR"

#: 재 볼 자본. 모의투자 예수금이 5억이다.
CAPITAL = 500_000_000.0

#: 재 볼 하한 배수. 1.6 = 실현 최대비중 기준, 5 = max_position_weight 기준.
MULTIPLES = [0.0, 1.0, 1.6, 3.0, 5.0]


def adv20(store, *, as_of, entities: list[str]) -> pd.Series:
    """종목별 20일 평균 거래대금. filters.py 와 같은 창·같은 방식으로 낸다."""
    prices = read_prices(
        store, as_of=as_of, entity=entities, lookback=TURNOVER_WINDOW * 2, market=MARKET
    )
    if prices.empty:
        return pd.Series(dtype=float)
    recent = prices.sort_values("valid_from")
    tail = recent.groupby("entity_id")["value"].tail(TURNOVER_WINDOW)
    return recent.loc[tail.index].groupby("entity_id")["value"].mean()


def main() -> int:
    store = build_store()
    as_of = LiveClock().now()
    max_adv_ratio = float(store.config("execution.max_adv_ratio", as_of=as_of))
    max_pos = float(store.config("allocator.max_position_weight", as_of=as_of))
    absolute = float(store.config(f"universe.min_turnover_20d_kr", as_of=as_of))

    print(f"as_of {as_of:%F %T} · 자본 {CAPITAL:,.0f}원")
    print(f"max_adv_ratio {max_adv_ratio:.0%} · max_position_weight {max_pos:.0%}")
    print(f"현재 상수 하한 {absolute:,.0f}원\n")

    # 지금 상수 하한으로 통과하는 유니버스를 기준으로 잡는다.
    params = FilterParams.from_store(store, as_of=as_of, market=MARKET)
    base = tradable_universe(
        store, as_of=as_of, market=MARKET, params=params, equity=CAPITAL
    )
    entities = list(base.kept)
    print(f"현재 상수 {absolute/1e8:.1f}억 하한 통과: {len(entities)}종목")

    adv = adv20(store, as_of=as_of, entities=entities)
    # 최대 비중 종목이 안 잘릴 최소 ADV.
    cap_adv = max_pos / max_adv_ratio * CAPITAL
    print(f"최대비중({max_pos:.0%}) 종목이 안 잘릴 ADV = {cap_adv:,.0f}원 "
          f"(= {cap_adv/1e8:.1f}억)\n")

    print(f"{'배수':>6} {'하한(억)':>10} {'통과종목':>8} {'상수대비':>8} "
          f"{'용량충족':>8}")
    print("-" * 48)
    for mult in MULTIPLES:
        floor = mult * CAPITAL
        passing = adv[adv >= floor]
        n_pass = int(passing.size)
        # 용량 충족 = 그 종목에 최대 비중을 실어도 안 잘리는 종목.
        capacity_ok = int((passing >= cap_adv).sum())
        cap_rate = capacity_ok / n_pass if n_pass else 0.0
        delta = n_pass - len(entities)
        print(f"{mult:>6.1f} {floor/1e8:>10.1f} {n_pass:>8} {delta:>+8} "
              f"{cap_rate:>7.0%}")

    # ADV 분위수 — 하한을 어디에 두면 무엇이 남는지 감을 준다.
    print("\nADV20 분위수 (현재 유니버스, 억원):")
    for q in [0.1, 0.25, 0.5, 0.75, 0.9]:
        print(f"  p{int(q*100):>2} = {np.quantile(adv, q)/1e8:>8.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
