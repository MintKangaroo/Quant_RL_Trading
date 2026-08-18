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
from quant_rl_trading.accounting.book import Book, Trade
from quant_rl_trading.accounting.book import Side as BookSide
from quant_rl_trading.accounting.rates import Rates
from quant_rl_trading.allocator.baseline import AllocatorParams
from quant_rl_trading.executor import guards
from quant_rl_trading.executor import pipeline as executor_pipeline
from quant_rl_trading.selector.combine import contributions
from quant_rl_trading.selector.weights import analyst_weights
from quant_rl_trading.store import Store
from quant_rl_trading.store.prices import read_prices

NAV_DAILY = "nav_daily"
ORDERS = "orders"
TRADES = "trades"
SIGNALS = "signals"
UNIVERSE = "universe"
REALIZED_WEIGHTS = "realized_weights"
INDICES = "indices"

#: 캘린더 옆 "지수 대비" 패널의 참고용 비교. ``config.benchmark`` 의 혼합
#: 벤치마크와는 다른 것이다 — 저건 보상 함수가 쓰는 한 줄짜리 지수고, 이건
#: 화면이 상시 보여주는 지수별 참고 비교다. **둘 다 가격지수다**(총수익지수는
#: 지금 키로 못 받는다, accounting.md §7.1). 실측(2026-08-14) 결과 창고에는
#: 코스피·코스닥·나스닥이 그 이름으로 없다. 있는 것은 KRX 100/300 계열과
#: US:IDX:SP500 뿐이다(regime.py 의 MARKET_PROXIES 도 같은 이유로 KRX 지수를
#: 대용치로 쓴다). 여기서는 대용치로 바꿔치기하지 않는다 — "코스피" 라고
#: 이름 붙여 놓고 실은 KRX 300 을 보여주면 그게 더 큰 거짓말이다.
BENCHMARK_CANDIDATES = (
    {"key": "kospi", "label": "코스피", "entity_id": "KR:IDX:KOSPI"},
    {"key": "kosdaq", "label": "코스닥", "entity_id": "KR:IDX:KOSDAQ"},
    {"key": "nasdaq", "label": "나스닥", "entity_id": "US:IDX:NASDAQ"},
    {"key": "sp500", "label": "S&P500", "entity_id": "US:IDX:SP500"},
)

#: 주문·체결 표에 싣는 최대 행수. 화면은 한 눈에 보는 것이지 원장이 아니다.
#:
#: **한 세션 주문 수보다 넉넉해야 한다.** 40 이면 마지막 세션(주문 40~46건)만
#: 들어차는데, 그 세션은 **결정은 D 체결은 D+1** 이라 아직 체결이 없다. 그래서
#: 패널 이름이 ORDERS & EXECUTIONS 인데 체결가·비용·실현손익 열이 **구조적으로
#: 영원히 비어 있었다**(2026-08-18 발견). 두 세션 이상이 들어와야 "낸 주문이
#: 체결됐는지" 를 눈으로 맞출 수 있다 — 이 표가 존재하는 이유가 그것이다.
ORDER_ROWS = 120

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
    #: 장중 시세 캐시. **없어도 화면은 돌아야 한다** — 회계는 종가로만 계산하고
    #: 이건 참고 열 전용이라, 주입 안 하면 그 열이 비는 것으로 끝난다.
    live_quotes: Any = None


def build_context(
    store: Store, clock: Any, *, as_of: datetime, market: str, live_quotes: Any = None
) -> Context:
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
        as_of=as_of, market=market, book=book, snapshot=snapshot, prices=prices,
        live_quotes=live_quotes,
    )


# -- KPI -----------------------------------------------------------------------


