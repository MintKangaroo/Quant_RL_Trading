"""주문 생애주기 계약 테스트 — 전부 결정론.

시간은 항상 인자로 넣는다. 여기서 고정하는 것:
1. retry_after_sec 전에는 재호가하지 않는다 (WAIT)
2. max_retries 소진 후에는 취소한다 (CANCEL)
3. 부분체결이면 잔량만 재호가 대상이 된다
4. 슬리피지 상한을 넘으면 포기한다 (ABANDON, CANCEL 과 구분)
5. 세션 종료 시 미체결은 재시도 여부와 무관하게 취소된다 (이월 없음)
6. 같은 입력은 항상 같은 결정을 낸다 (결정론)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from quant_rl_trading.executor.lifecycle import (
    ActionType,
    LifecycleParams,
    OpenOrder,
    OrderStatus,
    apply_fill,
    close_session,
    decide,
    open_order_from_planned,
)
from quant_rl_trading.executor.orders import SliceParams, plan_slices
from quant_rl_trading.schemas.order import Side

NOW = datetime(2026, 8, 12, 1, 0, tzinfo=UTC)

PARAMS = LifecycleParams(retry_after_sec=300, max_retries=3, max_slippage=0.005)


def make_order(
    *,
    side: Side = Side.BUY,
    reference_price: float = 10_000.0,
    limit_price: float | None = None,
    remaining_quantity: int = 100,
    retry_count: int = 0,
    status: OrderStatus = OrderStatus.SUBMITTED,
    last_action_at: datetime = NOW,
) -> OpenOrder:
    return OpenOrder(
        order_id="ord-1",
        entity_id="005930",
        side=side,
        reference_price=reference_price,
        limit_price=limit_price if limit_price is not None else reference_price,
        original_quantity=100,
        remaining_quantity=remaining_quantity,
        retry_count=retry_count,
        last_action_at=last_action_at,
        status=status,
    )


# ---------------------------------------------------------------------------
# 1. retry_after_sec 전에는 재호가하지 않는다
# ---------------------------------------------------------------------------


def test_wait_before_retry_after_sec():
    order = make_order()
    now = NOW + timedelta(seconds=299)
    new_order, action = decide(order, now=now, market_price=10_050.0, params=PARAMS)
    assert action.type is ActionType.WAIT
    assert new_order == order  # 아무것도 안 바뀐다


def test_reprice_exactly_at_retry_after_sec():
    order = make_order()
    now = NOW + timedelta(seconds=300)
    new_order, action = decide(order, now=now, market_price=10_020.0, params=PARAMS)
    assert action.type is ActionType.REPRICE
    assert new_order.retry_count == 1
    assert new_order.last_action_at == now


# ---------------------------------------------------------------------------
# 2. max_retries 소진 후 취소
# ---------------------------------------------------------------------------


def test_cancel_after_max_retries_exhausted():
    order = make_order(retry_count=3)  # max_retries == 3, 이미 다 썼다
    now = NOW + timedelta(seconds=300)
    new_order, action = decide(order, now=now, market_price=10_020.0, params=PARAMS)
    assert action.type is ActionType.CANCEL
    assert new_order.status is OrderStatus.CANCELLED
    assert "재시도" in action.reason


def test_third_retry_still_allowed_then_fourth_blocked():
    order = make_order(retry_count=2)
    now = NOW + timedelta(seconds=300)
    new_order, action = decide(order, now=now, market_price=10_020.0, params=PARAMS)
    assert action.type is ActionType.REPRICE
    assert new_order.retry_count == 3

    now2 = now + timedelta(seconds=300)
    _final_order, action2 = decide(
        new_order, now=now2, market_price=10_020.0, params=PARAMS
    )
    assert action2.type is ActionType.CANCEL


# ---------------------------------------------------------------------------
# 3. 부분체결 — 잔량만 재호가 대상
# ---------------------------------------------------------------------------


def test_partial_fill_leaves_only_remaining_quantity():
    order = make_order(remaining_quantity=100)
    filled = apply_fill(order, filled_quantity=40, now=NOW + timedelta(seconds=10))
    assert filled.status is OrderStatus.PARTIALLY_FILLED
    assert filled.remaining_quantity == 60
    assert filled.original_quantity == 100  # 원 수량은 안 바뀐다


def test_full_fill_marks_filled():
    order = make_order(remaining_quantity=100)
    filled = apply_fill(order, filled_quantity=100, now=NOW)
    assert filled.status is OrderStatus.FILLED


def test_fill_exceeding_remaining_quantity_rejected():
    order = make_order(remaining_quantity=50)
    with pytest.raises(ValueError):
        apply_fill(order, filled_quantity=60, now=NOW)


def test_decide_on_partially_filled_reprices_only_remaining():
    order = make_order(remaining_quantity=100)
    partial = apply_fill(order, filled_quantity=70, now=NOW + timedelta(seconds=10))
    now = partial.last_action_at + timedelta(seconds=300)
    new_order, action = decide(partial, now=now, market_price=10_020.0, params=PARAMS)
    assert action.type is ActionType.REPRICE
    assert new_order.remaining_quantity == 30  # 70주는 이미 나갔다, 잔량만


# ---------------------------------------------------------------------------
# 4. 슬리피지 상한 초과 시 포기 (ABANDON, CANCEL 과 구분)
# ---------------------------------------------------------------------------


def test_abandon_when_market_moved_beyond_slippage_cap_buy():
    # 기준가 10,000 매수, 상한 0.5% → 10,050 이 캡. 시세가 10,060 이면 포기.
    order = make_order(side=Side.BUY, reference_price=10_000.0)
    now = NOW + timedelta(seconds=300)
    new_order, action = decide(order, now=now, market_price=10_060.0, params=PARAMS)
    assert action.type is ActionType.ABANDON
    assert new_order.status is OrderStatus.ABANDONED
    assert action.type is not ActionType.CANCEL


def test_reprice_within_slippage_cap_buy():
    order = make_order(side=Side.BUY, reference_price=10_000.0)
    now = NOW + timedelta(seconds=300)
    new_order, action = decide(order, now=now, market_price=10_040.0, params=PARAMS)
    assert action.type is ActionType.REPRICE
    assert new_order.limit_price == pytest.approx(10_040.0)


def test_abandon_when_market_moved_beyond_slippage_cap_sell():
    # 매도, 기준가 10,000, 상한 0.5% → 9,950 이 캡. 시세가 9,900 이면 포기.
    order = make_order(side=Side.SELL, reference_price=10_000.0)
    now = NOW + timedelta(seconds=300)
    _new_order, action = decide(order, now=now, market_price=9_900.0, params=PARAMS)
    assert action.type is ActionType.ABANDON


# ---------------------------------------------------------------------------
# 5. 세션 종료 — 미체결 이월 없음
# ---------------------------------------------------------------------------


def test_close_session_cancels_regardless_of_timer():
    # 방금 제출돼서 retry_after_sec 은 전혀 안 지났지만, 세션이 끝나면 취소.
    order = make_order(last_action_at=NOW)
    closed = close_session([order], now=NOW + timedelta(seconds=1))
    assert len(closed) == 1
    new_order, action = closed[0]
    assert action.type is ActionType.CANCEL
    assert new_order.status is OrderStatus.CANCELLED
    assert "이월" in action.reason


def test_close_session_skips_terminal_orders():
    filled = make_order(status=OrderStatus.FILLED, remaining_quantity=0)
    open_order = make_order()
    closed = close_session([filled, open_order], now=NOW + timedelta(seconds=1))
    assert len(closed) == 1
    assert closed[0][0].order_id == open_order.order_id


# ---------------------------------------------------------------------------
# 6. 결정론 — 같은 입력은 항상 같은 결정
# ---------------------------------------------------------------------------


def test_decide_is_deterministic():
    order = make_order()
    now = NOW + timedelta(seconds=300)
    result1 = decide(order, now=now, market_price=10_020.0, params=PARAMS)
    result2 = decide(order, now=now, market_price=10_020.0, params=PARAMS)
    assert result1 == result2


# ---------------------------------------------------------------------------
# 상태 오용 방지
# ---------------------------------------------------------------------------


def test_decide_on_terminal_order_raises():
    order = make_order(status=OrderStatus.CANCELLED)
    with pytest.raises(ValueError):
        decide(order, now=NOW, market_price=10_000.0, params=PARAMS)


def test_apply_fill_on_terminal_order_raises():
    order = make_order(status=OrderStatus.FILLED, remaining_quantity=0)
    with pytest.raises(ValueError):
        apply_fill(order, filled_quantity=1, now=NOW)


# ---------------------------------------------------------------------------
# orders.py 와의 연결 — PlannedOrder 로부터 등록
# ---------------------------------------------------------------------------


def test_open_order_from_planned_order():
    slice_params = SliceParams(slice_count=1, slice_interval_sec=60, max_slippage=0.005)
    planned = plan_slices(
        entity_id="005930",
        side=Side.BUY,
        quantity=100,
        reference_price=10_000.0,
        target_weight=0.05,
        session="KR-2026-08-12",
        params=slice_params,
    )[0]
    order = open_order_from_planned(planned, reference_price=10_000.0, now=NOW)
    assert order.status is OrderStatus.SUBMITTED
    assert order.remaining_quantity == 100
    assert order.limit_price == planned.order.limit_price
    assert order.order_id == planned.order_id


def test_open_order_from_market_order_rejected():
    slice_params = SliceParams(slice_count=1, slice_interval_sec=60, max_slippage=0.005)
    planned = plan_slices(
        entity_id="005930",
        side=Side.SELL,
        quantity=100,
        reference_price=10_000.0,
        target_weight=0.0,
        session="KR-2026-08-12",
        params=slice_params,
        market_order=True,
    )[0]
    with pytest.raises(ValueError):
        open_order_from_planned(planned, reference_price=10_000.0, now=NOW)
