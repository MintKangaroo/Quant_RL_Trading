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
from quant_rl_trading.collectors import market_hours  # noqa: E402
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


def release_anchor(market: Market, *, recorded_at: datetime, now: datetime) -> datetime:
    """경과 시간의 기준점 — 조각이 기록된 시각과 **오늘 정규장 개장** 중 늦은 쪽.

    조각의 observed_at 은 세션 시계(데이터 날짜 16:00)라 다음 날 아침에는 이미
    "1,000분 경과" 다. 그대로 쓰면 개장 첫 회차에 전부 나간다(2026-09-02 실측 72건).
    """
    spec = market_hours.SPECS[market]
    here = market_hours.local_time(market, now)
    today_open = datetime.combine(here.date(), spec.regular_open, tzinfo=here.tzinfo)
    return max(recorded_at, today_open)


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

    market = Market(args.market)
    day = date.fromisoformat(args.day) if args.day else last_settled_day(
        store, market, now
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

    # 경과 시간의 기준점. 조각의 observed_at 은 **세션 시계**(데이터 날짜 16:00)라
    # 실제로 낸 벽시계가 아니다 — 09-01 세션을 09-02 08:40 에 돌리면 09:00 첫
    # 회차에 "1020분 경과" 가 되어 72건이 개장 단일가에 한꺼번에 나갔다(2026-09-02
    # 실측). 시간차 분할의 뜻은 **장중에 흩는 것**이므로 기준점은 그 조각이 기록된
    # 시각과 **오늘 정규장 개장** 중 늦은 쪽이다. 개장 전엔 어차피 체결이 없다.
    recorded_at = pd.Timestamp(pending["observed_at"].min()).to_pydatetime()
    anchor = release_anchor(market, recorded_at=recorded_at, now=now)
    elapsed = (now - anchor).total_seconds()

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
        f"  기준점({anchor:%H:%M}) 뒤 {elapsed / 60:.0f}분 경과 · 간격 {params.slice_interval_sec}초"
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