def _live_valuation(context: Context, valuation: Any) -> dict[str, Any]:
    """장중 시세로 다시 계산한 총자산. **회계가 아니라 화면용이다.**

    보유 수량은 장부에서, 가격은 장중 시세에서 가져온다. 장중 값이 없는
    종목은 **종가로 메운다** — 빼 버리면 그 종목이 사라진 것처럼 총자산이
    줄어 폭락으로 보인다(nav.value 가 예외를 던지는 것과 같은 이유).

    몇 종목이 장중 값을 받았는지(``covered``)를 같이 돌려준다. 절반만 받은
    수치를 "지금 총자산" 이라고 말하면 안 되고, 화면이 그 사실을 적어야 한다.

    현금은 그대로다. 미장분은 종가 환율을 쓴다 — 장중 환율은 받지 않는다.
    """
    cache = getattr(context, "live_quotes", None)
    positions = {
        entity: position
        for entity, position in context.book.positions.items()
        if position.quantity > 0
    }
    if cache is None or not positions:
        return {"nav": None, "change": None, "equity": None, "covered": 0}

    quotes = cache.get(list(positions))
    if not quotes:
        return {"nav": None, "change": None, "equity": None, "covered": 0}

    equity = 0.0
    covered = 0
    for entity, position in positions.items():
        quote = quotes.get(entity)
        if quote is not None and quote.price > 0:
            price = quote.price
            covered += 1
        else:
            price = context.prices.get(entity)
            if price is None:
                continue
        value = price * position.quantity
        # 미장분은 종가 환율로 환산한다. 장중 환율은 수집하지 않는다.
        equity += value * (valuation.fx_rate if entity.startswith("US:") else 1.0)

    cash = valuation.cash_krw + valuation.cash_usd * valuation.fx_rate
    live_nav = cash + equity + valuation.accrued_dividend - valuation.payable
    closing = valuation.nav
    return {
        "nav": live_nav,
        "change": (live_nav / closing - 1.0) if closing > 0 else None,
        "equity": equity,
        "covered": covered,
    }


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

    # 수익 4종 — LS_KR 대시보드에서 가장 먼저 읽던 자리다.
    #
    # **원금은 입출금의 합이지 첫날 NAV 가 아니다.** 첫날 NAV 로 재면 이후
    # 입금이 통째로 수익으로 잡힌다 (accounting.md §6, TWR 과 같은 이유).
    flows = store.get(
        "capital_flows", as_of=as_of, entity=ledger_module.ACCOUNT, lookback=None
    )
    principal = float(flows["amount"].astype(float).sum()) if not flows.empty else 0.0
    total_pnl = nav - principal if principal > 0 else None

    # 오늘 수익금은 **직전 스냅샷 대비**다. 그날 입금이 있었으면 그만큼 뺀다 —
    # 안 빼면 입금일이 대박 난 날로 보인다.
    today_pnl = None
    if previous is not None:
        today_pnl = nav - float(previous["nav"]) - float(context.snapshot.inflow or 0.0)

    curve = store.get(
        NAV_DAILY, as_of=as_of, entity=ledger_module.ACCOUNT, lookback=400
    )
    win_rate = None
    mdd = None
    if not curve.empty:
        ordered = curve.sort_values(["valid_from", "observed_at"])
        returns = ordered["twr_return"].astype(float)
        # **승률은 일간이다.** LS_KR 은 종목별 매도 기준이었는데, 우리는 아직
        # 매도 이력이 거의 없다. 없는 것을 재면 표본 두세 건짜리 승률이 나오고
        # 그 숫자는 성적이 아니라 노이즈다. 무엇을 세는지 화면에 적는다.
        traded = returns[returns != 0.0]
        win_rate = float((traded > 0).mean()) if len(traded) else None
        mdd = float(ordered["drawdown"].astype(float).min())

    live = _live_valuation(context, valuation)

    return {
        "nav": nav,
        "nav_after_tax": valuation.nav_after_tax,
        # **장중 재평가 — 참고값이다.** 위의 nav·daily_return·mdd 는 전부
        # 종가 기준이고(accounting.md §2 — 15:40 하루 한 번), 벤치마크도 같은
        # 시각으로 잰다. 그 둘을 섞으면 차이가 통째로 가짜 초과수익이 된다.
        # 그래서 **따로 담고 화면도 따로 그린다.** nav_daily 에 쓰지 않는다.
        "live_nav": live["nav"],
        "live_change": live["change"],
        "live_equity": live["equity"],
        "live_covered": live["covered"],
        "principal": principal or None,
        "today_pnl": today_pnl,
        "total_pnl": total_pnl,
        "win_rate": win_rate,
        "win_samples": len(traded) if not curve.empty and win_rate is not None else 0,
        "mdd": mdd,
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
                # **참고 열이다. 회계에 안 들어간다.** 위의 price·value·pnl 은
                # 전부 종가 기준이고(accounting.md §2 — NAV 는 15:40 하루 한 번),
                # 여기에 장중 값을 섞으면 벤치마크와 기준 시각이 어긋나 그 차이가
                # 통째로 가짜 초과수익이 된다. 그래서 따로 담고 화면도 따로 그린다.
                "live_price": None,
                "live_change": None,
            }
        )
    rows.sort(key=lambda row: row["value"] or 0.0, reverse=True)
    _attach_live(rows, context)
    return rows


