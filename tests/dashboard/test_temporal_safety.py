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


def test_공표_뒤_수집_유예_안이면_지연이_아니라_수집_대기다(store):
    """2026-09-17 아침: 미장 9/16 은 05:20 KST 에 공표됐지만 수집 크론은 08:40 이다. 그 사이는 pending."""
    from datetime import UTC, datetime

    store.seed_config_defaults()
    # 미장 9/15 시세만 있다. as_of 는 9/17 08:30 KST(= 9/16 23:30 UTC) — 9/16 세션 공표(05:20 KST) 뒤 3시간.
    store.append("prices", [{
        "entity_id": "US:AAPL", "valid_from": datetime(2026, 9, 15, 0, 0, tzinfo=UTC),  # 세션일 00:00 UTC — 창고 규약
        "observed_at": datetime(2026, 9, 15, 20, 20, tzinfo=UTC), "source": "test", "market": "US",
        "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1,
    }], ingest_run_id="p-us")
    morning = freshness.summary(store, as_of=datetime(2026, 9, 16, 23, 30, tzinfo=UTC))
    us = {i["key"]: i for i in morning["items"]}["us_prices"]
    assert us["lag_sessions"] == 1 and us["status"] == "pending", us
    # 10:30 KST 면 유예가 끝났다 — 진짜 지연
    late = freshness.summary(store, as_of=datetime(2026, 9, 17, 1, 30, tzinfo=UTC))
    assert {i["key"]: i for i in late["items"]}["us_prices"]["status"] == "stale"


def test_라이브_as_of_는_버킷으로_바닥_내림한다():
    """한 화면의 패널들이 **같은 시각**을 묻게 한다. 명시된 as_of 는 안 건드린다."""
    from datetime import UTC, datetime

    from quant_rl_trading.dashboard.api.common import quantize

    moment = datetime(2026, 9, 17, 4, 1, 37, 812000, tzinfo=UTC)
    assert quantize(moment, bucket_seconds=45) == datetime(2026, 9, 17, 4, 1, 30, tzinfo=UTC)
    assert quantize(moment, bucket_seconds=0) == moment, "0 이면 끈다"
    assert quantize(moment, bucket_seconds=1) == moment.replace(microsecond=0)


def test_판정_벤치마크는_자기_유예를_쓴다():
    """KRX 는 KODEX200 을 **다음 날 아침**에 낸다 — 국장 기본 유예(40분)로 재면 평일
    16:40 부터 매일 빨간불이 켜진다. 그런 경보는 진짜 고장을 덮는다."""
    from quant_rl_trading.dashboard.services.freshness import DATASETS

    row = next(item for item in DATASETS if item[0] == "kr_benchmark")
    assert row[6] == "system.freshness_grace_seconds_kr_benchmark", "자기 유예 키가 있어야 한다"
    others = [item[6] for item in DATASETS if item[0] != "kr_benchmark"]
    assert all(key is None for key in others), "나머지는 시장 기본값을 쓴다"
