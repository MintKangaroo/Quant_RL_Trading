"""체결 시뮬레이터.

고정 슬리피지가 소형주 수익률을 뻥튀기하는 것을 막는 게 목적이므로,
"주문이 클수록 비싸진다" 를 성질로 못박는다.
"""

from __future__ import annotations

import pytest

from quant_rl_trading.replay.fills import (
    FillParams,
    FillStatus,
    MarketState,
    impact_bps,
    max_position_for_liquidation,
    simulate_fill,
)
from quant_rl_trading.schemas.order import Order, Side

PARAMS = FillParams(
    impact_k=0.1, max_adv_ratio=0.03, max_liquidation_days=3, min_order_value=100_000.0
)


def state(**overrides) -> MarketState:  # type: ignore[no-untyped-def]
    base = {
        "entity_id": "KR:005930",
        "close": 70_000.0,
        "volume": 1_000_000.0,
        "adv": 1_000_000.0,
        "volatility": 0.02,
        "lot_size": 1,
        "tick_size": 0.0,
    }
    base.update(overrides)
    return MarketState(**base)  # type: ignore[arg-type]


def buy(quantity: int, **kwargs) -> Order:  # type: ignore[no-untyped-def]
    return Order(entity_id="KR:005930", side=Side.BUY, quantity=quantity, **kwargs)


def test_impact_grows_with_order_size() -> None:
    small = impact_bps(1_000, state(), PARAMS)
    large = impact_bps(100_000, state(), PARAMS)

    assert 0 < small < large


def test_impact_without_adv_is_an_error_not_zero() -> None:
    """유동성을 모르는 종목을 슬리피지 0 으로 체결시키면,
    가장 못 사는 종목이 백테스트에서 가장 좋은 성적을 낸다."""
    with pytest.raises(ValueError, match="ADV"):
        impact_bps(1_000, state(adv=0.0), PARAMS)


def test_buy_pays_up_and_sell_gets_less() -> None:
    bought = simulate_fill(buy(1_000), state(), PARAMS)
    sold = simulate_fill(
        Order(entity_id="KR:005930", side=Side.SELL, quantity=1_000), state(), PARAMS
    )

    assert bought.avg_price > 70_000.0 > sold.avg_price


def test_max_adv_ratio_causes_partial_fill() -> None:
    fill = simulate_fill(buy(100_000), state(volume=1_000_000.0), PARAMS)

    assert fill.status is FillStatus.PARTIAL
    assert fill.filled_quantity == 30_000, "거래량의 3% 를 넘겨 체결됐다"
    assert fill.reason == "max_adv_ratio"


def test_halted_stock_does_not_fill() -> None:
    fill = simulate_fill(buy(1_000), state(is_halted=True), PARAMS)

    assert fill.status is FillStatus.REJECTED
    assert fill.filled_quantity == 0
    assert fill.reason == "halted"


def test_cannot_buy_at_limit_up() -> None:
    fill = simulate_fill(buy(1_000), state(close=91_000.0, limit_up=91_000.0), PARAMS)

    assert fill.reason == "limit_up"


def test_cannot_sell_at_limit_down() -> None:
    fill = simulate_fill(
        Order(entity_id="KR:005930", side=Side.SELL, quantity=1_000),
        state(close=49_000.0, limit_down=49_000.0),
        PARAMS,
    )

    assert fill.reason == "limit_down"


def test_fill_price_never_breaks_the_limit_band() -> None:
    fill = simulate_fill(buy(30_000), state(close=90_000.0, limit_up=90_100.0), PARAMS)

    assert fill.avg_price <= 90_100.0


def test_quantity_is_rounded_down_to_lot_size() -> None:
    fill = simulate_fill(buy(1_050), state(lot_size=100), PARAMS)

    assert fill.filled_quantity == 1_000


def test_order_below_one_lot_is_rejected() -> None:
    fill = simulate_fill(buy(50), state(lot_size=100), PARAMS)

    assert fill.reason == "below_lot_size"


def test_tiny_order_is_rejected() -> None:
    """수수료가 잡아먹는 크기의 주문은 내지 않는다."""
    fill = simulate_fill(buy(1), state(close=1_000.0), PARAMS)

    assert fill.reason == "below_min_order_value"