def _attach_live(rows: list[dict[str, Any]], context: Context) -> None:
    """장중 값을 **참고 열에만** 채운다. 없으면 그대로 둔다.

    실패·장외를 종가로 때우지 않는다. 때우면 화면이 실시간인 척하게 되고,
    그건 조용히 틀리는 종류의 거짓이다 — 없으면 화면이 "장외" 로 그린다.
    """
    cache = getattr(context, "live_quotes", None)
    if cache is None or not rows:
        return
    quotes = cache.get([row["entity_id"] for row in rows])
    for row in rows:
        quote = quotes.get(row["entity_id"])
        if quote is None or quote.price <= 0:
            continue
        row["live_price"] = quote.price
        row["live_change"] = quote.change_rate


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


def _realized_by_trade(store: Store, as_of: datetime) -> dict[str, dict[str, Any]]:
    """체결별 실현손익. **매도에만 값이 있다.**

    화면이 "얼마에 팔았나" 만 보여주면 그게 이익인지 손실인지 알 수 없다.
    평균단가는 그 종목의 **모든 과거 매수**에 달려 있어서, 최근 10일만 봐서는
    못 구한다 — 그래서 전 기간을 접는다.

    **계산을 다시 구현하지 않는다.** ``Book.with_trade`` 가 이미
    ``(체결가 빼기 평단) 곱하기 수량, 비용 차감`` 을 하고 있고(이동평균법, 수수료 포함),
    회계와 화면이 다른 식을 쓰면 어느 쪽이 맞는지 판정할 방법이 없다
    (accounting.md §8 과 같은 이유). 여기서는 그 장부를 재생하며 매도 직전·직후
    누적 실현손익의 **차이**를 꺼낼 뿐이다.

    수익률의 분모는 **취득원가**(평단 × 수량)다. 매도대금으로 나누면 손실이
    난 거래에서 분모가 작아져 손실률이 실제보다 작아 보인다.
    """
    frame = store.get(TRADES, as_of=as_of)
    if frame.empty:
        return {}

    book = Book()
    out: dict[str, dict[str, Any]] = {}
    for row in ledger_module._ordered(frame):
        entity = str(row["entity_id"])
        currency = str(row["currency"])
        side = BookSide(str(row["side"]))
        quantity = float(row["quantity"])
        held = book.positions.get(entity)
        basis = (held.avg_cost * quantity) if held else 0.0
        before = book.realized_pnl.get(currency, 0.0)
        try:
            book = book.with_trade(
                Trade(
                    entity_id=entity, side=side, quantity=quantity,
                    price=float(row["price"]), currency=currency,
                    fee=float(row["fee"]), tax=float(row["tax"]),
                )
            )
        except ValueError:
            # 보유보다 많이 판 기록이다. 장부가 아니라 데이터의 문제이므로
            # 화면을 죽이지 않고 그 건만 건너뛴다.
            continue
        if side is not BookSide.SELL:
            continue
        realized = book.realized_pnl.get(currency, 0.0) - before
        key = f"{str(row['order_id']).split('|')[0]}|{entity}"
        out[key] = {
            "realized_pnl": realized,
            "realized_rate": (realized / basis) if basis else None,
            "currency": currency,
        }
    return out


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
    realized = _realized_by_trade(store, as_of)
    names = _names(store, as_of=as_of, entities=sorted(set(frame["entity_id"])))
    rows: list[dict[str, Any]] = []
    ordered = frame.sort_values(["valid_from", "observed_at"], ascending=False)
    for row in ordered.head(ORDER_ROWS).to_dict(orient="records"):
        session = str(row["session_id"])
        key = f"{session}|{row['entity_id']}"
        match = filled.get(key)
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
                # 체결이 있어도 **주문 수량에 못 미치면 부분 체결이다.**
                # 전부 filled 로 뭉개면 유동성 부족이 화면에서 사라진다.
                "status": (
                    (
                        "partial"
                        if float(match["quantity"]) < float(row["quantity"])
                        else "filled"
                    )
                    if match
                    else str(row["status"])
                ),
                "fill_price": match["price"] if match else None,
                "fill_quantity": match["quantity"] if match else None,
                "cost": (match["fee"] + match["tax"]) if match else None,
                # **매도에만 붙는다.** 매수에 0 을 넣으면 "본전" 으로 읽힌다.
                "realized_pnl": (realized.get(key) or {}).get("realized_pnl"),
                "realized_rate": (realized.get(key) or {}).get("realized_rate"),
                "currency": (realized.get(key) or {}).get("currency"),
                "target_weight": float(row["target_weight"]),
                "session_id": session,
                # 체결 지연은 실거래에서만 잰다. 0 으로 채우면 "빠르다" 로 읽힌다.
                "latency_ms": None,
            }
        )
    return rows


