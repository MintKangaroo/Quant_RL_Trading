"""Account cash and exposure constraints. Values come from the accounting ledger."""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Limits:
    max_position: float
    max_exposure: float
    max_positions: int
    max_daily_loss: float
    max_drawdown: float


@dataclass(frozen=True)
class Reservation:
    key: str
    entity: str
    side: str
    quantity: float
    price: float  # maximum possible fill price, in market currency
    currency: str


@dataclass(frozen=True)
class Decision:
    passed: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.passed


@dataclass
class Budget:
    nav: float
    fx: float
    cash: dict[str, float]
    holdings: dict[str, float]
    marks: dict[str, float]  # each instrument's own currency
    fees: dict[str, float]
    limits: Limits
    daily_return: float
    drawdown: float
    reservations: dict[str, Reservation] = field(default_factory=dict)
    valuation_error: str = ""

    def check(self, proposed: Reservation) -> Decision:
        if (
            proposed.side not in {"buy", "sell"}
            or proposed.currency not in {"KRW", "USD"}
            or not math.isfinite(proposed.quantity)
            or proposed.quantity <= 0
        ):
            return Decision(False, "risk: invalid order")
        others = [r for key, r in self.reservations.items() if key != proposed.key]
        if proposed.side == "sell":
            reserved = sum(
                r.quantity for r in others if r.entity == proposed.entity and r.side == "sell"
            )
            available = self.holdings.get(proposed.entity, 0.0) - reserved
            return (
                Decision(True)
                if proposed.quantity <= available + 1e-9
                else Decision(False, "risk: sell exceeds unreserved inventory")
            )
        if self.valuation_error:
            return Decision(False, f"risk: account valuation unavailable ({self.valuation_error})")
        limits = self.limits
        values = [
            self.nav,
            self.fx,
            self.daily_return,
            self.drawdown,
            limits.max_position,
            limits.max_exposure,
            limits.max_daily_loss,
            limits.max_drawdown,
        ]
        if (
            not all(math.isfinite(v) for v in values)
            or self.nav <= 0
            or self.fx <= 0
            or not 0 < limits.max_position <= 1
            or not 0 < limits.max_exposure <= 1
            or not 0 < limits.max_daily_loss <= 1
            or not 0 < limits.max_drawdown <= 1
            or limits.max_positions < 1
        ):
            return Decision(False, "risk: invalid account or risk limits")
        if self.daily_return <= -limits.max_daily_loss:
            return Decision(False, "risk: maximum daily loss")
        if self.drawdown <= -limits.max_drawdown:
            return Decision(False, "risk: maximum drawdown")
        buys = [r for r in others if r.side == "buy"] + [proposed]
        exposure: dict[str, float] = {}
        for entity, quantity in self.holdings.items():
            if not math.isfinite(quantity) or quantity < 0:
                return Decision(False, "risk: invalid holding quantity")
            if quantity <= 0:
                continue
            mark = self.marks.get(entity, math.nan)
            if not math.isfinite(mark) or mark <= 0:
                return Decision(False, "risk: missing holding valuation")
            exposure[entity] = quantity * mark * (self.fx if entity.startswith("US:") else 1.0)
        reserved_cash = {"KRW": 0.0, "USD": 0.0}
        pending_fees = 0.0
        for r in buys:
            fee = self.fees.get(r.currency, math.nan)
            if (
                r.currency not in reserved_cash
                or not math.isfinite(r.price)
                or r.price <= 0
                or not math.isfinite(r.quantity)
                or r.quantity < 0
                or not math.isfinite(fee)
                or fee < 0
            ):
                return Decision(False, "risk: buy requires a bounded price and known fee")
            gross = r.quantity * r.price
            rate = self.fx if r.currency == "USD" else 1.0
            reserved_cash[r.currency] += gross * (1.0 + fee)
            pending_fees += gross * fee * rate
            mark = self.marks.get(r.entity, r.price)
            exposure[r.entity] = (
                exposure.get(r.entity, 0.0) + r.quantity * max(mark, r.price) * rate
            )
        for currency, reserved in reserved_cash.items():
            available = self.cash.get(currency, 0.0)
            if not math.isfinite(available) or reserved > available + 1e-8:
                return Decision(False, f"risk: insufficient unreserved {currency} cash")
        after_fees = self.nav - pending_fees
        if after_fees <= 0 or sum(exposure.values()) > after_fees * limits.max_exposure + 1e-8:
            return Decision(False, "risk: maximum gross exposure")
        if exposure.get(proposed.entity, 0.0) > after_fees * limits.max_position + 1e-8:
            return Decision(False, "risk: maximum position size")
        if len([v for v in exposure.values() if v > 0]) > limits.max_positions:
            return Decision(False, "risk: maximum simultaneous positions")
        return Decision(True)
