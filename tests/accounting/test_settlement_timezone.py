"""A UTC midnight must not advance the US settlement calendar."""

from datetime import UTC, datetime

import pytest

from quant_rl_trading.accounting.book import Book
from quant_rl_trading.accounting.ledger import available_cash


def test_us_sale_cash_waits_for_market_local_settlement_day(store):
    sale = datetime(2026, 9, 4, 14, tzinfo=UTC)  # Friday; Monday is an exchange holiday.
    store.append(
        "trades",
        [
            {
                "entity_id": "US:X",
                "valid_from": sale,
                "observed_at": sale,
                "source": "test",
                "market": "US",
                "currency": "USD",
                "side": "sell",
                "quantity": 10.0,
                "price": 100.0,
                "fee": 1.0,
                "tax": 0.0,
                "order_id": "sale",
            }
        ],
        ingest_run_id="sale",
    )
    book = Book(cash={"USD": 999.0})
    # Wednesday UTC, still Tuesday in New York: D+2 has not arrived.
    before = datetime(2026, 9, 9, 2, tzinfo=UTC)
    assert (
        available_cash(
            store, as_of=before, book=book, settlement_days=2, market="US", currency="USD"
        )
        == 0
    )
    after = datetime(2026, 9, 9, 14, tzinfo=UTC)
    assert available_cash(
        store, as_of=after, book=book, settlement_days=2, market="US", currency="USD"
    ) == pytest.approx(999)