# -- 시계열 --------------------------------------------------------------------


def equity_curve(store: Store, context: Context, *, lookback: int) -> dict[str, Any]:
    """NAV·누적지수·낙폭 시계열. 회계가 남긴 것을 그대로 읽는다.

    벤치마크 낙폭만 여기서 **계산한다.** 장부에는 벤치마크 지수만 있고 그
    낙폭은 없기 때문이다. 우리 낙폭과 같은 규칙(전 기간 고점)으로 재려고
    창 이전의 벤치마크 고점을 따로 물어본다 — 창 안에서만 재면 창 첫날이
    고점이 되어 벤치마크가 실제보다 덜 빠진 것처럼 보인다.
    """
    frame = store.get(
        NAV_DAILY, as_of=context.as_of, entity=ledger_module.ACCOUNT, lookback=lookback
    )
    if frame.empty:
        empty: dict[str, Any] = {
            "sessions": [],
            "nav": [],
            "index": [],
            "drawdown": [],
            "benchmark": [],
            "benchmark_drawdown": [],
            "benchmark_label": _benchmark_label(store, as_of=context.as_of),
            "benchmark_note": None,
        }
        return empty
    ordered = frame.sort_values(["valid_from", "observed_at"]).tail(EQUITY_SESSIONS)
    benchmark = [
        float(value) if pd.notna(value) else None for value in ordered["benchmark_index"]
    ]
    return {
        # 왜 벤치마크가 비어 있는지. 화면이 이걸 못 말하면 "데이터가 없었다" 와
        # "벤치마크가 0% 였다" 가 같은 그림으로 보인다. 창 안의 **마지막**
        # 사유를 싣는다 — 여러 날이 같은 이유로 비는 것이 보통이다.
        "benchmark_note": _last_benchmark_note(ordered),
        "benchmark_label": _benchmark_label(store, as_of=context.as_of),
        "sessions": [pd.Timestamp(value).date().isoformat() for value in ordered["valid_from"]],
        "nav": [float(value) for value in ordered["nav"]],
        "index": [float(value) for value in ordered["index_value"]],
        "drawdown": [float(value) for value in ordered["drawdown"]],
        "benchmark": benchmark,
        "benchmark_drawdown": _benchmark_drawdown(
            store, context, benchmark, since=pd.Timestamp(ordered["valid_from"].iloc[0])
        ),
    }


