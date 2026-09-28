"""E1 집행 계획 읽기 — AI 부품이 세션 전에 적은 팔로 조각 수·간격을 바꾼다. 순수 코드.

계약: docs/design/execution-safety.md "E1 집행 계획 계약". 노출 행동(``selector.exposure.learned_decision``)과
같은 모양이다 — 부품이 창고 ``execution_plans`` 에 (세션·종목·팔)을 적고, 여기서는 **읽기만** 한다.
**이 파일에 모델 import 가 들어오는 날 불변식 6 이 깨진다**(tests/executor/test_plans.py 가 소스를 검사한다).

바꿀 수 있는 것은 조각 수와 간격뿐이다. 지정가 여유(``max_slippage``)는 언제나 설정값 그대로 넘긴다.
``execution.plan_source`` 가 ``"rule"``(기본)이면 표를 읽지도 않는다 — 지금 실전 동작 그대로다.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import pandas as pd

from quant_rl_trading.executor import orders
from quant_rl_trading.executor.orders import PlannedOrder, SliceParams
from quant_rl_trading.store.errors import ConfigNotFound

if TYPE_CHECKING:
    from quant_rl_trading.store import Store

PLANS = "execution_plans"
RULE = "rule"
#: **등록된 행동 공간** — (조각 수, 간격 초). 설정에 두면 판정 뒤에 팔이 조용히 바뀐다. ``4x60`` 은 C0(지금 규칙)이라
#: 설정 기본값과 같아야 한다(테스트가 고정).
ARMS: dict[str, tuple[int, int]] = {
    "4x60": (4, 3600),
    "2x30": (2, 1800),
    "1x0": (1, 0),
    "8x30": (8, 1800),
}
#: 계획을 찾는 창(달력일)과 낡음 한도 — 노출 행동(``ACTION_LOOKBACK_DAYS``·``ACTION_MAX_AGE``)과 같은 값이다.
PLAN_LOOKBACK_DAYS = 10
PLAN_MAX_AGE = timedelta(hours=12)


def plan_source(store: Store, *, as_of: datetime) -> str:
    """``execution.plan_source`` — ``"rule"``(기본, 옛 동작) 또는 부품 이름. 샌드박스 덮어쓰기로만 켠다."""
    try:
        value = str(store.config("execution.plan_source", as_of=as_of) or RULE)
    except ConfigNotFound:
        return RULE
    return value or RULE


def fresh_arms(frame: pd.DataFrame, *, as_of: datetime, source: str) -> dict[str, str]:
    """계획 행 → {종목: 팔}. 그 부품 것만, 세션 기준 시각에서 12시간 안의 것만, 아는 팔만."""
    if frame.empty or source == RULE:
        return {}
    rows = frame[frame["source"].astype(str) == source]
    rows = rows[pd.to_datetime(rows["valid_from"], utc=True) >= pd.Timestamp(as_of) - PLAN_MAX_AGE]
    rows = rows[rows["arm"].astype(str).isin(ARMS)]
    if rows.empty:
        return {}
    latest = rows.sort_values(["valid_from", "observed_at"]).drop_duplicates("entity_id", keep="last")
    return {str(entity): str(arm) for entity, arm in zip(latest["entity_id"], latest["arm"], strict=True)}


def read_arms(store: Store, *, as_of: datetime, market: str) -> dict[str, str]:
    """이 세션의 종목별 팔. ``plan_source`` 가 rule 이면 **표를 읽지 않고** 빈 dict — 전 종목 규칙."""
    source = plan_source(store, as_of=as_of)
    if source == RULE:
        return {}
    frame = store.get(PLANS, as_of=as_of, lookback=PLAN_LOOKBACK_DAYS, market=market,
                      columns=["entity_id", "valid_from", "observed_at", "source", "arm"])
    return fresh_arms(frame, as_of=as_of, source=source)


def params_for(base: SliceParams, arm: str | None) -> SliceParams:
    """팔 하나 → SliceParams. 팔이 없거나 모르면 ``base``(규칙) 그대로. 지정가 여유는 언제나 ``base`` 의 것."""
    if arm is None or arm not in ARMS:
        return base
    count, interval = ARMS[arm]
    return replace(base, slice_count=count, slice_interval_sec=interval)


def order_params(base: SliceParams, arms: dict[str, str], *, entity_id: str, market_order: bool) -> SliceParams:
    """주문 하나의 분할 규칙. **시장가(청산·킬스위치)는 언제나 규칙** — 안전 경로에 계획을 얹지 않는다."""
    return base if market_order else params_for(base, arms.get(entity_id))


def due_slices(
    planned: list[PlannedOrder], *, base: SliceParams, arms: dict[str, str], elapsed_sec: float
) -> list[PlannedOrder]:
    """``orders.due_slices`` 를 종목마다 그 팔의 간격으로. ``arms`` 가 비면(규칙) 그 함수를 그대로 부른다."""
    if not arms:
        return orders.due_slices(planned, params=base, elapsed_sec=elapsed_sec)
    out: list[PlannedOrder] = []
    for item in planned:
        params = order_params(base, arms, entity_id=item.order.entity_id,
                              market_order=item.order.limit_price is None)
        interval = int(params.slice_interval_sec)
        if interval <= 0 or item.slice_seq * interval <= elapsed_sec:
            out.append(item)
    return out
