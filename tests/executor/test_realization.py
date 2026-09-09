from datetime import UTC, datetime, timedelta

import pytest

from quant_rl_trading.executor import pipeline, realization
from quant_rl_trading.executor.orders import SliceParams, plan_slices
from quant_rl_trading.executor.sizing import Target
from quant_rl_trading.replay.clock import ReplayClock
from quant_rl_trading.schemas.order import Side

NOW = datetime(2026, 9, 9, 8, tzinfo=UTC)
SESSION = "KR-2026-09-09"


@pytest.mark.parametrize("kind", ["broker", "replay"])
@pytest.mark.parametrize("side,initial,target", [(Side.BUY, 0, 0.1), (Side.SELL, 10, 0.05)])
def test_actual_fills_update_weights_without_rewriting_history(store, kind, side, initial, target):
    store.seed_config_defaults()
    clock = ReplayClock(NOW)
    planned = plan_slices(
        entity_id="KR:A",
        side=side,
        quantity=10 if side is Side.BUY else 5,
        reference_price=100.0,
        target_weight=target,
        session=SESSION,
        params=SliceParams(slice_count=1, slice_interval_sec=0, max_slippage=0.005),
        market="KR",
    )
    pipeline.record_orders(store, clock, planned=planned, as_of=NOW, market="KR")
    pipeline.record_realized_weights(
        store,
        clock,
        holdings={"KR:A": initial},
        equity=10000.0,
        targets=[Target("KR:A", target, 100.0, 1e9)],
        as_of=NOW,
        market="KR",
        session=SESSION,
    )
    before = store.get("realized_weights", as_of=NOW).iloc[0]
    assert before["realized_weight"] == pytest.approx(initial / 100)
    if side is Side.BUY:
        assert pipeline.action_reflection_rate(store, as_of=NOW) == 0
    later = NOW + timedelta(minutes=1)
    fill_id = f"{planned[0].order_id}#5" if kind == "broker" else f"{SESSION}|KR:A|{side}"
    store.append(
        "trades",
        [
            {
                "entity_id": "KR:A",
                "valid_from": later,
                "observed_at": later,
                "source": "test",
                "market": "KR",
                "side": str(side),
                "quantity": 5.0,
                "price": 101.0,
                "currency": "KRW",
                "fee": 1.0,
                "tax": 0.0,
                "order_id": fill_id,
            }
        ],
        ingest_run_id="partial",
    )
    assert realization.refresh(store, ReplayClock(later), as_of=later) == 1
    assert realization.refresh(store, ReplayClock(later), as_of=later) == 0
    assert store.get("realized_weights", as_of=later).iloc[0]["realized_weight"] == pytest.approx(
        0.05
    )
    # 체결단가 101로 인한 시장 움직임/비용을 목표 집행률로 혼합하지 않는다.
    assert (
        store.get("realized_weights", as_of=NOW).iloc[0]["realized_weight"]
        == before["realized_weight"]
    )


def test_legacy_projection_is_unmeasured(store):
    store.append(
        "realized_weights",
        [
            {
                "entity_id": "KR:A",
                "valid_from": NOW,
                "observed_at": NOW,
                "source": "executor",
                "market": "KR",
                "session_id": SESSION,
                "target_weight": 0.1,
                "realized_weight": 0.1,
            }
        ],
        ingest_run_id="legacy",
    )
    assert pipeline.action_reflection_rate(store, as_of=NOW) is None
