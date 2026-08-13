"""Executor — 8단계를 순서대로. **순서가 곧 안전이다** (agents.md §7).

    1. 킬스위치 latch 확인    ← 걸려 있으면 여기서 종료
    2. 데이터 품질 게이트
    3. 시장 서킷 브레이커
    4. defer 게이트
    5. 목표비중 → 주식 수
    6. 거래대금 상한 · 청산 제약
    7. 주문 분할 집행
    8. 실현 비중 기록 → Allocator 되먹임   ← 절대 생략 금지

**8번이 빠지면 RL 은 학습할 수 없다** (불변식 7). 소액 구간에서는 5번 라운딩
때문에 목표와 실현이 크게 벌어지고, 되먹임이 없으면 Allocator 는 자기가 하지
않은 행동으로 벌을 받는다.

**Executor 안에는 AI 가 없다** (불변식 6). 이 파일에 LLM 호출이 들어오는 날
마지막 안전장치가 예측 불가능해진다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from quant_rl_trading.executor import guards
from quant_rl_trading.executor import orders as orders_module
from quant_rl_trading.executor.orders import PlannedOrder, SliceParams
from quant_rl_trading.executor.sizing import (
    Sized,
    SizingParams,
    Skipped,
    Target,
    replace_weight,
    size_orders,
)
from quant_rl_trading.schemas.order import Side

if TYPE_CHECKING:
    from quant_rl_trading.replay.clock import Clock
    from quant_rl_trading.store import Store

ORDERS = "orders"
REALIZED_WEIGHTS = "realized_weights"
SOURCE = "executor"


@dataclass
class ExecutionResult:
    session_id: str
    as_of: datetime
    market: str
    planned: tuple[PlannedOrder, ...] = ()
    skipped: tuple[Skipped, ...] = ()
    blocked_by: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return bool(self.blocked_by)


def run(
    store: Store,
    clock: Clock,
    *,
    as_of: datetime,
    market: str,
    targets: list[Target],
    holdings: dict[str, int],
    equity: float,
    market_open: datetime | None = None,
    board: str = "KOSPI",
    liquidation_only: bool = False,
) -> ExecutionResult:
    """한 세션의 집행. 주문을 만들고 기록한다.

    실제 전송은 여기서 하지 않는다 — 브로커 어댑터가 ``planned`` 를 받아
    보낸다. 나누는 이유는 shadow 운용 때문이다. **결정과 전송이 한 함수에
    있으면 shadow 는 '전송만 빼고' 를 코드 분기로 만들어야 하고, 그러면
    shadow 와 실전이 다른 코드를 타게 된다** (불변식 5).
    """
    session = orders_module.session_id(as_of=as_of, market=market)
    result = ExecutionResult(session_id=session, as_of=as_of, market=market)

    # 1. 킬스위치 — 가장 먼저. 다른 어떤 판단보다 앞선다.
    switch = guards.check_killswitch(store, as_of=as_of)
    if not switch:
        # **매도는 막지 않는다.** 청산까지 막는 안전장치는 빠져나올 길을 막는
        # 것이라 안전장치가 아니다. force_liquidation 이면 전량 청산까지 간다.
        liquidation_only = True
        result.notes.append(
            f"{switch.reason} — {'전 포지션 청산' if switch.force_liquidation else '신규매수만 차단'}"
        )
        if switch.force_liquidation:
            targets = [
                replace_weight(target, 0.0) for target in targets
            ] + [
                Target(entity_id=entity, weight=0.0, price=0.0, adv_value=0.0)
                for entity in holdings
                if entity not in {item.entity_id for item in targets}
            ]

    entities = [target.entity_id for target in targets]

    # 2. 데이터 품질
    quality = guards.check_data_quality(
        store, as_of=as_of, market=market, entities=entities
    )
    if not quality:
        result.blocked_by = quality.reason
        return result

    # 3. 서킷 브레이커
    breaker = guards.check_circuit_breaker(store, as_of=as_of, board=board)
    if not breaker:
        # 급락일에도 청산은 허용한다. 못 빠져나오게 막는 안전장치는 위험하다.
        liquidation_only = True
        result.notes.append(f"{breaker.reason} — 청산만 허용")
    elif breaker.reason:
        result.notes.append(breaker.reason)

    # 4. defer 게이트 — 신규매수만 보류한다.
    if market_open is not None:
        defer = guards.check_defer(store, as_of=as_of, market_open=market_open)
        if not defer:
            liquidation_only = True
            result.notes.append(f"{defer.reason} — 신규매수 보류")

    # 5~6. 수량 변환과 상한
    sizing_params = SizingParams.from_store(store, as_of=as_of)
    sized, skipped = size_orders(
        targets=targets, holdings=holdings, equity=equity, params=sizing_params
    )
    if liquidation_only:
        held_back = [item for item in sized if item.side is Side.BUY]
        for item in held_back:
            skipped.append(Skipped(item.entity_id, item.target_weight, "신규매수 차단"))
        sized = [item for item in sized if item.side is Side.SELL]
    result.skipped = tuple(skipped)

    # 7. 분할 집행
    slice_params = SliceParams.from_store(store, as_of=as_of)
    planned: list[PlannedOrder] = []
    for item in sized:
        planned.extend(
            orders_module.plan_slices(
                entity_id=item.entity_id,
                side=item.side,
                quantity=item.quantity,
                reference_price=item.price,
                target_weight=item.target_weight,
                session=session,
                params=slice_params,
                # 시장가는 청산과 킬스위치 발동 때만.
                market_order=liquidation_only and item.side is Side.SELL,
            )
        )
    result.planned = tuple(planned)

    record_orders(store, clock, planned=planned, as_of=as_of, market=market)
    # 8. **절대 생략 금지.**
    record_realized_weights(
        store, clock, sized=sized, targets=targets, as_of=as_of,
        market=market, session=session,
    )
    return result


def record_orders(
    store: Store,
    clock: Clock,
    *,
    planned: list[PlannedOrder],
    as_of: datetime,
    market: str,
) -> int:
    """주문 기록. **같은 세션을 두 번 돌려도 한 번만 남는다.**

    자연키가 (entity_id, session_id, slice_seq) 라 재시작 후 같은 주문을 다시
    만들어도 창고가 중복을 거부한다 — 멱등성의 마지막 방어선이다.
    """
    if not planned:
        return 0
    run_id = f"orders-{planned[0].session_id}"
    if store.ingest_run_recorded(ORDERS, run_id):
        return 0
    observed_at = clock.now()
    return store.append(
        ORDERS,
        [
            item.row(as_of=as_of, observed_at=observed_at, market=market, status="planned")
            for item in planned
        ],
        ingest_run_id=run_id,
        source=SOURCE,
    )


def record_realized_weights(
    store: Store,
    clock: Clock,
    *,
    sized: list[Sized],
    targets: list[Target],
    as_of: datetime,
    market: str,
    session: str,
) -> int:
    """8단계. 목표 비중과 **실제 집행된 비중**을 나란히 남긴다 (불변식 7).

    주문을 못 낸 종목도 남긴다 — 목표 5% 였는데 라운딩으로 0주가 된 사실이
    기록에 없으면, Allocator 는 자기가 5% 를 샀다고 믿는다.
    """
    realized = {item.entity_id: item.realized_weight for item in sized}
    run_id = f"realized-{session}"
    if store.ingest_run_recorded(REALIZED_WEIGHTS, run_id):
        return 0
    observed_at = clock.now()
    rows = [
        {
            "entity_id": target.entity_id,
            "valid_from": as_of,
            "observed_at": observed_at,
            "source": SOURCE,
            "market": market,
            "session_id": session,
            "target_weight": target.weight,
            "realized_weight": realized.get(target.entity_id, 0.0),
        }
        for target in targets
    ]
    if not rows:
        return 0
    return store.append(REALIZED_WEIGHTS, rows, ingest_run_id=run_id, source=SOURCE)


def action_reflection_rate(store: Store, *, as_of: datetime, lookback: int = 30) -> float:
    """**액션 반영률** — RL 이 낸 결정 중 실제로 집행된 비율.

    선행 프로젝트가 룰로 전락한 유력 원인은 안전장치가 RL 출력을 덮어쓴
    것이다. **30% 미만이면 그건 RL 이 아니라 룰 시스템이다** (CLAUDE.md).
    M4 전에도 계산해 둔다 — 룰 베이스라인에서도 같은 방식으로 덮이기 때문이다.
    """
    frame = store.get(REALIZED_WEIGHTS, as_of=as_of, lookback=lookback)
    if frame.empty:
        return 0.0
    target = frame["target_weight"].abs().sum()
    if target <= 0:
        return 0.0
    matched = (
        1.0 - (frame["target_weight"] - frame["realized_weight"]).abs().sum() / target
    )
    return max(0.0, min(1.0, float(matched)))
