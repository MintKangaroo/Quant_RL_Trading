"""감사 재현: 전송 시점의 latch와 실제 수량/세션을 사용해야 한다."""

from datetime import UTC, datetime, timedelta

import pytest

from quant_rl_trading.executor import guards
from quant_rl_trading.executor.orders import SliceParams, plan_slices
from quant_rl_trading.executor.sizing import SizingParams, Target, size_orders
from quant_rl_trading.schemas.order import Side

NOW = datetime(2026, 9, 9, 8, tzinfo=UTC)


@pytest.fixture
def seeded(store):
    store.seed_config_defaults()
    return store


def planned(store, side=Side.BUY):
    return plan_slices(
        entity_id="KR:005930",
        side=side,
        quantity=10,
        reference_price=1000,
        target_weight=0.1,
        session="KR-2026-09-08",
        params=SliceParams.from_store(store, as_of=NOW),
        market="KR",
    )


def test_reengage_same_day_after_manual_release(seeded):
    guards.engage(seeded, as_of=NOW, observed_at=NOW, reason="first", by="operator")
    later = NOW + timedelta(minutes=1)
    guards.release(seeded, as_of=later, observed_at=later, by="operator")
    again = later + timedelta(minutes=1)
    assert guards.engage(seeded, as_of=again, observed_at=again, reason="again", by="operator") == 1
    assert guards.engage(seeded, as_of=again, observed_at=again, reason="again", by="operator") == 0
    assert guards.killswitch_state(seeded, as_of=again)[0] is guards.KillswitchState.ENGAGED




def test_canonical_index_drop_triggers_breaker(seeded):
    seeded.append(
        "indices",
        [
            {
                "entity_id": "KR:IDX:KOSPI",
                "valid_from": day,
                "observed_at": day,
                "source": "test",
                "market": "KR",
                "close": close,
            }
            for day, close in [(NOW - timedelta(days=1), 1000.0), (NOW, 900.0)]
        ],
        ingest_run_id="index",
    )
    assert not guards.check_circuit_breaker(seeded, as_of=NOW, board="KOSPI")


def test_latest_available_is_not_latest_expected_session(seeded):
    stale = NOW - timedelta(days=2)
    seeded.append(
        "prices",
        [
            {
                "entity_id": "KR:005930",
                "valid_from": stale,
                "observed_at": stale,
                "source": "test",
                "market": "KR",
                "open": 1000.0,
                "high": 1000.0,
                "low": 1000.0,
                "close": 1000.0,
                "volume": 100.0,
                "value": 100000.0,
            }
        ],
        ingest_run_id="stale",
    )
    result = guards.check_data_quality(seeded, as_of=NOW, market="KR", entities=["KR:005930"])
    assert not result
    assert "2026-09-09" in result.reason


def test_expensive_share_limit_does_not_trap_existing_holding():
    params = SizingParams(
        max_adv_ratio=0.03, max_liquidation_days=3, min_order_value=100000.0, max_price_ratio=0.15
    )
    orders, skipped = size_orders(
        targets=[Target("KR:A", weight=0.0, price=200000.0, adv_value=1e9)],
        holdings={"KR:A": 1},
        equity=1000000.0,
        params=params,
    )
    assert not skipped
    assert len(orders) == 1 and orders[0].side is Side.SELL and orders[0].quantity == 1


def test_memoized_store_sees_external_latch_write(seeded):
    from quant_rl_trading.store.memo import MemoStore

    memo = MemoStore(seeded)
    assert guards.check_killswitch(memo, as_of=NOW)
    guards.engage(seeded, as_of=NOW, observed_at=NOW, reason="external", by="operator")
    assert not guards.check_killswitch(memo, as_of=NOW)
