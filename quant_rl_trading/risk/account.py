"""Point-in-time adapter from the fill/order journals to the pure budget engine."""

from __future__ import annotations

import math
from datetime import datetime

import pandas as pd

from quant_rl_trading.accounting import ledger, snapshot
from quant_rl_trading.accounting.rates import Rates
from quant_rl_trading.executor.action_journal import cancelled_quantities
from quant_rl_trading.executor.orders import PlannedOrder, client_order_id
from quant_rl_trading.replay.clock import Clock
from quant_rl_trading.risk.budget import Budget, Limits, Reservation
from quant_rl_trading.store import Store

RESERVING = frozenset(
    {"reserved", "paper", "submitting", "sent", "cancel_unknown", "modify_unknown"}
)
# Old terminal labels do not prove that a broker's unfilled remainder is cancelled.
UNVERIFIED_TERMINAL = frozenset({"cancelled", "expired", "abandoned", "filled"})


def key(session: str, entity: str, seq: int) -> str:
    return f"{session}|{entity}|{seq}"


def for_order(item: PlannedOrder, *, market: str, slippage: float) -> Reservation:
    if market not in {"KR", "US"} or not item.order.entity_id.startswith(f"{market}:"):
        raise ValueError("order market does not match instrument")
    price = float(item.order.limit_price or math.nan)
    if item.order.side == "buy":
        if not math.isfinite(slippage) or slippage < 0:
            raise ValueError("invalid repricing bound")
        price *= 1.0 + slippage
    return Reservation(
        key(item.session_id, item.order.entity_id, item.slice_seq),
        item.order.entity_id,
        str(item.order.side),
        item.order.quantity,
        price,
        "USD" if market == "US" else "KRW",
    )


def filled_quantities(store: Store, *, as_of: datetime) -> dict[str, float]:
    """Actual fills, including history older than an order polling window."""
    filled: dict[str, float] = {}
    for record in store.get("trades", as_of=as_of).to_dict(orient="records"):
        base = str(record["order_id"]).split("#", 1)[0]
        filled[base] = filled.get(base, 0.0) + float(record["quantity"])
    return filled


def read(store: Store, clock: Clock, *, as_of: datetime) -> Budget:
    store = store.execution_view()
    rates = Rates.from_store(store, as_of=as_of)
    book = ledger.build_book(store, as_of=as_of, rates=rates)
    limits = Limits(
        max_position=float(store.config("allocator.max_position_weight", as_of=as_of)),
        max_exposure=float(store.config("risk.max_gross_exposure", as_of=as_of)),
        max_positions=int(store.config("risk.max_positions", as_of=as_of)),
        max_daily_loss=float(store.config("risk.max_daily_loss", as_of=as_of)),
        max_drawdown=float(store.config("killswitch.drawdown_trigger", as_of=as_of)),
    )
    budget = Budget(
        nav=0,
        fx=0,
        cash={},
        holdings={e: p.quantity for e, p in book.positions.items()},
        marks={},
        fees={"KRW": rates.fee_kr, "USD": rates.fee_us},
        limits=limits,
        daily_return=0,
        drawdown=0,
    )
    try:
        snap = snapshot.take(store, clock, as_of=as_of, book=book)
        budget.nav, budget.fx = snap.valuation.nav, snap.valuation.fx_rate
        budget.daily_return, budget.drawdown = snap.twr_return, snap.drawdown
        budget.marks = snapshot.last_prices(store, as_of=as_of, entities=sorted(book.positions))
        days = int(store.config("execution.settlement_days", as_of=as_of))
        budget.cash = {
            currency: ledger.available_cash(
                store,
                as_of=as_of,
                book=book,
                settlement_days=days,
                market=market,
                currency=currency,
            )
            for market, currency in (("KR", "KRW"), ("US", "USD"))
        }
    except (LookupError, ValueError) as exc:
        budget.valuation_error = str(exc)
    orders = store.get("orders", as_of=as_of)
    if orders.empty:
        return budget
    filled = filled_quantities(store, as_of=as_of)
    cancelled = cancelled_quantities(store, as_of=as_of)
    slip = float(store.config("execution.max_slippage", as_of=as_of))
    for record in orders.to_dict(orient="records"):
        status = str(record["status"])
        broker_known = str(record["reason"]).startswith("broker_order_no=")
        if status not in RESERVING and not (status in UNVERIFIED_TERMINAL and broker_known):
            continue
        session, entity, seq = (
            str(record["session_id"]),
            str(record["entity_id"]),
            int(record["slice_seq"]),
        )
        logical = key(session, entity, seq)
        hashed = client_order_id(session=session, entity_id=entity, slice_seq=seq)
        quantity = (float(record["quantity"]) - filled.get(logical, 0.0)
                    - filled.get(hashed, 0.0) - cancelled.get(logical, 0.0))
        if not math.isfinite(quantity) or quantity < 0:
            raise ValueError("fill/cancellation quantity exceeds original order")
        if quantity == 0:
            continue
        original_slip = float(
            store.config(
                "execution.max_slippage", as_of=pd.Timestamp(record["valid_from"]).to_pydatetime()
            )
        )
        price = float(record["limit_price"] or math.nan)
        if str(record["side"]) == "buy":
            price *= 1.0 + max(slip, original_slip)
        budget.reservations[logical] = Reservation(
            logical,
            entity,
            str(record["side"]),
            quantity,
            price,
            "USD" if str(record["market"]) == "US" else "KRW",
        )
    return budget
