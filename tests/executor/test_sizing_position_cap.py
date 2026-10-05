"""사이징의 종목 상한 — 위험 한도와 같은 잣대 (agents.md §7 5번, 2026-10-04).

위험 한도(risk/budget.py)는 보유를 평가가, 대기 매수를 지정가로 재서 종목 비중 ≤ `allocator.max_position_weight` 를 건다.
사이징이 기준가로만 자르면 상한 ÷ (1+슬리피지) 를 넘는 목표의 마지막 조각이 막힌다.
"""

from __future__ import annotations

import math

from quant_rl_trading.executor.sizing import SizingParams, Target, size_orders
from quant_rl_trading.schemas.order import Side

EQUITY = 100_000_000.0
CUSHION = 1.005 ** 2 * 1.00015
PARAMS = SizingParams(max_adv_ratio=1.0, max_liquidation_days=3, min_order_value=100_000.0, max_price_ratio=0.1,
                      settlement_days=0, max_position=0.15, position_cushion=CUSHION)


def _target(weight: float) -> Target:
    return Target(entity_id="KR:A", weight=weight, price=10_000.0, adv_value=1e12)


def test_상한에_붙은_매수는_위험_한도가_받는_수량까지만() -> None:
    orders, _ = size_orders(targets=[_target(0.15)], holdings={}, equity=EQUITY, params=PARAMS)
    allowed = math.floor(0.15 * EQUITY / (10_000.0 * CUSHION))
    assert [(o.side, o.quantity) for o in orders] == [(Side.BUY, allowed)]
    assert "종목 상한" in orders[0].reason
    # 위험 한도의 잣대(지정가 ≤ 기준가 × 쿠션)로 재도 상한 안이다.
    assert allowed * 10_000.0 * CUSHION <= 0.15 * EQUITY


def test_상한_위_목표도_같은_수량에서_멈추고_보유를_센다() -> None:
    orders, _ = size_orders(targets=[_target(0.168)], holdings={"KR:A": 1_000}, equity=EQUITY, params=PARAMS)
    allowed = math.floor((0.15 * EQUITY - 1_000 * 10_000.0) / (10_000.0 * CUSHION))
    assert [(o.side, o.quantity) for o in orders] == [(Side.BUY, allowed)]


def test_이미_상한이면_사지_않고_사유를_남긴다() -> None:
    orders, skipped = size_orders(targets=[_target(0.168)], holdings={"KR:A": 1_500}, equity=EQUITY, params=PARAMS)
    assert not orders
    assert any("종목 상한" in item.reason for item in skipped)


def test_매도는_자르지_않는다() -> None:
    orders, _ = size_orders(targets=[_target(0.0)], holdings={"KR:A": 2_000}, equity=EQUITY, params=PARAMS)
    assert [(o.side, o.quantity) for o in orders] == [(Side.SELL, 2_000)]


def test_상한_밑_목표는_그대로() -> None:
    orders, _ = size_orders(targets=[_target(0.10)], holdings={}, equity=EQUITY, params=PARAMS)
    assert [(o.side, o.quantity, o.reason) for o in orders] == [(Side.BUY, 1_000, "")]
