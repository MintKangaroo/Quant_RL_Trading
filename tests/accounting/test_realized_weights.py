"""Actual exposure follows partial fills, and repeated reconciliation is idempotent."""

from datetime import UTC, datetime, timedelta

import pytest

from quant_rl_trading.accounting import weights
from quant_rl_trading.backtest.loop import seed_capital
from quant_rl_trading.executor import pipeline
from quant_rl_trading.replay.clock import ReplayClock

NOW = datetime(2026, 9, 9, 1, tzinfo=UTC)
SESSION = "KR-2026-09-09"


def test_partial_fill_updates_realized_exposure_without_counting_unfilled_quantity(store):
    store.seed_config_defaults()
    clock = ReplayClock(NOW)
    seed_capital(store, clock, amount=1e6, as_of=NOW - timedelta(days=1))
    store.append(
        "fx",
        [
            {
                "entity_id": "FX:USDKRW",
                "valid_from": NOW,
                "observed_at": NOW,
                "source": "test",
                "rate": 1350.0,
            }
        ],
        ingest_run_id="fx",
    )
    store.append(
        "prices",
        [
            {
                "entity_id": "KR:A",
                "valid_from": NOW,
                "observed_at": NOW,
                "source": "test",
                "market": "KR",
                "close": 1000.0,
            }
        ],
        ingest_run_id="price",
    )
    pipeline.record_realized_weights(
        store,
        clock,
        holdings={},
        equity=1e6,
        targets=[pipeline.Target("KR:A", weight=0.1, price=1000, adv_value=1e9)],
        as_of=NOW,
        market="KR",
        session=SESSION,
    )

    def trade(side, quantity, number):
        clock.advance(timedelta(seconds=1))
        store.append(
            "trades",
            [
                {
                    "entity_id": "KR:A",
                    "valid_from": clock.now(),
                    "observed_at": clock.now(),
                    "source": "test",
                    "market": "KR",
                    "side": side,
                    "quantity": quantity,
                    "price": 1000.0,
                    "currency": "KRW",
                    "fee": 0.0,
                    "tax": 0.0,
                    "order_id": f"{SESSION}|KR:A|{number}",
                }
            ],
            ingest_run_id=f"fill-{number}",
        )

    trade("buy", 40.0, 1)  # 100 intended shares, only 40 executed
    assert weights.refresh(store, clock, as_of=clock.now(), sessions={SESSION}) == 1
    actual = store.get("realized_weights", as_of=clock.now()).iloc[0]
    assert actual["realized_weight"] == pytest.approx(0.04)
    assert pipeline.action_reflection_rate(store, as_of=clock.now()) == pytest.approx(0.4)
    assert weights.refresh(store, clock, as_of=clock.now(), sessions={SESSION}) == 0
    trade("buy", 60.0, 2)
    assert weights.refresh(store, clock, as_of=clock.now(), sessions={SESSION}) == 1
    assert store.get("realized_weights", as_of=clock.now()).iloc[0][
        "realized_weight"
    ] == pytest.approx(0.1)
    trade("sell", 60.0, 3)
    assert weights.refresh(store, clock, as_of=clock.now(), sessions={SESSION}) == 1
    assert store.get("realized_weights", as_of=clock.now()).iloc[0][
        "realized_weight"
    ] == pytest.approx(0.04)
