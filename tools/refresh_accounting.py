#!/usr/bin/env python
"""늦게 온 종가로 **회계 스냅샷만** 다시 찍는다.

    uv run python tools/refresh_accounting.py --market KR
    uv run python tools/refresh_accounting.py --market KR --root data/_shadow

## 왜 필요한가

국장 일봉은 장이 끝나고 한참 뒤에 나온다. 그런데 세션은 16:00 에 돈다
(`loop.snapshot_moment` — 회계 15:40 과 신호 공표 16:00 중 늦은 쪽). 그
시각에는 창고에 **오늘 종가가 없어서** 어제 종가로 평가하고, 그 값이 오늘
행으로 박힌다.

실측 2026-08-19 16:34(shadow):

    08-18 16:00   9,881,077  -1.77%
    08-19 16:00   9,881,077   0.00%   <- 8/18 값 그대로
    prices 최신 = 2026-08-18 (오늘 0행)

화면에는 "오늘 수익률 0.00%" 로 나온다. **안 움직인 것이 아니라 아직
모르는 것**인데 둘이 같은 모양이다.

22:40 에 일봉을 다시 긁는 크론이 이미 있다(`collect_daily.sh`). 빠진 것은
**그 뒤에 회계를 다시 계산하는 일**뿐이다.

## 세션을 다시 돌리지 않는 이유

`run_session.py` 를 다시 돌리면 신호·후보·주문 결정까지 전부 다시 돈다.
NAV 하나를 고치려고 결정 경로를 다시 밟을 이유가 없고, 그 경로에는 주문이
있다. **고치려는 것만 고친다.**

## 두 번 돌려도 안전하다

`snapshot.write` 는 값이 달라졌을 때만 revision 을 올린 정정본을 얹는다
(불변식 4). 같으면 0행을 쓴다 — 그래서 몇 번을 돌려도 행이 안 쌓인다.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.accounting import benchmark as benchmark_module  # noqa: E402
from quant_rl_trading.accounting import ledger as ledger_module  # noqa: E402
from quant_rl_trading.accounting import snapshot as snapshot_module  # noqa: E402
from quant_rl_trading.accounting.rates import Rates  # noqa: E402
from quant_rl_trading.backtest import loop as loop_module  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market  # noqa: E402
from quant_rl_trading.replay.clock import ReplayClock  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402


def refresh(store: Store, *, market: str, day: datetime) -> tuple[int, float]:
    """그날 스냅샷을 다시 계산해 쓴다. (쓴 행수, NAV) 를 돌려준다."""
    # **세션과 같은 시각을 쓴다.** 여기서 다른 시각을 쓰면 같은 날에 대해
    # 두 개의 자연키가 생겨 곡선이 두 줄이 된다.
    as_of = loop_module.snapshot_moment(store, day.date(), as_of=day)
    clock = ReplayClock(as_of)
    rates = Rates.from_store(store, as_of=as_of)
    book = ledger_module.build_book(store, as_of=as_of, rates=rates)
    snapshot = snapshot_module.take(store, clock, as_of=as_of, book=book)
    benchmark = benchmark_module.level(
        store, as_of=as_of, fx_rate=snapshot.valuation.fx_rate
    )
    written = snapshot_module.write(store, clock, snapshot=snapshot, benchmark=benchmark)
    return written, float(snapshot.valuation.nav)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", default="KR")
    parser.add_argument("--root", default="data", help="창고 경로 (shadow 는 data/_shadow)")
    parser.add_argument(
        "--day", default=None, help="YYYY-MM-DD (생략하면 오늘)"
    )
    args = parser.parse_args(argv)

    if args.day:
        day = datetime.fromisoformat(args.day).replace(tzinfo=UTC)
    else:
        # 크론이 부르는 도구다. 되감아 부를 수단(--day)을 열어 뒀다.
        day = datetime.now(UTC)  # invariant-allow: wallclock

    store = Store(root=Path(args.root))
    market = Market(args.market)
    written, nav = refresh(store, market=market.value, day=day)
    state = "정정본 적재" if written else "변화 없음"
    print(f"{args.root} {market.value} {day:%Y-%m-%d} — {state} · NAV {nav:,.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
