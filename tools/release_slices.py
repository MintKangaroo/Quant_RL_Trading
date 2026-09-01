"""남은 조각 내보내기 — 08:40 세션이 낸 0번 조각 뒤로, 시간이 된 조각을 낸다.

``executor/orders.py`` 는 주문을 ``slice_count`` 조각으로 나누고 ``slice_interval_sec``
간격으로 낸다고 **문서에 적어 두고 실제로는 네 조각을 한꺼번에 냈다** (2026-09-01
발견 — ``slice_interval_sec`` 를 읽어 SliceParams 에 담아만 두고 아무도 안 썼다).
그 결과 82건이 전부 09:00 개장 단일가에서 체결됐다. 하루 중 스프레드가 가장 넓고
호가가 가장 얇은 순간이고, 우리 주문은 종목당 일거래대금의 0.9~2.4% 라 무시할
크기가 아니다 — 자본이 커지면 선형으로 나빠진다.

이 도구가 그 시간축이다. 세션은 0번 조각만 내보내고 나머지는 ``planned`` 로 남는데,
여기서 **시간이 된 것만** 골라 낸다. 안전은 ``submit_orders`` 의 ``submit-<order_id>``
멱등 가드가 지킨다 — 같은 조각을 두 번 내보내지 않는다.

    */20 9-14 · 평일   release_slices.py --market KR

``execution.slice_interval_sec <= 0`` 이면 세션이 이미 전부 냈으므로 할 일이 없다.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

from quant_rl_trading.broker import factory as broker_factory  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market  # noqa: E402
from quant_rl_trading.executor import orders as orders_module  # noqa: E402
from quant_rl_trading.executor.orders import PlannedOrder, SliceParams  # noqa: E402
from quant_rl_trading.executor.pipeline import submit_orders  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.schemas.order import Order, Side  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import Store, overlay  # noqa: E402
from tools.run_backtest import JOURNAL  # noqa: E402
from tools.run_session import build_store, last_settled_day  # noqa: E402

ORDERS = "orders"
STATUS_PLANNED = "planned"


def _planned_rows(store: Store, *, as_of: datetime, session_id: str, market: str) -> pd.DataFrame:
    """아직 안 나간 조각. **최신 revision 이 planned 인 것만.**

    같은 조각이 planned → submitting → sent 로 revision 을 올려 가며 쌓이므로,
    행 하나만 보고 "planned 다" 라고 하면 이미 나간 주문을 다시 낸다.
    """
    frame = store.get(ORDERS, as_of=as_of, lookback=7)
    if frame.empty:
        return frame
    frame = frame[
        (frame["session_id"] == session_id)
        & frame["entity_id"].astype(str).str.startswith(f"{market}:")
    ]
    if frame.empty:
        return frame
    frame = frame.sort_values("revision").drop_duplicates(
        subset=["entity_id", "slice_seq"], keep="last"
    )
    return frame[frame["status"] == STATUS_PLANNED]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", default="KR", choices=["KR", "US"])
    parser.add_argument("--sandbox", default="data/_paper")
    parser.add_argument("--day", help="세션 날짜 (기본: 마지막 거래일)")
    args = parser.parse_args(argv)

    load_env()
    clock = LiveClock()
    now = clock.now()
    source = build_store(None)
    layer = overlay.build(root=Path(args.sandbox), source=source.root, writable=JOURNAL)
    store = Store(root=layer.root)

    day = date.fromisoformat(args.day) if args.day else last_settled_day(
        store, Market(args.market), now
    )
    if day is None:
        print("거래일을 찾지 못했다.", file=sys.stderr)
        return 2
    session_id = f"{args.market}-{day.isoformat()}"

    params = SliceParams.from_store(store, as_of=now)
    if int(params.slice_interval_sec) <= 0:
        print("slice_interval_sec <= 0 — 세션이 이미 전부 냈다. 할 일이 없다.")
        return 0

    pending = _planned_rows(store, as_of=now, session_id=session_id, market=args.market)
    print(f"{args.market} 세션 {session_id} · 아직 안 나간 조각 {len(pending)}건")
    if pending.empty:
        return 0

    # 경과 시간은 **그 조각이 기록된 시각**(세션 시각)부터 잰다. 세션이 몇 시에
    # 돌았는지는 날마다 다를 수 있으므로 벽시계 08:40 을 가정하지 않는다.
    session_at = pd.Timestamp(pending["observed_at"].min()).to_pydatetime()
    elapsed = (now - session_at).total_seconds()

    planned: list[PlannedOrder] = []
    for row in pending.itertuples(index=False):
        limit = float(getattr(row, "limit_price", 0.0) or 0.0)
        planned.append(
            PlannedOrder(
                order=Order(
                    entity_id=str(row.entity_id),
                    side=Side(str(row.side)),
                    quantity=float(row.quantity),
                    limit_price=limit if limit > 0 else None,
                    reason="",
                ),
                order_id=orders_module.client_order_id(
                    session=session_id,
                    entity_id=str(row.entity_id),
                    slice_seq=int(row.slice_seq),
                ),
                session_id=session_id,
                slice_seq=int(row.slice_seq),
                target_weight=float(getattr(row, "target_weight", 0.0) or 0.0),
            )
        )

    due = orders_module.due_slices(planned, params=params, elapsed_sec=elapsed)
    print(
        f"  세션 뒤 {elapsed / 60:.0f}분 경과 · 간격 {params.slice_interval_sec}초"
        f" → 지금 낼 조각 {len(due)}건"
    )
    if not due:
        return 0

    broker, why = broker_factory.build_broker(store, market=args.market, as_of=now)
    print(f"  브로커: {why}")
    acks = submit_orders(
        store, clock, broker, planned=due, as_of=now, market=args.market
    )
    sent = sum(1 for a in acks if getattr(a, "sent", False))
    print(f"전송 {sent}건 / 시도 {len(due)}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
