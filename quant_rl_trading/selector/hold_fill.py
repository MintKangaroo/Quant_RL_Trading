"""보유일 잔여 채움 — 직전 재조정이 못 끝낸 몫만 마저 낸다 (selector.md §5 7번 "보유일 잔여 채움", 2026-10-04).

재조정 주기(``cadence``)의 보유일엔 명단도 비중도 안 바꾼다. 그런데 재조정일 주문이 다 체결된다는 보장은 없다 — 지정가 미체결,
킬스위치, 현금 한도. 예전엔 보유일이 그 잔여를 아무도 안 냈다. 2026-09-28 재조정이 매도 21% 중 10% 만 체결되고 매수를 현금
한도에서 멈춘 뒤, 주식 비중이 목표 100% 대비 90% 로 다음 재조정까지 묶였다(docs/diag/paper-cost-decomposition-2026-10.md §4).

## 무엇을 채우나 — 비중이 아니라 **주식 수**

직전 재조정의 목표를 **그날의 주식 수**로 고정한다::

    목표 주식 수 = ⌊ 목표 비중 × 재조정일 자본 × (지금 보정가 / 재조정일 보정가) ÷ 지금 원주가 ⌋

``목표 비중 × 재조정일 자본 ÷ 재조정일 가격`` 이 재조정 사이징이 낸 수량이다(``executor.sizing`` 과 같은 내림). 보정가 비율은
그 사이 분할·병합을 오늘 주식 단위로 옮길 뿐 값은 같다. 비중으로 다시 맞추면 가격 표류를 매일 되돌리는 매매가 된다 — 그건
매일 재조정이고, 시행 AO 가 없앤 것이다. 주식 수로 고정하면 "그날 못 산·못 판 주식" 만 남는다.

**매도 잔여도 같은 규칙이다.** 지금 보유가 목표 주식 수보다 많은 것은 표류가 아니라 재조정이 못 판 몫이다(주식 수는 표류하지
않는다). 매수만 채우면 매도 대금으로 사려던 몫이 현금으로 남고, 재조정일에 팔기로 한 종목을 다음 재조정까지 든다 — 재조정일
규칙(목표까지 사고판다) 그대로 양쪽을 마저 한다.

## 오래된 목표를 쫓지 않는다

**직전 재조정 세션 하나만** 본다(``cadence`` 로 찾은 마지막 재조정일). 그 세션 기록(``realized_weights``)이 없으면 — 정지로
재조정이 안 돌았으면 — 채우지 않는다. 더 옛 목표로 물러서지 않는다. 다음 재조정일이 오면 그날 새 목표가 이것을 대체한다.

## 백테스트·라이브가 같은 코드 (불변식 5)

분기는 없다. 백테스트는 체결이 다 되므로 목표 주식 수 = 보유 → 잔여 0 → 주문 0, 결과가 그대로다. 잔여는 체결이 안 된
곳에서만 생기고, 그건 라이브든 백테스트든(거래대금 상한 등) 같은 규칙으로 채운다. 집행 게이트(킬스위치·데이터 품질·서킷·
defer)·현금 한도·최소 주문금액은 집행기가 그대로 건다 — 여기는 목표만 낸다.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from quant_rl_trading.collectors.market_hours import Market, trading_days
from quant_rl_trading.selector import cadence as cadence_module
from quant_rl_trading.store import ConfigNotFound
from quant_rl_trading.store.prices import read_prices

if TYPE_CHECKING:
    from quant_rl_trading.store import Store

REALIZED_WEIGHTS = "realized_weights"
EVENTS = "events"
#: 직전 재조정일을 찾는 창(달력일). 주기 10세션에 연휴를 넉넉히 넘긴다(``cadence.NEXT_SEARCH_DAYS`` 와 같은 뜻).
SEARCH_DAYS = 60


@dataclass(frozen=True)
class RebalanceTarget:
    """직전 재조정 세션이 정한 것. 비중은 **노출 배수가 곱해진 뒤**의 목표다(``realized_weights.target_weight``)."""

    session_id: str
    as_of: datetime
    equity: float
    scale: float
    weights: dict[str, float]


@dataclass
class HoldFill:
    """보유일에 마저 낼 목표 주식 수. 비어 있으면 잔여 없음 — 옛 동작(주문 0)."""

    target: RebalanceTarget | None = None
    shares: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def last_rebalance_day(store: Store, *, as_of: datetime, market: str) -> date | None:
    """as_of **전** 마지막 재조정 세션의 날짜. 매일 재조정이거나 창 안에 없으면 None."""
    every, anchor = cadence_module.settings(store, as_of=as_of, market=market)
    if every <= 1:
        return None
    today = as_of.date()
    days = [d for d in trading_days(Market(market), today - timedelta(days=SEARCH_DAYS), today) if d < today]
    for day in reversed(days):
        if cadence_module.decide(anchor, every, day, Market(market)).rebalance:
            return day
    return None


def _event_payload(store: Store, *, as_of: datetime, entity: str, stage: str) -> dict[str, Any] | None:
    frame = store.get(EVENTS, as_of=as_of, lookback=SEARCH_DAYS, entity=entity)
    if frame.empty:
        return None
    rows = frame[frame["stage"] == stage]
    if rows.empty:
        return None
    payload = rows.sort_values(["valid_from", "seq"]).iloc[-1]["payload"]
    try:
        return json.loads(payload) if isinstance(payload, str) else dict(payload)
    except (TypeError, ValueError):
        return None


def rebalance_target(store: Store, *, as_of: datetime, market: str) -> tuple[RebalanceTarget | None, str]:
    """직전 재조정 세션의 목표·자본·배수. 못 찾으면 (None, 사유)."""
    day = last_rebalance_day(store, as_of=as_of, market=market)
    if day is None:
        return None, ""
    session = f"{market}-{day.isoformat()}"
    frame = store.get(REALIZED_WEIGHTS, as_of=as_of, lookback=SEARCH_DAYS)
    if not frame.empty:
        frame = frame[(frame["session_id"] == session) & (frame["market"] == market)]
    if frame.empty:
        return None, f"직전 재조정 {session} 기록이 없다 — 잔여 채움 안 함(옛 목표로 물러서지 않는다)"
    # 세션의 자본 = 그 세션이 사이징에 쓴 값(observe 단계, accounting 이 낸 NAV — 다시 계산하지 않는다).
    observed = _event_payload(store, as_of=as_of, entity=f"session-{session}", stage="observe")
    equity = float(observed.get("nav", 0.0)) if observed else 0.0
    if not math.isfinite(equity) or equity <= 0:
        return None, f"직전 재조정 {session} 의 자본 기록이 없다 — 잔여 채움 안 함"
    exposure = _event_payload(store, as_of=as_of, entity=f"session-{session}", stage="exposure")
    try:
        scale = float(exposure["scale"]) if exposure else 1.0
    except (TypeError, ValueError, KeyError):
        scale = 1.0
    weights = {
        str(entity): float(weight)
        for entity, weight in zip(frame["entity_id"], frame["target_weight"], strict=True)
        if weight is not None and math.isfinite(float(weight))
    }
    target = RebalanceTarget(
        session_id=session, as_of=frame["valid_from"].max().to_pydatetime(), equity=equity,
        scale=scale if scale > 0 else 1.0, weights=weights,
    )
    return target, ""


def residual_shares(
    target: RebalanceTarget,
    *,
    holdings: dict[str, int],
    growth: dict[str, float],
    prices: dict[str, float],
    equity: float,
    min_weight: float,
) -> dict[str, int]:
    """목표 주식 수와 보유가 ``min_weight``(자본 대비) 이상 어긋난 종목만 → 목표 주식 수. 순수 함수.

    ``growth`` = 지금 보정가 / 재조정일 보정가, ``prices`` = 지금 원주가. 둘 중 하나라도 없으면 그 종목은 뺀다 — 모르는 값으로
    주식 수를 내면 그것이 곧 틀린 주문이다.

    종목 상한은 여기서 자르지 않는다 — 사이징(`executor/sizing`)이 위험 한도 잣대로 재조정일·보유일 공통으로 자른다.
    """
    out: dict[str, int] = {}
    if equity <= 0:
        return out
    for entity in dict.fromkeys([*target.weights, *holdings]):
        weight = target.weights.get(entity)
        held = int(holdings.get(entity, 0))
        if weight is None:
            # 재조정 기록에 없는 보유 — 재조정 뒤에 생겼거나 기록이 빠졌다. 모르는 것은 건드리지 않는다.
            continue
        price, ratio = prices.get(entity, 0.0), growth.get(entity)
        if price <= 0 or ratio is None or not math.isfinite(ratio) or ratio <= 0:
            continue
        # 내림 — executor.sizing 과 같은 이유(넘치는 쪽으로 틀리면 현금 부족 → 거부 → 재시도).
        desired = math.floor(weight * target.equity * ratio / price + 1e-9)
        if desired == held:
            continue
        if abs(desired - held) * price / equity < min_weight:
            continue
        out[entity] = desired
    return out


def plan(
    store: Store,
    *,
    as_of: datetime,
    market: str,
    target: RebalanceTarget | None,
    holdings: dict[str, int],
    prices: dict[str, float],
    equity: float,
) -> HoldFill:
    """보유일의 잔여 채움 목표. 세션(``session/daily``)이 보유일에만 부른다.

    ``target`` 은 ``rebalance_target`` 의 결과다 — 세션이 시세를 읽기 **전에** 불러 목표 종목(못 산 종목 포함)의 시세를 같이
    읽는다. None 이면 채우지 않는다.
    """
    result = HoldFill()
    if target is None:
        return result
    result.target = target
    try:
        min_weight = float(store.config("selector.hold_fill_min_weight", as_of=as_of))
    except ConfigNotFound:
        # 키가 없던 시점(과거 재생) = 옛 동작. 채우지 않는다.
        return result
    entities = sorted(set(target.weights) | {e for e, q in holdings.items() if q})
    growth = _growth(store, as_of=as_of, since=target.as_of, market=market, entities=entities)
    result.shares = residual_shares(
        target, holdings=holdings, growth=growth, prices=prices, equity=equity, min_weight=min_weight,
    )
    if result.shares:
        buys = sum(1 for e, q in result.shares.items() if q > holdings.get(e, 0))
        result.notes.append(
            f"보유일 잔여 채움 — 직전 재조정 {target.session_id} 목표 주식 수까지 {len(result.shares)}종목"
            f"(매수 {buys} · 매도 {len(result.shares) - buys})"
        )
    return result


def _growth(
    store: Store, *, as_of: datetime, since: datetime, market: str, entities: list[str]
) -> dict[str, float]:
    """종목별 (as_of 마지막 보정 종가) / (재조정 as_of 마지막 보정 종가). 한 프레임에서 읽어 기준이 같다."""
    if not entities:
        return {}
    frame = read_prices(
        store, as_of=as_of, entity=entities, lookback=max((as_of - since).days + 15, 15), market=market,
        columns=["close", "adj_factor"], adjusted=True,
    )
    if frame.empty:
        return {}
    out: dict[str, float] = {}
    for entity, group in frame.sort_values("valid_from").groupby("entity_id"):
        closes = group[group["close"].astype(float) > 0]
        before = closes[closes["valid_from"] <= since]
        if before.empty or closes.empty:
            continue
        first, last = float(before["close"].iloc[-1]), float(closes["close"].iloc[-1])
        if first > 0:
            out[str(entity)] = last / first
    return out
