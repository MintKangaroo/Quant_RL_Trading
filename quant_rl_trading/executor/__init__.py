"""Executor — 목표 비중을 주문으로 바꾸고, 그 전에 네 겹의 안전장치를 지난다.

**Executor 안에는 AI 가 없다. 순수 코드만** (불변식 6). 마지막 안전장치는
예측 가능해야 한다 — 왜 막혔는지 사람이 읽고 30초 안에 판단할 수 있어야 한다.

    guards.py    킬스위치 latch · 데이터 게이트 · 서킷브레이커 · defer
    sizing.py    목표비중 → 주식 수 (라운딩·최소금액·거래대금 상한)
    orders.py    결정론적 주문 식별자 · 분할 집행
    pipeline.py  8단계를 순서대로
"""

from quant_rl_trading.executor.guards import (
    GateResult,
    KillswitchState,
    check_circuit_breaker,
    check_data_quality,
    check_defer,
    check_killswitch,
    engage,
    killswitch_state,
    release,
    should_engage,
)
from quant_rl_trading.executor.orders import (
    PlannedOrder,
    SliceParams,
    client_order_id,
    limit_price,
    plan_slices,
    session_id,
    split,
)
from quant_rl_trading.executor.pipeline import (
    ExecutionResult,
    action_reflection_rate,
    record_orders,
    record_realized_weights,
    run,
)
from quant_rl_trading.executor.sizing import Sized, SizingParams, Skipped, Target, size_orders

__all__ = [
    "ExecutionResult",
    "GateResult",
    "KillswitchState",
    "PlannedOrder",
    "SliceParams",
    "Sized",
    "SizingParams",
    "Skipped",
    "Target",
    "action_reflection_rate",
    "check_circuit_breaker",
    "check_data_quality",
    "check_defer",
    "check_killswitch",
    "client_order_id",
    "engage",
    "killswitch_state",
    "limit_price",
    "plan_slices",
    "record_orders",
    "record_realized_weights",
    "release",
    "run",
    "session_id",
    "should_engage",
    "size_orders",
    "split",
]
