from datetime import UTC, datetime
from unittest.mock import Mock

import pandas as pd

from quant_rl_trading.dashboard import create_app
from quant_rl_trading.dashboard.services import freshness
from quant_rl_trading.replay.clock import ReplayClock

NOW = datetime(2026, 9, 9, 8, tzinfo=UTC)


def test_historical_account_request_never_fetches_current_balance(store, monkeypatch):
    store.seed_config_defaults()
    fetch = Mock(side_effect=AssertionError("현재 계좌 조회 금지"))
    monkeypatch.setattr(
        "quant_rl_trading.dashboard.api.trading.account_service.broker_account", fetch
    )
    app = create_app(store=store, clock=ReplayClock(NOW))
    response = app.test_client().get("/api/trading/account?as_of=2026-09-08T08:00:00Z")
    assert response.status_code == 200
    assert response.json["data"]["available"] is False
    assert "미측정" in response.json["data"]["reason"]
    fetch.assert_not_called()


def test_missing_kr_index_does_not_borrow_us_index_freshness(store):
    store.append(
        "indices",
        [
            {
                "entity_id": "US:IDX:SP500",
                "valid_from": NOW,
                "observed_at": NOW,
                "source": "test",
                "market": "US",
                "close": 6000.0,
            }
        ],
        ingest_run_id="us-index",
    )
    assert (
        freshness._latest_session(
            store,
            "indices",
            as_of=NOW,
            market=None,
            entity="KR:IDX:KOSPI",
        )
        is None
    )


def test_bad_price_is_not_usable():
    from quant_rl_trading.store.prices import drop_dead_sessions

    frame = pd.DataFrame({"close": [0.0, -1.0, float("inf"), float("nan"), 100.0]})
    assert drop_dead_sessions(frame)["close"].tolist() == [100.0]


def test_known_but_future_effective_price_is_not_usable(store):
    from datetime import timedelta

    from quant_rl_trading.store.prices import read_prices

    store.append(
        "prices",
        [
            {
                "entity_id": "KR:A",
                "valid_from": NOW + timedelta(days=1),
                "observed_at": NOW,
                "source": "test",
                "market": "KR",
                "open": 100.0,
                "high": 100.0,
                "low": 100.0,
                "close": 100.0,
                "volume": 100.0,
                "value": 10000.0,
            }
        ],
        ingest_run_id="future-price",
    )
    assert read_prices(store, as_of=NOW, market="KR").empty