def _last_benchmark_note(ordered: pd.DataFrame) -> str | None:
    """창 안에서 벤치마크가 비어 있던 마지막 사유. 다 차 있으면 None."""
    if "benchmark_note" not in ordered.columns:
        return None
    notes = ordered["benchmark_note"].dropna()
    notes = notes[notes.astype(str) != ""]
    return str(notes.iloc[-1]) if not notes.empty else None


def _benchmark_label(store: Store, *, as_of: datetime) -> dict[str, Any]:
    """벤치마크 배지. **가격지수라는 사실을 화면이 말하게 한다.**

    ``config.benchmark`` 에서 그대로 읽는다 — 여기에 지수 이름을 적어 두면
    설정을 바꿨을 때 화면만 옛 지수를 말한다 (불변식 10).
    """
    section = store.config("benchmark", as_of=as_of)
    total_return = bool(section.get("total_return", False))
    return {
        "kr_index": str(section["kr_index"]),
        "us_index": str(section["us_index"]),
        "kr_weight": float(section["kr_weight"]),
        "us_weight": float(section["us_weight"]),
        # 우리는 배당을 받고 가격지수는 못 받는다. 배당수익률만큼(국내 대형주
        # 연 2~3%p) 우리가 이긴 것처럼 보인다 (accounting.md §7.1).
        "price_return_only": not total_return,
    }


def _benchmark_drawdown(
    store: Store,
    context: Context,
    benchmark: list[float | None],
    *,
    since: pd.Timestamp,
) -> list[float | None]:
    """창 안의 벤치마크 낙폭. 고점은 **창 이전까지 포함해서** 잡는다.

    벤치마크가 아직 한 번도 안 들어온 구간에서는 ``None`` 을 그대로 흘린다.
    0 으로 채우면 "그날 낙폭이 없었다" 가 되어, 벤치마크가 없다는 사실이
    좋은 성적으로 둔갑한다.
    """
    if not any(value is not None for value in benchmark):
        return [None] * len(benchmark)

    # NAV_DAILY 는 계좌 하나에 하루 한 행이라 전 기간을 읽어도 수백 행이다.
    history = store.get(NAV_DAILY, as_of=context.as_of, entity=ledger_module.ACCOUNT)
    peak = float("-inf")
    if not history.empty:
        prior = history[pd.to_datetime(history["valid_from"]) < since]
        if not prior.empty:
            earlier = prior["benchmark_index"].dropna()
            if not earlier.empty:
                peak = float(earlier.max())

    out: list[float | None] = []
    for value in benchmark:
        if value is None:
            out.append(None)
            continue
        peak = max(peak, value)
        out.append(value / peak - 1.0 if peak > 0 else 0.0)
    return out


