"""Session — 하루의 의사결정 사이클을 한 번 돌린다.

백테스트와 라이브가 같은 코드를 쓰는 지점이 여기다. 바뀌는 것은 Clock 뿐이고,
``if backtest:`` 분기는 없다 (불변식 5).

M3 에서 Selector·Allocator·Executor 가 들어올 자리는 ``strategy`` 인자다.
지금은 그 자리에 무엇을 끼우든 파이프라인 자체가 결정론적인지를 먼저 증명한다.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from lattice.replay.clock import Clock, LiveClock, require_aware
from lattice.replay.events import EventLog, canonical_json, payload_hash
from lattice.replay.fills import Fill, FillParams, MarketState, simulate_fill
from lattice.schemas.order import Order
from lattice.store import Store

#: as_of 시점의 관측을 받아 주문을 내는 것. M3 에서 Selector→Allocator→Executor 가 들어온다.
Strategy = Callable[[pd.DataFrame, datetime], Sequence[Order]]

#: 종목별 시장 상태를 만드는 것. 4단계 이후 실제 수급·변동성으로 대체된다.
MarketStateBuilder = Callable[[pd.DataFrame, str], MarketState]


@dataclass(frozen=True)
class SessionResult:
    run_id: str
    as_of: datetime
    orders: tuple[Order, ...]
    fills: tuple[Fill, ...]

    def digest(self) -> str:
        """주문·체결의 지문.

        결정론 검증은 이 값으로 한다. 벽시계(ts_wall)는 리플레이마다 당연히
        다르므로 비교 대상에서 뺀다 — 넣으면 테스트가 영원히 실패한다.
        """
        return payload_hash(
            {
                "orders": [order.canonical() for order in self.orders],
                "fills": [fill.canonical() for fill in self.fills],
            }
        )

    def serialized(self) -> str:
        return canonical_json(
            {
                "orders": [order.canonical() for order in self.orders],
                "fills": [fill.canonical() for fill in self.fills],
            }
        )


def run_session(
    *,
    store: Store,
    clock: Clock,
    run_id: str,
    strategy: Strategy,
    market_state: MarketStateBuilder,
    params: FillParams | None = None,
    lookback: int = 20,
    wall_clock: Clock | None = None,
) -> SessionResult:
    """관측 → 주문 → 체결. 각 단계를 이벤트 로그에 남긴다.

    as_of 는 Clock 에서 온다. 인자로 따로 받지 않는다 — 두 개가 되면
    언젠가 서로 어긋나고, 어긋난 쪽이 미래를 본다.
    """
    as_of = require_aware("clock.now()", clock.now())
    log = EventLog(
        store=store, clock=clock, run_id=run_id, wall_clock=wall_clock or LiveClock()
    )
    effective = params or FillParams.from_store(store, as_of=as_of)

    observation = store.get("prices", as_of=as_of, lookback=lookback)
    log.record(
        "observe",
        "store",
        {"rows": len(observation), "entities": sorted(set(observation["entity_id"]))},
    )

    orders = tuple(strategy(observation, as_of))
    log.record("decide", "strategy", {"orders": [order.canonical() for order in orders]})

    fills = tuple(
        simulate_fill(order, market_state(observation, order.entity_id), effective)
        for order in orders
    )
    log.record("execute", "fill_simulator", {"fills": [fill.canonical() for fill in fills]})

    log.flush()
    return SessionResult(run_id=run_id, as_of=as_of, orders=orders, fills=fills)
