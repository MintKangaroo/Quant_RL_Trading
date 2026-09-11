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

조치 intent를 전송 전에 영구 기록하고, 재시작 시 재시도 횟수·타이머·확인된
정정 주문번호를 복원한다. ACK만 받은 조치는 UNKNOWN으로 유지한다.
``watch_order_events.py``가 인증된 KR 확인 이벤트를 받아야 후속 조치를 허용한다.

    20,40 9 · */20 10-14 · 평일   chase_orders.py --market KR   # 장중 재호가
    # **09:00 에는 안 돈다** — 시가 단일가가 막 체결되는 순간이라 주문 상태가
    # 요동친다. 2026-09-01 09:00 실측: 82건 중 52건이 01433("정정할 수량 없음"),
    # 5건이 01442(정정수량 초과)였다. 09:20 부터 시작한다.
    20 15 * * 1-5      chase_orders.py --market KR --close  # 마감 전 취소
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from quant_rl_trading.broker import factory as broker_factory
from quant_rl_trading.broker.fills import sync_fills
from quant_rl_trading.collectors.market_hours import Market
from quant_rl_trading.dashboard.services.account import _client
from quant_rl_trading.dashboard.services.live_quotes import LiveQuoteCache
from quant_rl_trading.executor import guards, supervise
from quant_rl_trading.executor.action_journal import ActionJournal, refresh_order_states
from quant_rl_trading.executor.lifecycle import (
    LifecycleParams,
    OpenOrder,
    OrderStatus,
)
from quant_rl_trading.executor.pipeline import withdraw_unsent
from quant_rl_trading.replay.clock import LiveClock
from quant_rl_trading.risk import account as account_risk
from quant_rl_trading.store.errors import StoreError
from quant_rl_trading.risk.budget import Reservation
from quant_rl_trading.schemas.order import Side
from quant_rl_trading.settings import load_env
from quant_rl_trading.store import Store, overlay
from quant_rl_trading.store.locking import account_lock
from tools.reconcile_fills import BROKER_ORDER_NO_PREFIX, pending_from_orders
from tools.run_backtest import JOURNAL
from tools.run_session import build_store, last_settled_day

ORDERS = "orders"
STATUS_SENT = "sent"
#: LS 응답코드 — "정정/취소할 수량이 없습니다". 주문이 이미 체결·소멸했다는 뜻이라
#: 오류가 아니라 **상태 정보**로 읽는다.
BROKER_ORDER_GONE = "01433"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", default="KR", choices=["KR"])  # 미장은 t8407 이 없다
    parser.add_argument("--sandbox", default="data/_paper")
    parser.add_argument("--close", action="store_true", help="판단 없이 미체결 전부 취소 (마감 전)")
    args = parser.parse_args(argv)

    load_env()
    clock = LiveClock()
    source = build_store(None)
    layer = overlay.build(root=Path(args.sandbox), source=source.root, writable=JOURNAL)
    store = Store(root=layer.root)
    with account_lock(store.root):
        return _chase(args, store, clock)


def check_account(store, clock, *, order: OpenOrder, market: str):
    now = clock.now()
    gate = guards.check_pretrade(
        store, as_of=now, market=market, entity_id=order.entity_id, side=order.side
    )
    if not gate:
        return gate
    try:
        budget = account_risk.read(store, clock, as_of=now)
        slip = float(store.config("execution.max_slippage", as_of=now))
        price = (
            order.reference_price * (1.0 + slip) if order.side == Side.BUY else order.limit_price
        )
        decision = budget.check(Reservation(
            order.order_id, order.entity_id, str(order.side), order.remaining_quantity,
            price, "USD" if market == "US" else "KRW",
        ))
        return guards.GateResult(bool(decision), decision.reason)
    except (LookupError, ValueError, StoreError) as exc:
        return guards.GateResult(False, f"risk: account unavailable ({exc})")


