"""``orders.py`` 의 시장 분기 — 특히 호가단위(tick) 배선.

2026-08-15 발견: ``limit_price``/``plan_slices`` 가 ``market`` 을 몰라
``round_to_tick`` 을 언제나 국장(KR) 표로 불렀다. 달러 가격을 원화
호가단위표에 먹이면 문턱값이 아무 의미가 없어진다 — 예를 들어 $50 짜리
미국 주식은 "2,000원 미만" 칸에 걸려 tick=$1 이 되고, 실제 필요한 $0.01
보다 100배 거칠게 반올림된다. 이 테스트는 그 배선이 실제로 걸려 있는지
전 구간(``limit_price`` → ``plan_slices``)에서 확인한다.
"""

from __future__ import annotations

from quant_rl_trading.executor.orders import SliceParams, limit_price, plan_slices
from quant_rl_trading.schemas.order import Side


def test_limit_price_defaults_to_kr_table() -> None:
    """market 인자를 생략한 기존 호출부는 지금까지와 같은 결과를 낸다."""
    price = limit_price(reference=55_000.0, side=Side.BUY, max_slippage=0.005)
    assert price == limit_price(
        reference=55_000.0, side=Side.BUY, max_slippage=0.005, market="KR"
    )


def test_limit_price_us_uses_dollar_tick_not_won_table() -> None:
    """회귀 재현 — market='US' 를 넘기면 달러 호가단위($0.01)를 쓴다."""
    reference = 50.3719  # KR 표로는 "2,000원 미만" 칸(tick=1)에 잘못 걸린다.
    kr_price = limit_price(
        reference=reference, side=Side.BUY, max_slippage=0.005, market="KR"
    )
    us_price = limit_price(
        reference=reference, side=Side.BUY, max_slippage=0.005, market="US"
    )
    assert kr_price != us_price
    # 미장 결과는 센트 단위여야 한다(정수 원 단위가 아니라).
    assert round(us_price * 100) == us_price * 100


def test_plan_slices_threads_market_into_limit_price() -> None:
    """분할 주문의 지정가가 market 인자를 따라 갈라지는지 — 진짜 배선 확인.

    ``plan_slices`` 가 내부에서 ``limit_price`` 를 부르되 market 을 안
    넘기면(옛 버그) 두 결과가 같아진다 — 그러면 이 테스트가 잡는다.
    """
    params = SliceParams(slice_count=1, slice_interval_sec=60, max_slippage=0.005)
    reference = 50.3719

    kr_planned = plan_slices(
        entity_id="US:AAPL",
        side=Side.BUY,
        quantity=10,
        reference_price=reference,
        target_weight=0.05,
        session="US-2026-08-17",
        params=params,
        market="KR",
    )
    us_planned = plan_slices(
        entity_id="US:AAPL",
        side=Side.BUY,
        quantity=10,
        reference_price=reference,
        target_weight=0.05,
        session="US-2026-08-17",
        params=params,
        market="US",
    )
    assert kr_planned[0].order.limit_price != us_planned[0].order.limit_price
    assert us_planned[0].order.limit_price == limit_price(
        reference=reference, side=Side.BUY, max_slippage=0.005, market="US"
    )


def test_plan_slices_market_defaults_to_kr() -> None:
    """market 을 생략하면 국장 표 — 기존 KR 호출부는 동작이 안 바뀐다."""
    params = SliceParams(slice_count=1, slice_interval_sec=60, max_slippage=0.005)
    planned = plan_slices(
        entity_id="KR:005930",
        side=Side.BUY,
        quantity=10,
        reference_price=55_000.0,
        target_weight=0.05,
        session="KR-2026-08-18",
        params=params,
    )
    assert planned[0].order.limit_price == limit_price(
        reference=55_000.0, side=Side.BUY, max_slippage=0.005, market="KR"
    )
