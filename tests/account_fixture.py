"""Explicit ledger funding for tests that expect order approval."""

from datetime import timedelta


def fund_account(store, moment, *, capital=10_000_000.0, holdings=None):
    before = moment - timedelta(days=1)
    store.append(
        "capital_flows",
        [
            {
                "entity_id": "FUND",
                "valid_from": before,
                "observed_at": before,
                "source": "test",
                "currency": "KRW",
                "amount": capital,
                "kind": "deposit",
            }
        ],
        ingest_run_id="test-account-capital",
    )
    store.append(
        "fx",
        [
            {
                "entity_id": "FX:USDKRW",
                "valid_from": before,
                "observed_at": before,
                "source": "test",
                "rate": 1350.0,
            }
        ],
        ingest_run_id="test-account-fx",
    )
    if holdings:
        store.append(
            "trades",
            [
                {
                    "entity_id": entity,
                    "valid_from": before,
                    "observed_at": before,
                    "source": "test",
                    "market": "KR",
                    "currency": "KRW",
                    "side": "buy",
                    "quantity": quantity,
                    "price": 1000.0,
                    "fee": 0.0,
                    "tax": 0.0,
                    "order_id": f"opening-{entity}",
                }
                for entity, quantity in holdings.items()
            ],
            ingest_run_id="test-account-holdings",
        )
