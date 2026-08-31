"""미체결 추격 — 낸 주문을 장중에 지켜보고 재호가·취소한다.

``executor/supervise.py`` 는 "아직 아무도 안 부른다"는 경고를 달고 있었다.
그 결과 08:40 세션이 낸 지정가 주문의 미체결 잔량은 **장 끝까지 방치돼
만료**됐다 (2026-08-31: 8/27 주문 70건이 그렇게 사라졌고, 계좌 현금이 87% 로
굳은 원인 중 하나였다). 이 도구가 그 배선이다:

  1. 오늘 세션의 ``sent`` 주문을 창고에서 읽고
  2. 계좌 체결을 대사해 잔량을 확정하고 (trades 적재 = 대시보드도 최신화)
  3. ``lifecycle.decide`` 로 재호가/취소/포기를 판단해 브로커에 낸다
     — 재호가는 시세를 쫓되 **원 기준가 대비 슬리피지 상한**(execution.max_slippage)
     안에서만. 상한을 넘으면 포기(ABANDON)한다.

``--close`` 는 마감 직전용이다: 판단 없이 남은 미체결을 전부 취소한다
(미체결 이월 없음 — lifecycle.close_session 과 같은 규칙).

한계: 이 도구는 회차마다 새로 떠서 ``retry_count`` 를 0 부터 센다. 그래서
max_retries 는 **한 회차 안**의 상한이고, 회차 간 상한은 크론 간격과 슬리피지
상한이 맡는다. 타이머(retry_after_sec)는 주문 행의 observed_at 에서 잰다.

    */20 9-14 * * 1-5  chase_orders.py --market KR   # 장중 재호가
    20 15 * * 1-5      chase_orders.py --market KR --close  # 마감 전 취소
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from quant_rl_trading.broker import factory as broker_factory  # noqa: E402
from quant_rl_trading.broker.fills import sync_fills  # noqa: E402
from quant_rl_trading.dashboard.services.account import _client  # noqa: E402
from quant_rl_trading.dashboard.services.live_quotes import LiveQuoteCache  # noqa: E402
from quant_rl_trading.executor import supervise  # noqa: E402
from quant_rl_trading.executor.lifecycle import (  # noqa: E402
    LifecycleParams,
    OpenOrder,
    OrderStatus,
)
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.schemas.order import Side  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import Store, overlay  # noqa: E402
from tools.reconcile_fills import BROKER_ORDER_NO_PREFIX, pending_from_orders  # noqa: E402
from tools.run_backtest import JOURNAL  # noqa: E402
from tools.run_session import build_store, last_settled_day  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market  # noqa: E402

ORDERS = "orders"
STATUS_SENT = "sent"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", default="KR", choices=["KR"])  # 미장은 t8407 이 없다
    parser.add_argument("--sandbox", default="data/_paper")
    parser.add_argument("--close", action="store_true", help="판단 없이 미체결 전부 취소 (마감 전)")
    args = parser.parse_args(argv)

    load_env()
    clock = LiveClock()
    now = clock.now()
    source = build_store(None)
    layer = overlay.build(root=Path(args.sandbox), source=source.root, writable=JOURNAL)
    store = Store(root=layer.root)

    day = last_settled_day(store, Market(args.market), now)
    if day is None:
        print("거래일을 찾지 못했다.", file=sys.stderr)
        return 2
    session_id = f"{args.market}-{day.isoformat()}"

    # 1) 오늘 세션의 sent 주문만 추격한다 — 지난 세션 주문은 브로커에서 이미
    #    만료돼 정정·취소를 낼 대상이 아니다(대사가 UNKNOWN 으로 확인해 준다).
    pending = [
        item
        for item in pending_from_orders(store, as_of=now, market=args.market, session_id=session_id)
        if item.order_id.startswith(f"{session_id}|")
    ]
    print(f"{args.market} 세션 {session_id} · 추격 대상 sent {len(pending)}건")
    if not pending:
        print("추격할 주문이 없다.")
        return 0

    # 2) 체결 대사 — 잔량을 확정하고 trades 도 최신으로 적는다.
    client = _client(store, as_of=now, market=args.market)
    result = sync_fills(store, client, clock, as_of=now, pending=pending)
    cumulative = supervise.cumulative_from_sync(result)
    print(f"  대사: trades {result.rows_written}행 적재 · 체결상태 아는 주문 {len(cumulative)}건")

    # 3) 주문 행 → OpenOrder 재구성. reference_price 는 원 지정가 — 슬리피지
    #    상한은 그 값 대비로 잰다(재호가마다 기준을 옮기면 상한이 무의미해진다).
    frame = store.get(ORDERS, as_of=now, lookback=7)
    frame = frame[(frame["session_id"] == session_id) & (frame["status"] == STATUS_SENT)]
    open_orders: list[OpenOrder] = []
    for row in frame.itertuples(index=False):
        reason = str(getattr(row, "reason", "") or "")
        if not reason.startswith(BROKER_ORDER_NO_PREFIX):
            continue
        limit = float(getattr(row, "limit_price", 0.0) or 0.0)
        if limit <= 0:
            continue  # 시장가는 즉시 종결이라 추격할 게 없다
        quantity = int(float(row.quantity))
        submitted_at = pd.Timestamp(row.observed_at).to_pydatetime()
        open_orders.append(
            OpenOrder(
                order_id=f"{session_id}|{row.entity_id}|{row.slice_seq}",
                entity_id=str(row.entity_id),
                side=Side(str(row.side)),
                reference_price=limit,
                limit_price=limit,
                original_quantity=quantity,
                remaining_quantity=quantity,
                retry_count=0,
                last_action_at=submitted_at,
                status=OrderStatus.SUBMITTED,
                broker_order_no=reason[len(BROKER_ORDER_NO_PREFIX):],
            )
        )
    if not open_orders:
        print("지정가 sent 주문이 없다.")
        return 0

    # 4) 브로커 — factory 가 모드·지문·live_trading 게이트를 전부 지킨다.
    broker, why = broker_factory.build_broker(store, market=args.market, as_of=now)
    print(f"  브로커: {why}")

    if args.close:
        outcome = supervise.close(open_orders, broker, now=now)
    else:
        quotes = LiveQuoteCache(lambda: client).get([o.entity_id for o in open_orders])
        prices = {eid: q.price for eid, q in quotes.items() if q.price > 0}
        params = LifecycleParams.from_store(store, as_of=now)
        outcome = supervise.step(
            open_orders,
            broker,
            now=now,
            market_prices=prices,
            cumulative_filled=cumulative,
            params=params,
        )

    # 종결된 주문은 상태를 revision 으로 되적는다 — 안 적으면 status 가 sent 로
    # 영영 남아, 다음 회차가 이미 끝난 주문에 또 취소를 내고 01433("정정/취소할
    # 수량이 없습니다")을 매번 받는다 (2026-08-31 실측). pipeline 의
    # _record_submit_result(revision=2) 와 같은 관용구, 그 위 revision=3.
    terminal = {
        o.order_id: o.status.value
        for o in outcome.orders
        if o.status in (OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.ABANDONED)
    }
    if terminal:
        by_id = {
            f"{session_id}|{r.entity_id}|{r.slice_seq}": r for r in frame.itertuples(index=False)
        }
        rows = []
        for order_id, status in terminal.items():
            src = by_id.get(order_id)
            if src is None:
                continue
            row = {c: getattr(src, c) for c in frame.columns}
            row["status"] = status
            row["revision"] = int(getattr(src, "revision", 2) or 2) + 1
            row["observed_at"] = now
            rows.append(row)
        run_id = f"chase-final-{session_id}-{now:%Y%m%dT%H%M%S}"
        if rows and not store.ingest_run_recorded(ORDERS, run_id):
            store.append(ORDERS, rows, ingest_run_id=run_id, source="chase_orders")
            print(f"  상태 되적음: {len(rows)}건 ({', '.join(sorted(set(terminal.values())))})")

    for action in outcome.actions:
        o = action.order
        print(f"  {action.type.value:8s} {o.order_id} 잔량 {o.remaining_quantity} @ {o.limit_price:,.0f} — {action.reason or ''}")
    for order_id, why_skip in outcome.skipped:
        print(f"  건너뜀   {order_id} — {why_skip}")
    for order_id, err in outcome.errors:
        print(f"  실패     {order_id} — {err}", file=sys.stderr)
    filled = sum(1 for o in outcome.orders if o.status is OrderStatus.FILLED)
    print(
        f"조치 {len(outcome.actions)} · 체결종결 {filled} · 건너뜀 {len(outcome.skipped)}"
        f" · 실패 {len(outcome.errors)} · 계속 지켜볼 것 {len(outcome.open)}"
    )
    return 1 if outcome.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
