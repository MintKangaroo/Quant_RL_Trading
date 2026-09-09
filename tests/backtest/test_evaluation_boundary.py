"""A loss on the first scored session belongs in both return and drawdown."""

import pytest

from quant_rl_trading.backtest.stats import summarize


def test_first_scored_day_included():
    report = summarize(
        initial_index=100.0,
        index_values=[90.0, 99.0],
        returns=[-0.1, 0.1],
        traded_value=0.0,
        average_nav=100.0,
        requested=0,
        filled=0,
        action_reflection=0.0,
    )
    assert report.total_return == pytest.approx(-0.01)
    assert report.max_drawdown == pytest.approx(-0.1)
    assert report.days == 2


def test_single_scored_day_can_lose_money():
    report = summarize(
        initial_index=100.0,
        index_values=[80.0],
        returns=[-0.2],
        traded_value=0.0,
        average_nav=100.0,
        requested=0,
        filled=0,
        action_reflection=0.0,
    )
    assert report.total_return == pytest.approx(-0.2)
    assert report.max_drawdown == pytest.approx(-0.2)


@pytest.mark.parametrize("intraday_snapshot", [False, True])
def test_loop_includes_loss_against_previous_accounting_snapshot(store, intraday_snapshot):
    from datetime import UTC, date, datetime, timedelta

    from quant_rl_trading.accounting import snapshot
    from quant_rl_trading.backtest import loop
    from quant_rl_trading.replay.clock import ReplayClock

    store.seed_config_defaults()
    prior = datetime(2026, 9, 8, 7, tzinfo=UTC)
    current = prior + timedelta(days=1)
    clock = ReplayClock(prior)
    loop.seed_capital(store, clock, amount=100_000.0, as_of=prior - timedelta(days=1))
    store.append(
        "fx",
        [
            {
                "entity_id": "FX:USDKRW",
                "valid_from": prior,
                "observed_at": prior,
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
                "valid_from": at,
                "observed_at": at,
                "source": "test",
                "market": "KR",
                "close": price,
            }
            for at, price in ((prior, 1000.0), (current, 900.0))
        ],
        ingest_run_id="prices",
    )
    store.append(
        "trades",
        [
            {
                "entity_id": "KR:A",
                "valid_from": prior,
                "observed_at": prior,
                "source": "test",
                "market": "KR",
                "side": "buy",
                "quantity": 100.0,
                "price": 1000.0,
                "currency": "KRW",
                "fee": 0.0,
                "tax": 0.0,
                "order_id": "opening-position",
            }
        ],
        ingest_run_id="opening-position",
    )
    opening = snapshot.take(store, clock, as_of=prior)
    snapshot.write(store, clock, snapshot=opening)

    if intraday_snapshot:
        at = current - timedelta(hours=2)
        store.append("prices", [{
            "entity_id": "KR:A", "valid_from": at, "observed_at": at,
            "source": "test", "market": "KR", "close": 950.0,
        }], ingest_run_id="intraday-price")
        clock = ReplayClock(at)
        snapshot.write(store, clock, snapshot=snapshot.take(store, clock, as_of=at))

    report = loop.run(
        store,
        start=date(2026, 9, 9),
        end=date(2026, 9, 9),
        produce_signals=False,
    )
    assert report.days[0].twr_return == pytest.approx(-0.1)
    assert report.performance.total_return == pytest.approx(-0.1)
    assert report.performance.max_drawdown == pytest.approx(-0.1)
