"""미장 전용 shadow 장부에 **달러** 첫 자본을 넣는다 — 원화 입금은 미장 주문가능금액(달러 현금)이 0 이라 주문이 안 나간다.

    .venv/bin/python tools/fund_shadow_usd.py --sandbox data/_ar_us_shadow --usd 370000          # 점검만
    .venv/bin/python tools/fund_shadow_usd.py --sandbox data/_ar_us_shadow --usd 370000 --apply

G1 미장 shadow(9/25)가 겪은 첫 세션 주문 0(portfolio-construction.md '미장 G1 트랙' ①)을 되풀이하지 않으려고 만든 도구다.
`tools/run_session.py --capital` 과 **같은 시각 규칙**으로 넣는다 — 그 세션이 워밍업으로 같이 도는 전날 세션의 스냅샷 시각 − 1일
(`backtest/loop.run` 의 `seed_capital` 호출과 같은 값, 같은 run id `backtest-seed-<날짜>`). 다른 것은 통화(USD)뿐이다.
금액 기본값 $370,000 은 미장 슬리브·G1 장부와 같다(≈ 국장 장부 5.03억).

이미 입출금 기록이 하나라도 있으면 넣지 않는다(rc 3) — 두 번 넣으면 입금일 NAV 가 수익으로 잡힌다(accounting.md §6).
rc: 0 넣었다(또는 점검) · 1 세션 없음 · 3 이미 자본이 있다.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.backtest import loop  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market, previous_trading_day  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock, ReplayClock  # noqa: E402
from quant_rl_trading.store import Store, overlay  # noqa: E402

DEFAULT_USD = 370_000.0


def opening_moment(store: Store, market: Market, now: datetime) -> datetime | None:
    """run_session 이 그 시각에 돌면 넣었을 입금 시각 — 워밍업(전날) 세션 스냅샷 − 1일."""
    from tools.run_session import last_settled_day

    day = last_settled_day(store, market, now)
    if day is None:
        return None
    warm = previous_trading_day(market, day)
    probe = datetime.combine(warm, loop.DEFAULT_SNAPSHOT_TIME, tzinfo=loop.SEOUL)
    first = loop.snapshot_moment(store, warm, as_of=probe, market=market)
    return loop.snapshot_moment(store, warm, as_of=first, market=market) - timedelta(days=1)


def fund(store: Store, *, usd: float, now: datetime, apply: bool, market: Market = Market.US) -> int:
    flows = store.get(loop.CAPITAL_FLOWS, as_of=now, lookback=3650)
    if not flows.empty:
        print(f"이미 입출금 기록 {len(flows)}행 — 넣지 않는다", flush=True)
        return 3
    opening = opening_moment(store, market, now)
    if opening is None:
        print(f"{market} 세션을 찾지 못했다", flush=True)
        return 1
    print(f"  {'입금' if apply else '입금 예정'} USD {usd:,.0f} · valid_from = observed_at = {opening.isoformat()}", flush=True)
    if apply:
        loop.seed_capital(store, ReplayClock(opening), amount=usd, as_of=opening, currency="USD")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sandbox", required=True)
    ap.add_argument("--usd", type=float, default=DEFAULT_USD)
    ap.add_argument("--root", default="data", help="원천(실전) 창고 — 샌드박스가 읽기 전용으로 겹쳐 본다")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    if not str(args.sandbox).rstrip("/").endswith("_shadow"):
        print(f"{args.sandbox}: shadow 샌드박스(…_shadow)에만 넣는다", file=sys.stderr)
        return 2
    from tools.run_backtest import JOURNAL

    layer = overlay.build(root=Path(args.sandbox), source=Path(args.root), writable=JOURNAL)
    return fund(Store(root=layer.root), usd=args.usd, now=LiveClock().now(), apply=args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
