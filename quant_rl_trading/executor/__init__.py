"""Executor — 목표 비중을 주문으로 바꾸고, 그 전에 네 겹의 안전장치를 지난다.

**Executor 안에는 AI 가 없다. 순수 코드만** (불변식 6). 마지막 안전장치는
예측 가능해야 한다 — 왜 막혔는지 사람이 읽고 30초 안에 판단할 수 있어야 한다.

    guards.py    킬스위치 latch · 데이터 게이트 · 서킷브레이커 · defer
    sizing.py    목표비중 → 주식 수 (라운딩·최소금액·거래대금 상한)
    orders.py    결정론적 주문 식별자 · 분할 집행
    pipeline.py  8단계를 순서대로
    lifecycle.py 미체결 주문의 생애주기 판단 (재호가 타이머 · 재시도 · 취소)
    supervise.py 그 판단을 실제 브로커 호출로 옮기는 루프
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
from quant_rl_trading.executor.lifecycle import (
    Action,
    ActionType,
    LifecycleParams,
    OpenOrder,
    OrderStatus,
    apply_fill,
    close_session,
    decide,
    open_order_from_planned,
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
from quant_rl_trading.executor.supervise import (
    SupervisionResult,
    close,
    cumulative_from_sync,
    register,
    step,
)
from quant_rl_trading.executor.ticks import round_to_tick, tick_size

__all__ = [
    "Action",
    "ActionType",
    "ExecutionResult",
    "GateResult",
    "KillswitchState",
    "LifecycleParams",
    "OpenOrder",
    "OrderStatus",
    "PlannedOrder",
    "Sized",
    "SizingParams",
    "Skipped",
    "SliceParams",
    "SupervisionResult",
    "Target",
    "action_reflection_rate",
    "apply_fill",
    "check_circuit_breaker",
    "check_data_quality",
    "check_defer",
    "check_killswitch",
    "client_order_id",
    "close",
    "close_session",
    "cumulative_from_sync",
    "decide",
    "engage",
    "killswitch_state",
    "limit_price",
    "open_order_from_planned",
    "plan_slices",
    "record_orders",
    "record_realized_weights",
    "register",
    "release",
    "round_to_tick",
    "run",
    "session_id",
    "should_engage",
    "size_orders",
    "split",
    "step",
    "tick_size",
]