def returns_calendar(store: Store, *, as_of: datetime, lookback: int) -> dict[str, Any]:
    """일별 수익률. 화면이 달력으로 깐다.

    ``Context`` 가 아니라 ``as_of`` 만 받는다. 달력은 ``nav_daily`` 하나로
    그려지는데 Context 를 요구하면 부르는 쪽이 장부와 스냅샷을 먼저 접어야
    하고, 그 값은 이 화면에 한 글자도 안 나온다.

    **여기서 달력을 만들지 않는다** — 주 시작 요일·빈칸 배치는 표현이고,
    표현을 서버에 넣으면 화면을 바꿀 때마다 API 가 따라 바뀐다.

    수익률은 TWR 이다. NAV 증감으로 재면 입금일이 수익으로 잡힌다.
    """
    frame = store.get(
        NAV_DAILY, as_of=as_of, entity=ledger_module.ACCOUNT, lookback=lookback
    )
    if frame.empty:
        return {"days": [], "months": []}
    ordered = frame.sort_values(["valid_from", "observed_at"])
    days = [
        {
            "session": pd.Timestamp(row["valid_from"]).date().isoformat(),
            "return": float(row["twr_return"]),
            "nav": float(row["nav"]),
        }
        for row in ordered.to_dict(orient="records")
    ]
    # 월별 누적은 일별 수익률의 곱이다. 합이 아니다 — 합으로 재면 변동이 큰
    # 달에서 실제와 벌어진다.
    months: dict[str, float] = {}
    for day in days:
        key = day["session"][:7]
        months[key] = (1.0 + months.get(key, 0.0)) * (1.0 + day["return"]) - 1.0
    return {
        "days": days,
        "months": [{"month": k, "return": v} for k, v in sorted(months.items())],
    }


def calendar_payload(store: Store, *, as_of: datetime, lookback: int) -> dict[str, Any]:
    """별도 창의 캘린더가 쓰는 전부. ``nav_daily`` 만 읽는다.

    ``최고/최악의 날`` 을 여기서 고르는 이유는, 화면이 고르면 표시된 달만
    보고 고르게 되기 때문이다. 창 전체에서 골라야 "이 창의 최악" 이 된다.
    """
    calendar = returns_calendar(store, as_of=as_of, lookback=lookback)
    days = calendar["days"]
    if not days:
        return {
            "calendar": calendar,
            "best": None,
            "worst": None,
            "sessions": 0,
            "cumulative": None,
        }

    best = max(days, key=lambda day: day["return"])
    worst = min(days, key=lambda day: day["return"])
    cumulative = 1.0
    for day in days:
        cumulative *= 1.0 + day["return"]
    return {
        "calendar": calendar,
        "best": best,
        "worst": worst,
        "sessions": len(days),
        # 창 전체의 누적. 일별 TWR 의 곱이다 — 합으로 재면 변동이 큰 구간에서 벌어진다.
        "cumulative": cumulative - 1.0,
    }


