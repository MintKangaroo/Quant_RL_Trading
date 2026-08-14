"""하루치 세션 — shadow 운용의 실행기.

    uv run python tools/run_session.py                    # 마지막 거래일
    uv run python tools/run_session.py --day 2026-08-13

## 백테스트 루프를 그대로 쓴다

하루를 굴리는 코드는 이미 있다(`backtest/loop.py`). shadow 를 위해 두 번째
루프를 만들면 **shadow 와 백테스트가 다른 코드를 타게 되고**, 그때부터 둘의
성적 차이는 전략의 차이가 아니라 코드의 차이일 수 있다 (불변식 5).

그래서 이 도구가 하는 일은 구간을 하루로 잡아 같은 함수를 부르는 것뿐이다.
어제 낸 주문이 오늘 체결되고, 회계 스냅샷이 남고, 오늘 종가로 내일 주문이
만들어진다.

## 왜 샌드박스인가

shadow 는 **돈이 오가지 않는 운용**이다. 그 체결을 실전 창고의 ``trades`` 에
적으면 회계가 그것을 진짜 보유로 계산하고, 실제 입금이 시작되는 날 장부가
이미 오염돼 있다. append-only 창고에서 그건 되돌릴 수 없다.

읽기는 실제 창고를 그대로 본다(오버레이 링크). 실거래로 넘어갈 때 바꾸는
것은 ``--sandbox`` 를 빼는 것 하나다.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.backtest import loop  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market, trading_days  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import Store, overlay  # noqa: E402
from tools.backfill import build_store  # noqa: E402
from tools.run_backtest import JOURNAL  # noqa: E402

DEFAULT_SANDBOX = REPO_ROOT / "data" / "_shadow"


def last_trading_day(market: Market, today: date) -> date | None:
    """마지막 거래일. 휴장일에 돌려도 조용히 아무것도 안 하지 않는다."""
    days = trading_days(market, today - timedelta(days=14), today)
    return days[-1] if days else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", default="KR", choices=["KR", "US"])
    parser.add_argument("--day", help="세션 날짜 (기본: 마지막 거래일)")
    parser.add_argument("--sandbox", default=str(DEFAULT_SANDBOX))
    parser.add_argument(
        "--live-store",
        action="store_true",
        help="실제 창고에 쓴다. **실거래 자본이 들어온 뒤에만** 쓴다",
    )
    parser.add_argument(
        "--capital",
        type=float,
        default=0.0,
        help="첫 실행에 넣을 자본. 두 번째부터는 0 (창고가 중복을 거부한다)",
    )
    args = parser.parse_args(argv)

    load_env()
    source = build_store(None)
    if args.live_store:
        store = source
    else:
        layer = overlay.build(
            root=Path(args.sandbox), source=source.root, writable=JOURNAL
        )
        store = Store(root=layer.root)

    market = Market(args.market)
    day = (
        date.fromisoformat(args.day)
        if args.day
        else last_trading_day(market, LiveClock().now().date())
    )
    if day is None:
        print(f"{market} 거래일을 찾지 못했다.", file=sys.stderr)
        return 1

    print(f"{market} {day} · 창고 {store.root}")
    result = loop.run(
        store,
        start=day,
        end=day,
        market=args.market,
        capital=args.capital,
        warmup_days=0,
        # 신호는 일일 실행기가 실전 창고에 이미 쌓았다. 여기서 또 만들지 않는다.
        produce_signals=False,
    )
    for note in result.notes:
        print(f"  ⚠️  {note}")
    if not result.days:
        print("세션이 돌지 않았다 (거래일이 아니다).", file=sys.stderr)
        return 1

    entry = result.days[0]
    print(
        f"  NAV {entry.nav:,.0f} · 지수 {entry.index_value:.2f} · 낙폭 {entry.drawdown:.2%}\n"
        f"  후보 {len(entry.candidates)} · 주문 {entry.planned_orders} · 체결 {entry.filled}"
        + (f" · 차단 {entry.blocked_by}" if entry.blocked_by else "")
    )
    for note in entry.notes:
        print(f"    · {note}")
    # 차단은 사고가 아니다(안전장치가 일한 것이다). 그래도 종료코드로 알린다 —
    # shadow 10거래일 무사고를 사람이 로그를 뒤져 판정하게 두지 않는다.
    return 2 if entry.blocked_by else 0


if __name__ == "__main__":
    raise SystemExit(main())
