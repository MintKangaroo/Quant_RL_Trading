"""Point-in-time adapter from the fill/order journals to the pure budget engine."""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta

import pandas as pd

from quant_rl_trading.accounting import ledger, snapshot
from quant_rl_trading.accounting.rates import Rates
from quant_rl_trading.collectors.market_hours import SPECS, Market, is_trading_day, local_time
from quant_rl_trading.executor.action_journal import cancelled_quantities, submission_times
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


def order_trading_day(market: Market, moment: datetime) -> date:
    """``moment`` 에 낸(또는 관측한) 주문이 속하는 **거래소 거래일**.

    현지 시각이 정규장 마감 전이고 그날이 거래일이면 그날, 아니면 다음 거래일이다.
    미장 주문은 한국시간 12:20 에 기록되는데 현지로는 전날 밤 23:20 이라, 관측
    시각의 현지 날짜로 세면 그 주문이 아직 열리지도 않은 세션 중에 "지난 날" 로
    풀려 버린다(코드 리뷰 2026-09-11) — 그래서 날짜가 아니라 세션으로 센다.
    """
    here = local_time(market, moment)
    day = here.date()
    if here.time() >= SPECS[market].regular_close or not is_trading_day(market, day):
        day += timedelta(days=1)
        for _ in range(14):
            if is_trading_day(market, day):
                break
            day += timedelta(days=1)
    return day


def _broker_day_has_passed(
    record: dict, *, as_of: datetime, submitted: datetime | None
) -> bool:
    """이 주문이 살아 있던 거래소 거래일이 as_of 의 거래일보다 앞선가.

    기준 시각은 **브로커 전송 시각**이다. 주문 행의 ``observed_at`` 은 15:45 대사가
    장 마감 뒤로 다시 적으므로, 그걸로 세면 그날 주문이 다음 세션 것으로 밀린다.
    전송 기록이 없으면(미전송 예약) 마지막 관측 시각으로 센다.
    """
    market = Market(str(record["market"]))
    seen = submitted or pd.Timestamp(record["observed_at"]).to_pydatetime()
    return order_trading_day(market, seen) < order_trading_day(market, as_of)


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
    submitted_at = submission_times(store, as_of=as_of)
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
        if _broker_day_has_passed(
            record, as_of=as_of, submitted=submitted_at.get(logical)
        ):
            # **지난 거래일의 주문은 상태와 무관하게 잔량이 남을 수 없다.** 국장·미장
            # 지정가는 당일 유효(day order)라 장 마감에 거래소가 미체결을 소멸시킨다.
            # 받았는지 모르는 주문(submitting·*_unknown)도 받았다면 그날 소멸했고
            # 못 받았다면 애초에 없다. 예약이 아니라 **대사**의 문제다 — 실제 체결
            # 누락은 15:45 체결 대사가 잡는다. 이걸 예약으로 계속 잡으면 매일 쌓여
            # 계좌를 마비시킨다: 2026-09-11 모의계좌 예약 317건(8/26 시뮬 96·abandoned
            # 120·…), 매수 24건 "현금 부족"·매도 9건 "재고 초과" 차단.
            continue
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