def benchmark_compare(store: Store, *, as_of: datetime, lookback: int) -> dict[str, Any]:
    """캘린더 옆 "지수 대비" 패널. 이번 달 누적과 **누적 초과 곡선**.

    ## 왜 일별 막대가 아니라 누적 곡선인가

    이 전략은 저베타다(실측 베타 0.131 · 상승일 포착률 14%). 상승장에서 일별
    초과가 음수인 날이 줄줄이 나오는 것이 **설계대로 굴러가는 모습**인데,
    막대로 그리면 화면이 매일 "졌다" 고 스무 번 외친다 — 실측으로 2026-07 에
    일별 초과의 부호가 22일 중 9~13번 뒤집혔다. 신호 대 잡음이 나쁘다.

    게다가 빨강이 손실로 읽힌다. 우리가 +1% 벌어도 지수가 +3% 면 빨강인데,
    같은 패널의 캘린더 쪽 빨강은 **진짜 손실**이라 한 패널에서 같은 색이 두
    가지를 뜻하게 된다.

    누적은 그 달의 결론이고, 곡선이면 **언제부터 벌어졌나**도 보인다.

    **가격지수다, 총수익지수가 아니다.** KRX Open API·LS·FRED 세 곳 다 배당을
    반영한 총수익지수를 안 준다(실측). 배당수익률만큼(국내 대형주 연 2~3%p)
    우리가 유리하게 나온다 — 화면이 이 사실을 ``price_return_only`` 로 실어
    보내고, 그 문구를 화면이 지운 채 보여주지 않는다.

    달은 **캘린더 패널과 같은 달**을 고른다(데이터에 있는 마지막 달). 실제
    달력의 이번 달을 쓰면 데모 창고처럼 최근 데이터가 며칠 전에서 끝나는
    창고에서 패널이 통째로 빈다.
    """
    nav = store.get(NAV_DAILY, as_of=as_of, entity=ledger_module.ACCOUNT, lookback=lookback)
    if nav.empty:
        return {
            "month": None,
            "price_return_only": True,
            "benchmarks": [
                {"key": c["key"], "label": c["label"], "available": False,
                 "reason": "nav_daily 가 비어 있다"}
                for c in BENCHMARK_CANDIDATES
            ],
        }
    ordered = nav.sort_values(["valid_from", "observed_at"])
    our_by_day = {
        pd.Timestamp(row["valid_from"]).date().isoformat(): float(row["twr_return"])
        for row in ordered.to_dict(orient="records")
    }
    month = sorted({day[:7] for day in our_by_day})[-1]
    month_days = sorted(day for day in our_by_day if day.startswith(month))
    cumulative_ours = 1.0
    for day in month_days:
        cumulative_ours *= 1.0 + our_by_day[day]
    cumulative_ours -= 1.0

    benchmarks: list[dict[str, Any]] = []
    for candidate in BENCHMARK_CANDIDATES:
        frame = store.get(INDICES, as_of=as_of, entity=candidate["entity_id"], lookback=lookback)
        if frame.empty:
            benchmarks.append(
                {
                    "key": candidate["key"],
                    "label": candidate["label"],
                    "available": False,
                    "reason": f"{candidate['entity_id']} 이름의 지수가 창고에 없다",
                }
            )
            continue
        idx_ordered = frame.sort_values("valid_from")
        idx_dates = [pd.Timestamp(v).date().isoformat() for v in idx_ordered["valid_from"]]
        closes = idx_ordered["close"].astype(float).tolist()
        idx_returns: dict[str, float] = {}
        previous_close = None
        for date, close in zip(idx_dates, closes, strict=True):
            if previous_close is not None and previous_close > 0:
                idx_returns[date] = close / previous_close - 1.0
            previous_close = close

        # 지수 쪽 누적은 **지수 자기 거래일**로 잰다. 우리 거래일에 맞춰 자르면
        # 미국장은 코리아 휴장일만큼 손실을 보고 시작한다 — 두 시장의 달력이
        # 다르다는 사실 자체를 지워버리는 것이다.
        index_month_days = sorted(d for d in idx_returns if d.startswith(month))
        cumulative_index = None
        if index_month_days:
            cumulative_index = 1.0
            for day in index_month_days:
                cumulative_index *= 1.0 + idx_returns[day]
            cumulative_index -= 1.0

        # 축은 **두 달력의 합집합**이다. 우리 거래일로만 자르면 지수가 그 달에
        # 실제로 움직인 날 일부가 축에서 빠지고, 그러면 곡선의 끝점이
        # ``cumulative_excess`` 와 안 맞는다 — 실측으로 국장에서 3.6~4.4%p
        # 어긋났다. 화면의 숫자와 곡선이 다른 값을 말하는 것은 그 자체로 결함이다.
        axis = sorted(set(month_days) | set(index_month_days))
        daily = []
        running_ours = 1.0
        running_index = 1.0
        for day in axis:
            ours = our_by_day.get(day)
            index = idx_returns.get(day)
            if ours is not None:
                running_ours *= 1.0 + ours
            if index is not None:
                running_index *= 1.0 + index
            daily.append(
                {
                    "session": day,
                    "ours": ours,
                    "index": index,
                    # **누적 초과**다. 일별 초과가 아니다 — 저베타 전략에서
                    # 하루하루의 승패는 베타 차이가 만드는 잡음이고, 그 달의
                    # 결론은 누적이다(실측: 2026-07 에 일별 부호가 22일 중
                    # 9~13번 뒤집혔다. 막대로 그리면 화면이 매일 "졌다" 고
                    # 외치는데 그게 설계대로 굴러가는 중이라는 뜻이었다).
                    "cumulative_excess": running_ours - running_index,
                    # 그날 양쪽이 다 관측됐나. 한쪽이 없는 날은 그쪽 누적이
                    # 안 움직인다 — 즉 **"그날 그쪽 수익률이 0"** 이라고 가정한
                    # 셈이다. 휴장이면 맞고 미수집이면 틀리는데 여기서는 둘을
                    # 못 가른다. 그래서 지우지 않고 화면이 세어서 적는다.
                    "paired": ours is not None and index is not None,
                }
            )
        benchmarks.append(
            {
                "key": candidate["key"],
                "label": candidate["label"],
                "available": True,
                "cumulative_ours": cumulative_ours,
                "cumulative_index": cumulative_index,
                "cumulative_excess": (
                    cumulative_ours - cumulative_index if cumulative_index is not None else None
                ),
                "daily": daily,
            }
        )
    return {"month": month, "price_return_only": True, "benchmarks": benchmarks}