def _chase(args, store: Store, clock) -> int:
    refresh_order_states(store, clock)
    now = clock.now()

    day = last_settled_day(store, Market(args.market), now)
    if day is None:
        print("거래일을 찾지 못했다.", file=sys.stderr)
        return 2
    session_id = f"{args.market}-{day.isoformat()}"
    if args.close:
        released = withdraw_unsent(store, clock, session=session_id, market=args.market)
        print(f"  미전송 조각 철회 {released}건")

    # 오늘 세션만 추격한다. 과거 주문은 날짜 지정 대사 전까지 미확정으로 남긴다.
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
    frame = store.get(ORDERS, as_of=clock.now(), lookback=7)
    frame = frame[(frame["session_id"] == session_id) & (frame["status"].isin([STATUS_SENT, "cancel_unknown", "modify_unknown"]))]
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
                status=(OrderStatus(str(row.status)) if str(row.status) != STATUS_SENT else OrderStatus.SUBMITTED),
                broker_order_no=reason[len(BROKER_ORDER_NO_PREFIX):],
            )
        )
    if not open_orders:
        print("지정가 sent 주문이 없다.")
        return 0

    # 4) 브로커 — factory 가 모드·지문·live_trading 게이트를 전부 지킨다.
    broker, why = broker_factory.build_broker(store, market=args.market, as_of=now)
    print(f"  브로커: {why}")
    journal = ActionJournal(store, clock, broker)
    open_orders = [journal.restore(order) for order in open_orders]

    if args.close:
        outcome = supervise.close(open_orders, broker, now=now, dispatch=journal.dispatch)
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
            dispatch=journal.dispatch,
            pretrade_check=lambda order: check_account(
                store, clock, order=order, market=args.market
            ),
        )

    # 종결 또는 미확정 상태를 revision으로 되적는다. unknown은 대사 대상으로 남긴다.
    # 안 적으면 status 가 sent 로
    # 영영 남아, 다음 회차가 이미 끝난 주문에 또 취소를 내고 01433("정정/취소할
    # 수량이 없습니다")을 매번 받는다 (2026-08-31 실측). pipeline 의
    # _record_submit_result(revision=2) 와 같은 관용구, 그 위 revision=3.
    terminal = {
        o.order_id: o.status.value
        for o in outcome.orders
        if o.status in (OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.ABANDONED,
                        OrderStatus.CANCEL_UNKNOWN, OrderStatus.MODIFY_UNKNOWN)
    }
    # 브로커가 "정정/취소할 수량이 없다"(01433) 고 하면 그 주문은 계좌에서 이미
    # 끝난 것이다 — 체결 조회가 아직 안 따라왔을 뿐이다. 종결로 적지 않으면 다음
    # 회차가 같은 주문에 또 재호가를 내고 같은 오류를 받는다 (2026-09-01 09:00:
    # 52건이 그랬다). **브로커의 이 응답이 우리 장부보다 최신이다.**
    #
    # 01442(정정수량이 정정가능수량 초과)는 다르다 — 일부는 아직 살아 있다는 뜻이라
    # 종결로 적지 않는다. 다음 회차가 체결을 다시 대사하면 잔량이 맞아 든다.
    # 01433("정정/취소할 수량 없음")은 우리가 재호가하려던 사이에 브로커가 다 채운 경우다.
    # 전에는 그냥 FILLED 로 적었는데, 그러면 trades 에 체결이 없는 채로 종결돼 15:45 대사가
    # 브로커 **평균매입단가**로 정정하게 된다(2026-09-07 실측: 6종목 매도대금 +6만 원 과대,
    # 수수료·세금 0). 그래서 그 주문들만 체결을 한 번 더 조회해 실제 체결가로 trades 에
    # 적고, 체결을 확인한 것만 FILLED 로 적는다. 못 찾으면 unknown으로 대사에 맡긴다.
    gone = [order_id for order_id, message in outcome.errors if BROKER_ORDER_GONE in message]
    if gone:
        again = sync_fills(
            store, client, clock, as_of=now, pending=[p for p in pending if p.order_id in gone]
        )
        known = supervise.cumulative_from_sync(again)
        print(f"  이미종결 되찾기: trades {again.rows_written}행 적재 · 체결 확인 {len(known)}/{len(gone)}건")
        requested = {p.order_id: float(p.requested_quantity) for p in pending}
        for order_id in gone:
            # 0주 "확인" 은 확인이 아니다 — 정정 사슬을 못 따라갔을 때 그렇게 보였다(2026-09-08 KR:081660).
            if order_id in known and known[order_id] >= requested.get(order_id, float("inf")):
                terminal[order_id] = OrderStatus.FILLED.value
            else:
                print(f"  미확정 {order_id} — 체결 {known.get(order_id, '?')}주 < 요청 {requested.get(order_id, '?')}주, unknown 유지·대사 필요")
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
        if BROKER_ORDER_GONE in err:
            print(f"  이미종결 {order_id} — 브로커에 남은 수량 없음(01433)")
        else:
            print(f"  실패     {order_id} — {err}", file=sys.stderr)
    filled = sum(1 for o in outcome.orders if o.status is OrderStatus.FILLED)
    print(
        f"조치 {len(outcome.actions)} · 체결종결 {filled} · 건너뜀 {len(outcome.skipped)}"
        f" · 실패 {len(outcome.errors)} · 계속 지켜볼 것 {len(outcome.open)}"
    )
    # 01433 은 오류가 아니라 "이미 끝났다" 는 사실이라 rc 를 올리지 않는다.
    real_errors = [e for e in outcome.errors if BROKER_ORDER_GONE not in e[1]]
    unresolved = any(o.status in supervise.UNRESOLVED for o in outcome.orders
                     if terminal.get(o.order_id) != OrderStatus.FILLED.value)
    return 1 if real_errors or unresolved else 0


if __name__ == "__main__":
    raise SystemExit(main())
