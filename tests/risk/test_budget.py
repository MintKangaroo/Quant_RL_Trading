"""Financial boundaries of the deterministic, currency-aware account budget."""

from dataclasses import replace

import pytest

from quant_rl_trading.risk.budget import Budget, Limits, Reservation


def budget():
    return Budget(
        nav=100_000,
        fx=1000,
        cash={"KRW": 100_000, "USD": 0},
        holdings={},
        marks={},
        fees={"KRW": 0.001, "USD": 0.002},
        limits=Limits(0.5, 0.8, 2, 0.03, 0.3),
        daily_return=0,
        drawdown=0,
    )


def buy(entity="KR:A", quantity=10, price=1000, currency="KRW", key="new"):
    return Reservation(key, entity, "buy", quantity, price, currency)


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("daily_return", -0.03, "daily loss"),
        ("drawdown", -0.3, "drawdown"),
        ("nav", float("nan"), "invalid account"),
        ("fx", 0, "invalid account"),
    ],
)
def test_loss_limits_and_invalid_valuation_block_buys(field, value, reason):
    b = budget()
    setattr(b, field, value)
    decision = b.check(buy())
    assert not decision and reason in decision.reason


def test_name_cap_includes_existing_and_reserved_exposure():
    b = budget()
    b.holdings, b.marks = {"KR:A": 30}, {"KR:A": 1000}
    b.reservations["pending"] = buy(quantity=10, key="pending")
    assert not b.check(buy(quantity=10))  # post-fee NAV is smaller than 100,000
    assert b.check(buy(quantity=9))


def test_gross_cap_aggregates_kr_and_us_in_krw():
    b = budget()
    b.holdings, b.marks = {"US:X": 70}, {"US:X": 1.0}
    decision = b.check(buy(quantity=11))
    assert not decision and "gross exposure" in decision.reason


def test_krw_cash_cannot_fund_a_usd_order():
    b = budget()
    decision = b.check(buy("US:A", quantity=1, price=1, currency="USD"))
    assert not decision and "USD cash" in decision.reason


def test_pending_sells_do_not_release_cash_or_exposure():
    b = budget()
    b.holdings, b.marks = {"KR:X": 40, "KR:Y": 30}, {"KR:X": 1000, "KR:Y": 1000}
    b.reservations["sell"] = Reservation("sell", "KR:X", "sell", 40, 1000, "KRW")
    decision = b.check(buy(quantity=11))
    assert not decision and "gross exposure" in decision.reason


def test_position_count_includes_pending_buys():
    b = budget()
    b.reservations = {"a": buy("KR:A", key="a"), "b": buy("KR:B", key="b")}
    assert "simultaneous" in b.check(buy("KR:C")).reason


def test_sell_reservations_and_buy_halt_still_allow_available_liquidation():
    b = budget()
    b.daily_return = -0.5
    b.valuation_error = "no current FX"
    b.holdings = {"KR:A": 10}
    b.reservations["sell"] = Reservation("sell", "KR:A", "sell", 7, 1000, "KRW")
    proposed = Reservation("new", "KR:A", "sell", 4, 1000, "KRW")
    assert not b.check(proposed)
    assert b.check(replace(proposed, quantity=3))


def test_own_reservation_is_replaced_not_double_counted():
    b = budget()
    b.reservations["new"] = buy(quantity=40)
    assert b.check(buy(quantity=40))


@pytest.mark.parametrize("quantity", [float("nan"), float("inf"), -1])
def test_invalid_inventory_cannot_bypass_exposure_check(quantity):
    b = budget()
    b.holdings, b.marks = {"KR:A": quantity}, {"KR:A": 1000}
    assert not b.check(buy())