def candles(
    store: Store, *, as_of: datetime, entity_id: str, market: str, lookback: int
) -> dict[str, Any]:
    """한 종목의 일봉과 이동평균, 그리고 그 위에 얹을 우리 흔적.

    **1분봉이 아니라 일봉이다.** 창고에 있는 것이 일봉이고, 없는 봉을 그리면
    화면이 창고보다 많이 아는 것처럼 보인다.
    """
    frame = read_prices(
        store,
        as_of=as_of,
        entity=entity_id,
        lookback=lookback,
        market=market,
        columns=["open", "high", "low", "close", "volume", "valid_from"],
    )
    if frame.empty:
        return {"entity_id": entity_id, "sessions": [], "ohlc": [], "volume": [], "ma": {}}

    # 휴장일 종가 0 은 ``read_prices`` 가 이미 뺐다 (2026-06-03·2026-07-17).
    # 그대로 그리면 y축이 0까지 늘어나 나머지 봉이 전부 납작해진다.
    ordered = frame.sort_values("valid_from")
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
    frame = read_prices(
        store,
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
    elif "_demo" in root:
        # 화면 확인용 창고(tools/seed_demo.py). 보유와 주문은 우리가 심은
        # 것이고 시세만 진짜다. **이걸 LIVE 로 보여주면 안 된다** — 화면에서
        # 가능한 가장 비싼 오해가 모드를 잘못 읽는 것이다.
        mode, mode_note = "DEMO", "화면 확인용 — 보유·주문은 심은 것이다"
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
    live_quotes: Any = None,
) -> dict[str, Any]:
    """트레이딩 탭 한 판. 장부는 한 번만 접는다.

    **회계가 평가를 거부하면 화면은 그 이유를 띄운다.** 환율이 없으면
    ``ledger.fx_rate`` 가 예외를 던지는데(그게 옳다 — 1.0 으로 때우면 NAV 가
    무너진다), 그때 화면이 통째로 500 이 되면 사람은 "대시보드가 죽었다" 로
    읽는다. 실제로는 **수집이 빠진 것**이고, 그 구분이 복구 시간을 가른다.
    """
    try:
        context = build_context(
        store, clock, as_of=as_of, market=market, live_quotes=live_quotes
    )
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
            "calendar": {"days": [], "months": []},
            "benchmark_compare": {"month": None, "price_return_only": True, "benchmarks": []},
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
        "calendar": returns_calendar(store, as_of=context.as_of, lookback=lookback),
        "benchmark_compare": benchmark_compare(store, as_of=context.as_of, lookback=lookback),
    }