def test_limit_order_is_not_filled_through() -> None:
    """저가도 지정가 위에서 끝났다 — 하루 종일 한 번도 안 닿았으니 미체결이다."""
    fill = simulate_fill(
        buy(1_000, limit_price=69_000.0),
        state(close=70_000.0, low=69_500.0, high=70_500.0),
        PARAMS,
    )

    assert fill.reason == "limit_not_met"


def test_buy_limit_fills_when_the_low_touched_it() -> None:
    """**2026-08-14 shadow 사고의 회귀 테스트.**

    KR:030200 을 52,700 지정가로 샀는데 그날 저가가 52,400 이었다. 저가가
    지정가 밑을 스쳤으니 채워진 것인데, 종가(53,400)와만 비교하던 판정이
    이걸 전부 미체결로 적었다. 못 산 종목이 오른 만큼 성적이 낮게 나오고,
    체결률은 유동성 문제처럼 보였다.
    """
    fill = simulate_fill(
        Order(entity_id="KR:030200", side=Side.BUY, quantity=28, limit_price=52_700.0),
        state(
            entity_id="KR:030200",
            close=53_400.0,
            low=52_400.0,
            high=54_300.0,
            volume=698_261.0,
            adv=436_171.5,
        ),
        PARAMS,
    )

    assert fill.status is FillStatus.FILLED
    assert fill.filled_quantity == 28
    # 지정가보다 비싸게 사지는 않는다.
    assert fill.avg_price == 52_700.0


def test_sell_limit_fills_when_the_high_touched_it() -> None:
    fill = simulate_fill(
        Order(entity_id="KR:005930", side=Side.SELL, quantity=1_000, limit_price=71_000.0),
        state(close=70_000.0, low=69_000.0, high=71_500.0),
        PARAMS,
    )

    assert fill.status is FillStatus.FILLED
    assert fill.avg_price == 71_000.0


def test_limit_does_not_improve_a_price_that_was_already_better() -> None:
    """지정가에 닿았다고 해서 지정가로 체결시키지는 않는다 —
    종가+충격이 이미 더 싸면 그 값이 남는다. 더 유리한 쪽을 지어내면
    백테스트가 실제보다 좋아진다."""
    fill = simulate_fill(
        buy(1_000, limit_price=71_000.0),
        state(close=70_000.0, low=69_000.0, high=70_500.0),
        PARAMS,
    )

    assert fill.status is FillStatus.FILLED
    assert 70_000.0 < fill.avg_price < 71_000.0


def test_missing_low_falls_back_to_close() -> None:
    """저가를 모르는 시계열은 종가로 판단한다 — 옛 동작 그대로.
    ``None`` 을 "항상 닿았다"로 읽으면 체결이 공짜가 된다."""
    fill = simulate_fill(buy(1_000, limit_price=69_000.0), state(close=70_000.0), PARAMS)

    assert fill.reason == "limit_not_met"


def test_illiquid_stock_is_rejected() -> None:
    fill = simulate_fill(buy(1_000), state(volume=10.0), PARAMS)

    assert fill.reason == "no_liquidity"


def test_liquidation_constraint_bounds_position_size() -> None:
    """3일 안에 못 빠져나오는 크기는 애초에 들어가지 않는다."""
    assert max_position_for_liquidation(state(adv=1_000_000.0), PARAMS) == 90_000


def test_params_come_from_store_config(store, ts) -> None:  # type: ignore[no-untyped-def]
    """임계치는 설정에서 읽는다 (불변식 10).

    시뮬레이터가 순수 함수로 남으면서도 숫자를 하드코딩하지 않는 연결고리라,
    끊어지면 조용히 옛 임계치로 백테스트가 돌아간다.
    """
    store.seed_config_defaults()

    loaded = FillParams.from_store(store, as_of=ts(2026, 8, 1))

    assert loaded.max_adv_ratio == 0.03
    assert loaded.max_liquidation_days == 3
    assert loaded == PARAMS


def test_same_input_gives_same_output() -> None:
    first = simulate_fill(buy(1_000), state(), PARAMS)
    second = simulate_fill(buy(1_000), state(), PARAMS)

    assert first.canonical() == second.canonical()
