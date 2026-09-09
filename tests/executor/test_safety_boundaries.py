"""감사 재현: 전송 시점의 latch와 실제 수량/세션을 사용해야 한다."""

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest

from quant_rl_trading.broker import Ack
from quant_rl_trading.executor import guards, pipeline
from quant_rl_trading.executor.orders import SliceParams, plan_slices
from quant_rl_trading.executor.sizing import SizingParams, Target, size_orders
from quant_rl_trading.replay.clock import ReplayClock
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


def test_deferred_buy_checks_latch_at_submission_time(seeded):
    decision = NOW - timedelta(days=1)
    guards.engage(seeded, as_of=NOW, observed_at=NOW, reason="stop", by="operator")
    broker = Mock()
    result = pipeline.submit_orders(
        seeded,
        ReplayClock(NOW),
        broker,
        planned=planned(seeded),
        as_of=decision,
        market="KR",
    )
    broker.submit.assert_not_called()
    assert result and all(not ack.accepted and "킬스위치" in ack.rsp_msg for ack in result)
    assert seeded.get("orders", as_of=NOW).empty  # 미전송을 submitting으로 적지 않는다


def test_latch_is_rechecked_between_slices_and_sells_remain_possible(seeded):
    items = planned(seeded)
    assert len(items) > 1

    def submit(item, *, as_of):
        guards.engage(seeded, as_of=NOW, observed_at=NOW, reason="stop", by="operator")
        return Ack(order_id=item.order_id, accepted=True, sent=True)

    broker = Mock(submit=Mock(side_effect=submit))
    result = pipeline.submit_orders(
        seeded, ReplayClock(NOW), broker, planned=items, as_of=NOW, market="KR"
    )
    assert broker.submit.call_count == 1
    assert sum(ack.accepted for ack in result) == 1
    sells = planned(seeded, Side.SELL)
    # sell에는 별도 session을 부여하여 매수 idempotency key와 충돌하지 않게 한다
    from dataclasses import replace

    sells = [replace(item, order_id=f"sell-{item.order_id}") for item in sells]
    pipeline.submit_orders(seeded, ReplayClock(NOW), broker, planned=sells, as_of=NOW, market="KR")
    assert broker.submit.call_count == 1 + len(sells)


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


def test_planned_buys_reserve_limit_price_and_commission(seeded):
    from quant_rl_trading.accounting import KRW, Rates
    from quant_rl_trading.accounting.book import Side as BookSide

    seeded.append(
        "prices",
        [
            {
                "entity_id": "KR:A",
                "valid_from": NOW,
                "observed_at": NOW,
                "source": "test",
                "market": "KR",
                "open": 1000.0,
                "high": 1000.0,
                "low": 1000.0,
                "close": 1000.0,
                "volume": 1e6,
                "value": 1e9,
            }
        ],
        ingest_run_id="current",
    )
    cash = 200000.0
    result = pipeline.run(
        seeded,
        ReplayClock(NOW),
        as_of=NOW,
        market="KR",
        targets=[Target("KR:A", 0.2, 1000.0, 1e9)],
        holdings={},
        equity=1e6,
        cash=cash,
    )
    assert result.planned
    gross = sum(item.order.quantity * item.order.limit_price for item in result.planned)
    fee, tax = Rates.from_store(seeded, as_of=NOW).costs(
        side=BookSide.BUY,
        gross=gross,
        currency=KRW,
    )
    assert gross + fee + tax <= cash
