"""어제 낸 주문을 오늘 종가에 맞춘다 — 백테스트의 체결 단계.

**결정은 D, 체결은 D+1 이다** (backtest.md §1). 같은 날 결정하고 같은 날
체결시키면 그 종가를 보고 그 종가에 사는 것이 되고, 그건 백테스트에서 가장
흔한 미래 훔쳐보기다.

체결 결과는 ``trades`` 로 창고에 남긴다. 회계가 그 기록에서 장부를 다시 접기
때문에, 백테스트는 자기 포지션을 따로 들고 다니지 않는다 (backtest.md §3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

import pandas as pd

from quant_rl_trading.accounting.book import KRW, USD
from quant_rl_trading.accounting.book import Side as BookSide
from quant_rl_trading.accounting.rates import Rates
from quant_rl_trading.backtest import market as market_module
from quant_rl_trading.replay.fills import Fill, FillParams, FillStatus, simulate_fill
from quant_rl_trading.schemas.order import Order, Side
from quant_rl_trading.store import DuplicateIngestRun

if TYPE_CHECKING:
    from quant_rl_trading.replay.clock import Clock
    from quant_rl_trading.store import Store

ORDERS = "orders"
TRADES = "trades"
SOURCE = "backtest"


@dataclass
class ExecutionDay:
    """하루치 체결 결과."""

    session_id: str
    fills: tuple[Fill, ...] = ()
    requested: int = 0
    filled: int = 0
    traded_value: float = 0.0
    rows_written: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def fill_rate(self) -> float:
        """체결률. 못 산 것을 산 것으로 치지 않았는지 보는 눈이다."""
        return self.filled / self.requested if self.requested else 0.0


def currency_of(market: str) -> str:
    return USD if market.upper() == "US" else KRW


def pending(store: Store, *, as_of: datetime, session_id: str) -> pd.DataFrame:
    """직전 세션이 낸 주문. **미체결은 이월하지 않는다** (backtest.md §1).

    창고에서 읽는다 — 메모리에 들고 있으면 이어 돌리기가 불가능하고, 재시작한
    백테스트는 자기가 낸 주문을 알아볼 수 없다.
    """
    frame = store.get(ORDERS, as_of=as_of, lookback=7)
    if frame.empty:
        return frame
    return frame[frame["session_id"] == session_id]


def _aggregate(orders: pd.DataFrame) -> list[tuple[Order, str]]:
    """분할 조각을 종목·방향별 한 건으로 합친다 (backtest.md §2).

    조각마다 따로 체결시키면 거래대금 상한이 조각 수만큼 곱해져 **하루에
    유동성의 몇 배를 삼킬 수 있게 된다.**

    지정가는 조각들이 같은 기준가에서 나오므로 원래 같은 값이다. 그래도
    매수는 가장 낮은 값, 매도는 가장 높은 값을 골라 둔다 — 다를 일이 생긴다면
    그건 체결이 더 어려운 쪽이고, 어려운 쪽으로 틀리는 편이 안전하다.
    """
    out: list[tuple[Order, str]] = []
    grouped = orders.groupby(["entity_id", "side", "session_id"], sort=True)
    for (entity_id, side_name, session_id), group in grouped:
        side = Side(str(side_name))
        limits = group["limit_price"].dropna()
        if len(limits) < len(group):
            # 한 조각이라도 시장가면 묶음 전체를 시장가로 본다 — 청산·킬스위치
            # 경로가 그렇게 만든다.
            limit = None
        else:
            limit = float(limits.min() if side is Side.BUY else limits.max())
        out.append(
            (
                Order(
                    entity_id=str(entity_id),
                    side=side,
                    quantity=int(group["quantity"].sum()),
                    limit_price=limit,
                ),
                f"{session_id}|{entity_id}|{side}",
            )
        )
    return out


def run(
    store: Store,
    clock: Clock,
    *,
    as_of: datetime,
    market: str,
    session_id: str,
) -> ExecutionDay:
    """직전 세션의 주문을 오늘 봉에 맞춰 체결시키고 ``trades`` 에 적는다."""
    result = ExecutionDay(session_id=session_id)
    orders = pending(store, as_of=as_of, session_id=session_id)
    if orders.empty:
        return result

    requests = _aggregate(orders)
    entities = sorted({order.entity_id for order, _ in requests})
    states = market_module.states(
        store, as_of=as_of, entities=entities, market=market
    )
    params = FillParams.from_store(store, as_of=as_of)
    rates = Rates.from_store(store, as_of=as_of)
    currency = currency_of(market)

    fills: list[Fill] = []
    rows: list[dict[str, object]] = []
    observed_at = clock.now()

    for order, order_id in requests:
        result.requested += order.quantity
        state = states.get(order.entity_id)
        if state is None:
            # 그날 봉이 없다 — 거래정지이거나 상장폐지다. 직전 종가로 때우면
            # 정지 중에 사고파는 백테스트가 된다.
            result.notes.append(f"{order.entity_id}: 체결일 시세 없음")
            continue
        fill = simulate_fill(order, state, params)
        fills.append(fill)
        if fill.status is FillStatus.REJECTED or fill.filled_quantity <= 0:
            continue

        gross = fill.filled_quantity * fill.avg_price
        book_side = BookSide(str(fill.side))
        fee, tax = rates.costs(side=book_side, gross=gross, currency=currency)
        result.filled += fill.filled_quantity
        result.traded_value += gross
        rows.append(
            {
                "entity_id": fill.entity_id,
                "valid_from": as_of,
                "observed_at": observed_at,
                "source": SOURCE,
                "market": market,
                "side": str(fill.side),
                "quantity": float(fill.filled_quantity),
                "price": fill.avg_price,
                "currency": currency,
                "fee": fee,
                "tax": tax,
                "order_id": order_id,
            }
        )

    result.fills = tuple(fills)
    if rows:
        run_id = f"backtest-trades-{session_id}"
        if not store.ingest_run_recorded(TRADES, run_id):
            try:
                result.rows_written = int(
                    store.append(TRADES, rows, ingest_run_id=run_id, source=SOURCE)
                )
            except DuplicateIngestRun:
                result.rows_written = 0
    return result
