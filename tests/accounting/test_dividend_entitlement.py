"""평가일 보유가 아니라 배당락 직전 수량으로 권리를 계산한다."""

from datetime import UTC, datetime, timedelta

import pytest

from quant_rl_trading.accounting import KRW, Rates
from quant_rl_trading.accounting.ledger import build_book

EX_DATE = datetime(2026, 9, 8, tzinfo=UTC)


@pytest.mark.parametrize(
    "buy_offset,sell_offset,expected_quantity",
    [
        (1, None, 0),
        (0, None, 0),
        (-1, None, 10),
        (-1, 1, 10),
        (-2, -1, 0),
    ],
)
def test_dividend_entitlement_survives_later_trades(
    store, buy_offset, sell_offset, expected_quantity
):
    store.seed_config_defaults()
    now = EX_DATE + timedelta(days=3)
    rows = []
    for side, offset in [("buy", buy_offset), ("sell", sell_offset)]:
        if offset is None:
            continue
        day = EX_DATE + timedelta(days=offset)
        rows.append(
            {
                "entity_id": "KR:A",
                "valid_from": day,
                "observed_at": day,
                "source": "test",
                "market": "KR",
                "side": side,
                "quantity": 10.0,
                "price": 100.0,
                "currency": KRW,
                "fee": 0.0,
                "tax": 0.0,
                "order_id": side,
            }
        )
    store.append("trades", rows, ingest_run_id="trades")
    store.append(
        "dividends",
        [
            {
                "entity_id": "KR:A",
                "valid_from": EX_DATE,
                "observed_at": EX_DATE,
                "source": "test",
                "market": "KR",
                "currency": KRW,
                "per_share": 10.0,
                "tax_rate": 0.154,
                "pay_date": now + timedelta(days=1),
            }
        ],
        ingest_run_id="dividend",
    )
    rates = Rates.from_store(store, as_of=now)
    book = build_book(store, as_of=now, rates=rates)
    assert book.accrued_dividend[KRW] == pytest.approx(
        rates.dividend_net(gross=expected_quantity * 10.0, currency=KRW)
    )
    paid = build_book(store, as_of=now + timedelta(days=1), rates=rates)
    assert paid.accrued_dividend[KRW] == 0
    assert paid.cash[KRW] - book.cash[KRW] == pytest.approx(book.accrued_dividend[KRW])


def test_future_effective_trade_and_dividend_do_not_enter_book(store):
    store.seed_config_defaults()
    later = EX_DATE + timedelta(days=1)
    store.append(
        "trades",
        [
            {
                "entity_id": "KR:A",
                "valid_from": later,
                "observed_at": EX_DATE,
                "source": "test",
                "market": "KR",
                "side": "buy",
                "quantity": 10.0,
                "price": 100.0,
                "currency": KRW,
                "fee": 0.0,
                "tax": 0.0,
                "order_id": "future",
            }
        ],
        ingest_run_id="future",
    )
    book = build_book(store, as_of=EX_DATE, rates=Rates.from_store(store, as_of=EX_DATE))
    assert not book.positions
    assert book.cash[KRW] == 0


@pytest.mark.parametrize("hour,entitled", [(1, True), (14, False)])
def test_us_entitlement_uses_exchange_date_not_utc_date(store, hour, entitled):
    store.seed_config_defaults()
    trade_at = EX_DATE + timedelta(hours=hour)
    store.append(
        "trades",
        [
            {
                "entity_id": "US:A",
                "valid_from": trade_at,
                "observed_at": trade_at,
                "source": "test",
                "market": "US",
                "side": "buy",
                "quantity": 10.0,
                "price": 100.0,
                "currency": "USD",
                "fee": 0.0,
                "tax": 0.0,
                "order_id": "us-buy",
            }
        ],
        ingest_run_id="us-buy",
    )
    store.append(
        "dividends",
        [
            {
                "entity_id": "US:A",
                "valid_from": EX_DATE,
                "observed_at": EX_DATE,
                "source": "test",
                "market": "US",
                "currency": "USD",
                "per_share": 10.0,
                "tax_rate": 0.15,
                "pay_date": None,
            }
        ],
        ingest_run_id="us-dividend",
    )
    now = EX_DATE + timedelta(days=1)
    rates = Rates.from_store(store, as_of=now)
    book = build_book(store, as_of=now, rates=rates)
    expected = rates.dividend_net(gross=100.0, currency="USD") if entitled else 0.0
    assert book.accrued_dividend["USD"] == pytest.approx(expected)
