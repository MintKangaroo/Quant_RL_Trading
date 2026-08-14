"""트레이딩 화면 집계 — **지금 포트폴리오가 어떤 상태인가.**

목업(`mock_trading.py`)이 있던 자리다. 그때는 회계도 주문도 없었고, 화면은
레이아웃만 있었다. 지금은 `nav_daily` · `trades` · `orders` ·
`realized_weights` · `killswitch` 가 전부 실재한다.

## 이 화면은 수익률 화면이 아니라 리스크 예산 화면이다

첫 질문은 "얼마 벌었나" 가 아니라 **"예산을 얼마나 쓰고 있나"** 다
(dashboard.md §1). 그래서 낙폭이 밴드(12/22/30%) 위에 얹혀 나오고, 액션
반영률이 KPI 스트립에 상시 올라간다.

## NAV 를 여기서 계산하지 않는다

전부 `accounting/` 에서 온다 (불변식: accounting.md §8). 화면이 자기 NAV 를
접으면 리포트·보상 함수와 어긋나고, 어긋난 순간 어느 쪽이 맞는지 판정할
방법이 없다.

## 없는 것은 없다고 말한다

- **Allocator 는 M4 다.** Q값·행동확률 자리에 지금 있는 것은 룰 베이스라인의
  목표 비중과 Analyst 기여도다. 그 사실을 응답에 `decision.engine` 으로 싣고
  화면이 그대로 띄운다. RL 이 아닌 것을 RL 처럼 그리면 M4 에서 무엇이 달라졌는지
  아무도 모른다
- 체결 지연(latency)·호가는 실거래 기록이 없으면 ``null`` 이다. 0 으로 채우면
  "빠르다" 로 읽힌다
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd

from quant_rl_trading.accounting import ledger as ledger_module
from quant_rl_trading.accounting import snapshot as snapshot_module
from quant_rl_trading.accounting.rates import Rates
from quant_rl_trading.allocator.baseline import AllocatorParams
from quant_rl_trading.executor import guards
from quant_rl_trading.executor import pipeline as executor_pipeline
from quant_rl_trading.selector.combine import contributions
from quant_rl_trading.selector.weights import analyst_weights
from quant_rl_trading.store import Store

NAV_DAILY = "nav_daily"
ORDERS = "orders"
TRADES = "trades"
SIGNALS = "signals"
PRICES = "prices"
UNIVERSE = "universe"
REALIZED_WEIGHTS = "realized_weights"

#: 주문·체결 표에 싣는 최대 행수. 화면은 한 눈에 보는 것이지 원장이 아니다.
ORDER_ROWS = 40

#: 워치리스트 상한. 후보 수 상한(selector.n_candidates)과 별개로, 표가 화면을
#: 넘기지 않게 자른다.
WATCHLIST_ROWS = 12

#: 에쿼티 커브에 실을 최대 세션. 넘으면 앞을 자른다 — 화면 폭이 유한하다.
EQUITY_SESSIONS = 250


@dataclass(frozen=True)
class Context:
    """한 요청이 보는 것. 여러 패널이 같은 장부를 다시 접지 않게 한 번만 만든다."""

    as_of: datetime
    market: str
    book: Any
    snapshot: snapshot_module.Snapshot
    prices: dict[str, float]


def build_context(store: Store, clock: Any, *, as_of: datetime, market: str) -> Context:
    """장부와 스냅샷을 한 번만 접는다.

    회계는 기록에서 매번 재구성한다(ledger.py). 패널마다 다시 부르면 같은
    계산을 대여섯 번 하게 된다.
    """
    rates = Rates.from_store(store, as_of=as_of)
    book = ledger_module.build_book(store, as_of=as_of, rates=rates)
    snapshot = snapshot_module.take(store, clock, as_of=as_of, book=book)
    prices = snapshot_module.last_prices(
        store, as_of=as_of, entities=sorted(book.positions)
    )
    return Context(
        as_of=as_of, market=market, book=book, snapshot=snapshot, prices=prices
    )


# -- KPI -----------------------------------------------------------------------


def kpis(store: Store, context: Context) -> dict[str, Any]:
    """상단 스트립. **낙폭과 액션 반영률이 수익률과 나란히 선다.**

    선행 프로젝트가 룰로 전락한 것을 늦게 알아챈 이유가 이 숫자가 화면에
    없어서였다 (CLAUDE.md 재발 방지 지표).
    """
    valuation = context.snapshot.valuation
    as_of = context.as_of
    equity = valuation.equity_kr + valuation.equity_us * valuation.fx_rate
    nav = valuation.nav
    reflection = executor_pipeline.action_reflection_rate(store, as_of=as_of)
    floor = float(store.config("allocator.action_reflection_floor", as_of=as_of))

    previous = ledger_module.previous_snapshot(store, as_of=as_of)
    cumulative = None
    if previous is not None:
        # 누적수익률은 지수에서 온다. NAV 비율로 재면 입금이 수익이 된다.
        cumulative = context.snapshot.index_value / 100.0 - 1.0

    return {
        "nav": nav,
        "nav_after_tax": valuation.nav_after_tax,
        "cash_krw": valuation.cash_krw,
        "cash_usd": valuation.cash_usd,
        "equity": equity,
        "equity_kr": valuation.equity_kr,
        "equity_us": valuation.equity_us,
        "fx_rate": valuation.fx_rate,
        "daily_return": context.snapshot.twr_return,
        "cumulative_return": cumulative,
        "index_value": context.snapshot.index_value,
        "drawdown": context.snapshot.drawdown,
        "exposure": equity / nav if nav > 0 else None,
        "action_reflection": reflection,
        "action_reflection_floor": floor,
        "positions": len([p for p in context.book.positions.values() if p.quantity > 0]),
    }


# -- 리스크 --------------------------------------------------------------------


def risk(store: Store, context: Context) -> dict[str, Any]:
    """리스크 예산. **임계치는 전부 store.config 에서 온다** (불변식 10).

    화면이 12/22/30 을 직접 들면 학습 설정과 어긋나고, 어긋난 화면은 안전해
    보이는 쪽으로 틀린다.
    """
    as_of = context.as_of
    reward = store.config("reward", as_of=as_of)
    killswitch_config = store.config("killswitch", as_of=as_of)
    allocator = AllocatorParams.from_store(store, as_of=as_of)
    switch = guards.check_killswitch(store, as_of=as_of)

    drawdown = abs(context.snapshot.drawdown)
    free = float(reward["drawdown_free"])
    warn = float(reward["drawdown_warn"])
    hard = float(reward["drawdown_hard"])
    if drawdown < free:
        band, message = "free", f"자유구간 · 페널티 없음 · 여유 {(free - drawdown) * 100:.1f}%p"
    elif drawdown < warn:
        band, message = "warn", f"페널티 구간 · 한계까지 {(hard - drawdown) * 100:.1f}%p"
    else:
        band = "hard"
        message = f"급증 구간 · 신규매수 제한 · 한계까지 {(hard - drawdown) * 100:.1f}%p"

    orders = store.get(ORDERS, as_of=as_of, lookback=5)
    rejected = int((orders["status"] == "rejected").sum()) if not orders.empty else 0
    total_orders = len(orders)

    valuation = context.snapshot.valuation
    exposure = (
        (valuation.equity_kr + valuation.equity_us * valuation.fx_rate) / valuation.nav
        if valuation.nav > 0
        else 0.0
    )

    return {
        "drawdown": drawdown,
        "band": band,
        "band_message": message,
        "bands": {"free": free, "warn": warn, "hard": hard},
        "daily_return": context.snapshot.twr_return,
        "killswitch": {
            "engaged": not bool(switch),
            "reason": switch.reason,
            "force_liquidation": switch.force_liquidation,
            "drawdown_trigger": float(killswitch_config["drawdown_trigger"]),
            "order_fail_rate": float(killswitch_config["order_fail_rate"]),
        },
        "exposure": exposure,
        "max_position_weight": allocator.max_position_weight,
        "cash_buffer": allocator.cash_buffer,
        "orders_total": total_orders,
        "orders_rejected": rejected,
        "reject_rate": rejected / total_orders if total_orders else None,
    }


def alerts(kpi: dict[str, Any], risk_state: dict[str, Any]) -> list[dict[str, str]]:
    """경고. **등급을 붙인다** — 전부 같은 색이면 아무것도 눈에 안 띈다."""
    out: list[dict[str, str]] = []
    if risk_state["killswitch"]["engaged"]:
        out.append({"level": "critical", "text": f"킬스위치: {risk_state['killswitch']['reason']}"})
    if risk_state["band"] == "hard":
        out.append({"level": "critical", "text": risk_state["band_message"]})
    elif risk_state["band"] == "warn":
        out.append({"level": "warning", "text": risk_state["band_message"]})

    reflection = kpi["action_reflection"]
    if reflection is not None and reflection < kpi["action_reflection_floor"]:
        out.append(
            {
                "level": "warning",
                "text": (
                    f"액션 반영률 {reflection * 100:.0f}% — "
                    f"하한 {kpi['action_reflection_floor'] * 100:.0f}% 미만이면 "
                    "RL 이 아니라 룰 시스템이다"
                ),
            }
        )
    rate = risk_state["reject_rate"]
    if rate is not None and rate > risk_state["killswitch"]["order_fail_rate"]:
        out.append({"level": "critical", "text": f"주문 거부율 {rate * 100:.1f}%"})
    if not out:
        out.append({"level": "info", "text": "경고 없음 — 임계치는 store.config 기준"})
    return out


# -- 포지션·워치리스트 ----------------------------------------------------------


def positions(store: Store, context: Context) -> list[dict[str, Any]]:
    """보유 종목. 평가액과 손익은 장부와 마지막 종가에서만 온다."""
    valuation = context.snapshot.valuation
    nav = valuation.nav
    names = _names(store, as_of=context.as_of, entities=list(context.book.positions))
    signals = _latest_scores(store, as_of=context.as_of)

    rows: list[dict[str, Any]] = []
    for entity_id, position in sorted(context.book.positions.items()):
        if position.quantity <= 0:
            continue
        price = context.prices.get(entity_id)
        value = price * position.quantity if price is not None else None
        # 취득단가는 수수료를 포함한 이동평균이다(book.Position.avg_cost) —
        # 실제로 나간 돈이라, 이걸로 재야 수익률이 부풀지 않는다.
        cost = position.book_value
        rows.append(
            {
                "entity_id": entity_id,
                "name": names.get(entity_id, entity_id),
                "quantity": position.quantity,
                "avg_price": position.avg_cost,
                "price": price,
                "value": value,
                "pnl": value - cost if value is not None else None,
                "pnl_pct": (value / cost - 1.0) if value is not None and cost > 0 else None,
                "weight": value / nav if value is not None and nav > 0 else None,
                "score": signals.get(entity_id),
            }
        )
    rows.sort(key=lambda row: row["value"] or 0.0, reverse=True)
    return rows


def _signal_of(target: float | None, held: float) -> str:
    """화면에 찍는 신호. **결정을 여기서 다시 내리지 않는다** — 기록된 목표
    비중과 보유를 읽어 이름만 붙인다.

    BUY  : 목표가 있는데 아직 덜 샀다
    HOLD : 목표만큼 들고 있다
    SELL : 들고 있는데 목표에서 빠졌다
    """
    if target is None or target <= 0:
        return "SELL" if held > 0 else "—"
    return "HOLD" if held > 0 else "BUY"


def watchlist(store: Store, context: Context) -> list[dict[str, Any]]:
    """오늘의 후보 상위 N. **합성 점수 순이다** — 등락률 순이 아니다.

    선정 파이프라인 전체를 화면에서 다시 돌리지 않는다. 그건 수 초가 걸리고,
    화면이 매매 결정을 다시 계산하면 화면과 실제 결정이 갈라질 수 있다.
    여기서 보여주는 것은 **기록된 신호**다.
    """
    as_of = context.as_of
    scores = _latest_scores(store, as_of=as_of)
    if not scores:
        return []
    top = sorted(scores.items(), key=lambda item: item[1], reverse=True)[:WATCHLIST_ROWS]
    entities = [entity for entity, _ in top]
    names = _names(store, as_of=as_of, entities=entities)
    quotes = _quotes(store, as_of=as_of, entities=entities, market=context.market)
    held = {
        entity: position
        for entity, position in context.book.positions.items()
        if position.quantity > 0
    }
    targets = _target_weights(store, as_of=as_of)

    rows: list[dict[str, Any]] = []
    for entity, score in top:
        position = held.get(entity)
        price = quotes.get(entity, {}).get("close")
        quantity = position.quantity if position else 0.0
        pnl = (
            (price - position.avg_cost) * quantity
            if position is not None and price is not None
            else None
        )
        rows.append(
            {
                "entity_id": entity,
                "name": names.get(entity, entity),
                "score": score,
                "price": price,
                "change": quotes.get(entity, {}).get("change"),
                "value": quotes.get(entity, {}).get("value"),
                "position": quantity,
                "target_weight": targets.get(entity),
                "signal": _signal_of(targets.get(entity), quantity),
                "pnl": pnl,
                "pnl_pct": (
                    (price / position.avg_cost - 1.0)
                    if position is not None and price is not None and position.avg_cost > 0
                    else None
                ),
            }
        )
    return rows


def _target_weights(store: Store, *, as_of: datetime) -> dict[str, float]:
    """마지막 세션의 목표 비중. 없으면 빈 dict — 0 으로 채우지 않는다."""
    frame = store.get(REALIZED_WEIGHTS, as_of=as_of, lookback=5)
    if frame.empty:
        return {}
    latest_session = frame.sort_values("valid_from")["session_id"].iloc[-1]
    rows = frame[frame["session_id"] == latest_session]
    return {
        str(row["entity_id"]): float(row["target_weight"])
        for row in rows.to_dict(orient="records")
    }


# -- 결정 ----------------------------------------------------------------------


def decision(store: Store, context: Context, *, entity_id: str | None) -> dict[str, Any]:
    """한 종목의 결정 분해.

    **M4 전에는 Q값이 없다.** 있는 것은 Analyst 기여도(score × confidence ×
    가중치)와 룰 베이스라인의 목표 비중이다. 응답에 그 사실을 적어 보낸다 —
    화면이 이것을 RL 처럼 그리면 M4 에서 무엇이 달라졌는지 알 수 없다.
    """
    as_of = context.as_of
    params = AllocatorParams.from_store(store, as_of=as_of)
    weights = analyst_weights(store, as_of=as_of, market=context.market)
    scores = _latest_scores(store, as_of=as_of)

    target = entity_id
    if target is None:
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        target = ranked[0][0] if ranked else None

    breakdown: list[dict[str, Any]] = []
    if target is not None:
        signals = store.get(SIGNALS, as_of=as_of, entity=target, lookback=5)
        for item in contributions(signals, weights, target):
            breakdown.append(
                {
                    "analyst": item.analyst,
                    "score": item.score,
                    "confidence": item.confidence,
                    "weight": item.weight,
                    "share": item.share,
                }
            )
    breakdown.sort(key=lambda row: abs(row["share"]), reverse=True)

    realized = store.get(REALIZED_WEIGHTS, as_of=as_of, lookback=5)
    target_weight = realized_weight = None
    if target is not None and not realized.empty:
        rows = realized[realized["entity_id"] == target]
        if not rows.empty:
            last = rows.sort_values(["valid_from", "observed_at"]).iloc[-1]
            target_weight = float(last["target_weight"])
            realized_weight = float(last["realized_weight"])

    position = context.book.positions.get(target) if target else None
    price = context.prices.get(target) if target else None
    return {
        # **RL 이 아니다.** 화면이 이 문자열을 그대로 띄운다.
        "engine": f"룰 베이스라인 ({params.baseline})",
        "rl_active": False,
        "engine_note": "Allocator(RL)는 M4 다. 지금 비중은 규칙이 정한다",
        "entity_id": target,
        "score": scores.get(target) if target else None,
        "contributions": breakdown,
        "target_weight": target_weight,
        "realized_weight": realized_weight,
        "position": {
            "quantity": position.quantity if position else 0.0,
            "avg_price": position.avg_cost if position else None,
            "price": price,
            "pnl": (
                (price - position.avg_cost) * position.quantity
                if position and price is not None
                else None
            ),
        },
    }


# -- 주문·체결 ------------------------------------------------------------------


def orders(store: Store, context: Context) -> list[dict[str, Any]]:
    """주문과 체결을 한 표로. 최근이 위다.

    체결은 ``trades``, 주문은 ``orders`` 다. 두 표를 따로 두면 "낸 주문이
    체결됐는지" 를 사람이 눈으로 맞춰야 한다.
    """
    as_of = context.as_of
    frame = store.get(ORDERS, as_of=as_of, lookback=10)
    trades = store.get(TRADES, as_of=as_of, lookback=10)
    filled: dict[str, dict[str, Any]] = {}
    if not trades.empty:
        for row in trades.to_dict(orient="records"):
            # 백테스트 체결의 order_id 는 "{세션}|{종목}|{방향}" 이다.
            key = f"{str(row['order_id']).split('|')[0]}|{row['entity_id']}"
            filled[key] = {
                "price": float(row["price"]),
                "quantity": float(row["quantity"]),
                "fee": float(row["fee"]),
                "tax": float(row["tax"]),
            }

    if frame.empty:
        return []
    names = _names(store, as_of=as_of, entities=sorted(set(frame["entity_id"])))
    rows: list[dict[str, Any]] = []
    ordered = frame.sort_values(["valid_from", "observed_at"], ascending=False)
    for row in ordered.head(ORDER_ROWS).to_dict(orient="records"):
        session = str(row["session_id"])
        match = filled.get(f"{session}|{row['entity_id']}")
        rows.append(
            {
                "time": pd.Timestamp(row["valid_from"]).isoformat(),
                "entity_id": str(row["entity_id"]),
                "name": names.get(str(row["entity_id"]), str(row["entity_id"])),
                "side": str(row["side"]),
                "quantity": float(row["quantity"]),
                "limit_price": (
                    float(row["limit_price"]) if pd.notna(row["limit_price"]) else None
                ),
                "status": "filled" if match else str(row["status"]),
                "fill_price": match["price"] if match else None,
                "fill_quantity": match["quantity"] if match else None,
                "cost": (match["fee"] + match["tax"]) if match else None,
                "target_weight": float(row["target_weight"]),
                "session_id": session,
                # 체결 지연은 실거래에서만 잰다. 0 으로 채우면 "빠르다" 로 읽힌다.
                "latency_ms": None,
            }
        )
    return rows


# -- 시계열 --------------------------------------------------------------------


def equity_curve(store: Store, context: Context, *, lookback: int) -> dict[str, Any]:
    """NAV·누적지수·낙폭 시계열. 회계가 남긴 것을 그대로 읽는다."""
    frame = store.get(
        NAV_DAILY, as_of=context.as_of, entity=ledger_module.ACCOUNT, lookback=lookback
    )
    if frame.empty:
        return {"sessions": [], "nav": [], "index": [], "drawdown": [], "benchmark": []}
    ordered = frame.sort_values(["valid_from", "observed_at"]).tail(EQUITY_SESSIONS)
    return {
        "sessions": [pd.Timestamp(value).date().isoformat() for value in ordered["valid_from"]],
        "nav": [float(value) for value in ordered["nav"]],
        "index": [float(value) for value in ordered["index_value"]],
        "drawdown": [float(value) for value in ordered["drawdown"]],
        "benchmark": [
            float(value) if pd.notna(value) else None for value in ordered["benchmark_index"]
        ],
    }


def candles(
    store: Store, *, as_of: datetime, entity_id: str, market: str, lookback: int
) -> dict[str, Any]:
    """한 종목의 일봉과 이동평균, 그리고 그 위에 얹을 우리 흔적.

    **1분봉이 아니라 일봉이다.** 창고에 있는 것이 일봉이고, 없는 봉을 그리면
    화면이 창고보다 많이 아는 것처럼 보인다.
    """
    frame = store.get(
        PRICES,
        as_of=as_of,
        entity=entity_id,
        lookback=lookback,
        market=market,
        columns=["open", "high", "low", "close", "volume", "valid_from"],
    )
    if frame.empty:
        return {"entity_id": entity_id, "sessions": [], "ohlc": [], "volume": [], "ma": {}}

    ordered = frame.sort_values("valid_from")
    # 휴장일이 종가 0 으로 적재된 세션이 있다 (2026-06-03·2026-07-17). 그대로
    # 그리면 y축이 0까지 늘어나 나머지 봉이 전부 납작해진다. **0원에 거래된
    # 날은 없다** — 없는 봉을 지우는 것이지 불편한 값을 감추는 것이 아니다.
    # 수집 쪽은 이제 그 응답을 거부한다 (krx_source.ohlcv_on).
    ordered = ordered[ordered["close"].astype(float) > 0]
    if ordered.empty:
        return {"entity_id": entity_id, "sessions": [], "ohlc": [], "volume": [], "ma": {}}
    closes = ordered["close"].astype(float)
    sessions = [pd.Timestamp(value).date().isoformat() for value in ordered["valid_from"]]
    trades = store.get(TRADES, as_of=as_of, entity=entity_id, lookback=lookback)
    marks = [
        {
            "session": pd.Timestamp(row["valid_from"]).date().isoformat(),
            "side": str(row["side"]),
            "price": float(row["price"]),
            "quantity": float(row["quantity"]),
        }
        for row in trades.to_dict(orient="records")
    ] if not trades.empty else []

    return {
        "entity_id": entity_id,
        "sessions": sessions,
        "ohlc": [
            [float(row["open"]), float(row["close"]), float(row["low"]), float(row["high"])]
            for row in ordered.to_dict(orient="records")
        ],
        "volume": [float(value) for value in ordered["volume"].fillna(0.0)],
        "ma": {
            f"ma{window}": [
                None if pd.isna(value) else float(value)
                for value in closes.rolling(window).mean()
            ]
            for window in (5, 20, 60)
        },
        "trades": marks,
    }


# -- 내부 ----------------------------------------------------------------------


def _latest_scores(store: Store, *, as_of: datetime) -> dict[str, float]:
    """종목별 최신 합성 점수. 신호가 없으면 빈 dict — 0 으로 채우지 않는다."""
    signals = store.get(
        SIGNALS,
        as_of=as_of,
        lookback=5,
        columns=["entity_id", "analyst", "score", "confidence", "observed_at"],
    )
    if signals.empty:
        return {}
    weights = analyst_weights(store, as_of=as_of, market="KR")
    if not weights:
        return {}
    from quant_rl_trading.selector.combine import combined_scores

    combined = combined_scores(signals, weights)
    return {str(entity): float(value) for entity, value in combined.items()}


def _names(store: Store, *, as_of: datetime, entities: list[str]) -> dict[str, str]:
    if not entities:
        return {}
    # 정렬에 쓰는 열도 함께 요청한다. columns 로 좁히면 안 부른 열은 오지 않고,
    # 그때 정렬 키가 사라져 조용히 KeyError 로 죽는다.
    frame = store.get(
        UNIVERSE,
        as_of=as_of,
        entity=entities,
        lookback=10,
        columns=["name", "valid_from", "observed_at"],
    )
    if frame.empty:
        return {}
    latest = frame.sort_values(["valid_from", "observed_at"]).groupby("entity_id").tail(1)
    return {str(row["entity_id"]): str(row["name"]) for row in latest.to_dict(orient="records")}


def _quotes(
    store: Store, *, as_of: datetime, entities: list[str], market: str
) -> dict[str, dict[str, float]]:
    """종가·등락률·거래대금. 등락률은 직전 세션 대비다."""
    if not entities:
        return {}
    frame = store.get(
        PRICES,
        as_of=as_of,
        entity=entities,
        lookback=10,
        market=market,
        columns=["close", "value", "valid_from"],
    )
    if frame.empty:
        return {}
    out: dict[str, dict[str, float]] = {}
    for entity, group in frame.sort_values("valid_from").groupby("entity_id"):
        closes = group["close"].astype(float)
        if closes.empty:
            continue
        last = float(closes.iloc[-1])
        previous = float(closes.iloc[-2]) if len(closes) >= 2 else None
        out[str(entity)] = {
            "close": last,
            "change": (last / previous - 1.0) if previous else None,
            "value": float(group["value"].astype(float).iloc[-1]),
        }
    return out


def system(store: Store, context: Context) -> dict[str, Any]:
    """상단 상태 바. **모드와 창고를 화면이 항상 말한다.**

    shadow 창고를 보면서 실전이라고 착각하는 것이 이 화면에서 가능한 가장
    비싼 오해다. 그래서 창고 경로에서 모드를 유도해 배지로 띄운다 — 사람이
    설정을 기억하게 두지 않는다.
    """
    root = str(store.root)
    if root.endswith("_shadow"):
        mode, mode_note = "SHADOW", "모의 운용 — 돈이 오가지 않는다"
    elif "_backtest" in root:
        mode, mode_note = "BACKTEST", "백테스트 샌드박스"
    else:
        mode, mode_note = "LIVE", "실전 창고"

    as_of = context.as_of
    # 여기서 쓰는 것은 **가장 늦은 관측 시각 하나**다. 컬럼을 안 좁히면
    # 단계별 실측 로그의 detail 문자열까지 통째로 퍼온다 — 요청 하나에서
    # 1.3초를 쓰고 있었고, 그게 이 API 시간의 절반이었다.
    latency = store.get(
        "ingest_latency", as_of=as_of, lookback=2, columns=["observed_at"]
    )
    last_ingest = None
    if not latency.empty:
        last_ingest = pd.Timestamp(latency["observed_at"].max()).isoformat()

    signals = store.get(SIGNALS, as_of=as_of, lookback=3, columns=["observed_at"])
    return {
        "mode": mode,
        "mode_note": mode_note,
        "store_root": root,
        "broker": "LS · 미연결",  # 브로커 어댑터는 실전 투입 때 붙는다
        "engine": f"룰 베이스라인 ({AllocatorParams.from_store(store, as_of=as_of).baseline})",
        "last_ingest": last_ingest,
        # **신호가 언제 것인지**가 이 화면에서 가장 자주 묻는 질문이다.
        "last_signal": (
            pd.Timestamp(signals["observed_at"].max()).isoformat()
            if not signals.empty
            else None
        ),
        "signals_rows": len(signals),
    }


def payload(
    store: Store,
    clock: Any,
    *,
    as_of: datetime,
    market: str,
    lookback: int,
    entity_id: str | None = None,
) -> dict[str, Any]:
    """트레이딩 탭 한 판. 장부는 한 번만 접는다.

    **회계가 평가를 거부하면 화면은 그 이유를 띄운다.** 환율이 없으면
    ``ledger.fx_rate`` 가 예외를 던지는데(그게 옳다 — 1.0 으로 때우면 NAV 가
    무너진다), 그때 화면이 통째로 500 이 되면 사람은 "대시보드가 죽었다" 로
    읽는다. 실제로는 **수집이 빠진 것**이고, 그 구분이 복구 시간을 가른다.
    """
    try:
        context = build_context(store, clock, as_of=as_of, market=market)
    except LookupError as error:
        return {
            "market": market,
            "system": {
                "mode": "LIVE" if not str(store.root).endswith("_shadow") else "SHADOW",
                "mode_note": "평가 불가",
                "store_root": str(store.root),
                "broker": "—",
                "engine": "—",
                "last_ingest": None,
                "last_signal": None,
                "signals_rows": 0,
            },
            "unavailable": str(error),
            "kpis": None,
            "risk": None,
            "alerts": [{"level": "critical", "text": f"회계가 평가를 거부했다: {error}"}],
            "positions": [],
            "watchlist": [],
            "decision": None,
            "orders": [],
            "equity": {"sessions": [], "nav": [], "index": [], "drawdown": [], "benchmark": []},
        }
    kpi = kpis(store, context)
    risk_state = risk(store, context)
    return {
        "market": market,
        "system": system(store, context),
        "kpis": kpi,
        "risk": risk_state,
        "alerts": alerts(kpi, risk_state),
        "positions": positions(store, context),
        "watchlist": watchlist(store, context),
        "decision": decision(store, context, entity_id=entity_id),
        "orders": orders(store, context),
        "equity": equity_curve(store, context, lookback=lookback),
    }
