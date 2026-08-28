"""모의·실계좌로 나간 주문의 체결을 계좌에서 읽어 ``trades`` 에 적는다 (backtest.md §9).

    .venv/bin/python tools/reconcile_fills.py --market KR --sandbox data/_paper
    .venv/bin/python tools/reconcile_fills.py --market KR --sandbox data/_paper --day 2026-08-31

아침 세션(``run_session --live-broker``)이 보낸 주문은 ``orders.status = sent`` 로
남고 ``reason`` 에 ``broker_order_no=<번호>`` 가 있다. 이 도구는 그 번호로 t0425 를
조회해 실제 체결을 장부에 적는다. **시뮬레이션하지 않는다** — 체결가는 계좌가
말해 준다. ``execution.pending`` 은 ``sent`` 를 봉으로 체결시키지 않으므로, 이
도구가 안 돌면 그 주문은 장부에 영원히 없다. 그래서 종료코드가 말한다:

    0  전부 확인(체결·미체결·취소 중 하나로 확정)
    1  하나라도 "모른다"(조회 실패) — 다음 실행이 다시 본다
    2  대사할 주문이 없다 (오늘 세션이 안 돌았거나 sent 가 0건)
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.broker.fills import FillState, PendingFill, sync_fills  # noqa: E402
from quant_rl_trading.collectors.ls_client import LSClient, LSCredentials  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market  # noqa: E402
from quant_rl_trading.executor.pipeline import BROKER_ORDER_NO_PREFIX  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.schemas.order import Side  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import Store, overlay  # noqa: E402
from tools.backfill import build_store  # noqa: E402
from tools.run_backtest import JOURNAL  # noqa: E402
from tools.run_session import last_settled_day  # noqa: E402
from tools.verify_live_order import resolve_profile  # noqa: E402

ORDERS = "orders"
STATUS_SENT = "sent"


def pending_from_orders(store: Store, *, as_of: datetime, market: str, session_id: str) -> list[PendingFill]:
    """``sent`` 주문 → PendingFill. 주문번호가 없는 ``sent`` 는 대사할 수 없다 — 그 사실을 남긴다."""
    frame = store.get(ORDERS, as_of=as_of, lookback=7)
    if frame.empty:
        return []
    frame = frame[(frame["session_id"] == session_id) & (frame["status"] == STATUS_SENT)]
    out: list[PendingFill] = []
    for row in frame.itertuples(index=False):
        reason = str(getattr(row, "reason", "") or "")
        if not reason.startswith(BROKER_ORDER_NO_PREFIX):
            print(f"  ⚠️  {row.entity_id} slice {row.slice_seq}: sent 인데 주문번호가 없다 — 대사 불가", file=sys.stderr)
            continue
        out.append(
            PendingFill(
                order_id=f"{session_id}|{row.entity_id}|{row.slice_seq}",
                entity_id=str(row.entity_id),
                side=Side(str(row.side)),
                market=market,
                broker_order_no=reason[len(BROKER_ORDER_NO_PREFIX):],
                requested_quantity=float(row.quantity),
            )
        )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", default="KR", choices=["KR", "US"])
    parser.add_argument("--sandbox", default="data/_paper")
    parser.add_argument("--day", help="세션 날짜 (기본: 마지막 거래일 = 아침 세션이 결정한 날)")
    args = parser.parse_args(argv)

    load_env()
    clock = LiveClock()
    source = build_store(None)
    layer = overlay.build(root=Path(args.sandbox), source=source.root, writable=JOURNAL)
    store = Store(root=layer.root)
    market = Market(args.market)
    day = date.fromisoformat(args.day) if args.day else last_settled_day(store, market, clock.now())
    if day is None:
        print("거래일을 찾지 못했다.", file=sys.stderr)
        return 2
    session_id = f"{args.market}-{day.isoformat()}"
    now = clock.now()
    pending = pending_from_orders(store, as_of=now, market=args.market, session_id=session_id)
    print(f"{args.market} 세션 {session_id} · 창고 {store.root} · sent {len(pending)}건")
    if not pending:
        print("대사할 주문이 없다.")
        return 2

    profile = resolve_profile(store, market=args.market, as_of=now)
    credentials = LSCredentials.from_env(prefix=profile.env_prefix)
    print(f"계좌 — 모드 키 {profile.env_prefix} · 지문 {credentials.fingerprint or '(없음)'} · 선언 {credentials.kind or '(미선언)'}")
    client = LSClient(credentials=credentials, live_trading=True, min_interval_sec=profile.min_interval_sec)

    result = sync_fills(store, client, clock, as_of=now, pending=pending)
    unknown = 0
    for outcome in result.outcomes:
        if outcome.state is FillState.UNKNOWN:
            unknown += 1
            mark = "모른다"
        elif outcome.state is FillState.RECORDED:
            mark = "체결"
        else:
            mark = "변동없음"
        qty = outcome.fill.quantity if outcome.fill else outcome.cumulative_quantity
        price = f" @ {outcome.fill.price:,.0f}" if outcome.fill else ""
        print(f"  {mark:<4} {outcome.order_id} · {qty if qty is not None else '-'}주{price} {outcome.detail}")
    print(f"trades {result.rows_written}행 적재 · 모름 {unknown}건")
    return 1 if unknown else 0


if __name__ == "__main__":
    raise SystemExit(main())
