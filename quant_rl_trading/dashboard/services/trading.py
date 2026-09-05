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
from quant_rl_trading.accounting import performance as performance_module
from quant_rl_trading.accounting import snapshot as snapshot_module
from quant_rl_trading.accounting.rates import Rates
from quant_rl_trading.allocator.baseline import AllocatorParams
from quant_rl_trading.executor import guards
from quant_rl_trading.executor import pipeline as executor_pipeline
from quant_rl_trading.selector.combine import contributions
from quant_rl_trading.collectors.market_hours import Market, is_regular_session
from quant_rl_trading.selector.weights import analyst_weights
from quant_rl_trading.store import Store
from quant_rl_trading.store import mode as mode_module
from quant_rl_trading.store import names as names_module
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


# -- 미장 슬리브 -------------------------------------------------------------------


def _us_sleeve(store: Store, context: Context, *, lookback: int) -> dict[str, Any] | None:
    """미장 탭의 숫자는 **달러 슬리브**다 — 장부 전체(원화 NAV)가 아니다.

    shadow 장부 하나에 국장 원화와 미장 달러가 같이 살아서, 미장 탭이 장부 전체 NAV 를
    보여주면 8/25 의 계단(국장 초기자본)과 9/2 의 계단(달러 입금)이 미장 성과처럼 읽힌다
    (사용자 지적 2026-09-04). 회계 스냅샷의 `equity_us`·`cash_usd` 와 nav_daily 의 같은
    열로 슬리브 NAV(USD)를 접는다. 원금은 `capital_flows` 의 USD 합. 지수·낙폭은 슬리브
    첫 스냅샷 = 100. **회계를 다시 하는 것이 아니다** — 회계가 남긴 두 열을 더할 뿐이다.
    """
    frame = store.get(NAV_DAILY, as_of=context.as_of, entity=ledger_module.ACCOUNT, lookback=lookback)
    if frame.empty or "equity_us" not in frame.columns:
        return None
    ordered = frame.sort_values(["valid_from", "observed_at"]).copy()
    ordered["nav_usd"] = ordered["equity_us"].astype(float) + ordered["cash_usd"].astype(float)
    ordered = ordered[ordered["nav_usd"] > 0]
    if ordered.empty:
        return None
    flows = store.get("capital_flows", as_of=context.as_of, entity=ledger_module.ACCOUNT, lookback=lookback * 3)
    principal = float(flows.loc[flows["currency"].astype(str) == "USD", "amount"].astype(float).sum()) if not flows.empty else 0.0
    valuation = context.snapshot.valuation
    nav_now = float(valuation.equity_us + valuation.cash_usd)
    # 날짜마다 마지막 스냅샷 하나 — 같은 날 05:20(미장 마감)·16:00(국장 마감) 둘이 있으면 뒤 것.
    ordered["day"] = pd.to_datetime(ordered["valid_from"]).dt.date
    daily = ordered.groupby("day", sort=True).tail(1)
    navs = daily["nav_usd"].astype(float).to_list()
    sessions = [d.isoformat() for d in daily["day"]]
    # "어제" 는 **직전 날짜**의 값이다. 같은 날 앞 스냅샷과 비교하면 오늘 체결이 이미 들어간 값끼리
    # 비교돼 오늘 수익금이 0 으로 보인다(9/4 폰 실측: 370,697 → 370,697).
    today = pd.Timestamp(context.as_of).date()
    earlier = [v for d, v in zip(daily["day"], navs) if d < today]
    previous_nav = earlier[-1] if earlier else None
    base = navs[0]
    index = [100.0 * v / base for v in navs]
    peak = pd.Series(navs).cummax()
    drawdown = [float(v / p - 1.0) for v, p in zip(navs, peak)]
    peak_now = max(max(navs), nav_now)
    return {
        "currency": "USD",
        "nav": nav_now,
        "previous_nav": previous_nav,
        "nav_change": None if previous_nav is None else nav_now - previous_nav,
        "daily_return": None if not previous_nav else nav_now / previous_nav - 1.0,
        "principal": principal,
        "total_pnl": nav_now - principal if principal > 0 else None,
        "cumulative_return": nav_now / principal - 1.0 if principal > 0 else None,
        "index_value": 100.0 * nav_now / base,
        "drawdown": nav_now / peak_now - 1.0,
        "mdd": min(drawdown + [nav_now / peak_now - 1.0]),
        "win_rate": (lambda r: float((r > 0).mean()) if len(r) else None)(
            pd.Series(navs).pct_change().dropna().loc[lambda x: x != 0.0]),
        "equity": float(valuation.equity_us),
        "cash": float(valuation.cash_usd),
        "curve": {"sessions": sessions, "nav": navs, "index": index, "drawdown": drawdown,
                  "benchmark": [None] * len(navs), "benchmark_drawdown": [None] * len(navs),
                  "benchmark_note": "달러 슬리브 — 벤치마크 지수 미배선", "benchmark_label": {"label": "—"}},
    }


def _apply_us_sleeve(view: dict[str, Any], sleeve: dict[str, Any]) -> None:
    """kpis·equity·performance 를 슬리브 숫자로 덮는다. 종목·주문 패널은 이미 시장별이다."""
    k = view.get("kpis") or {}
    k.update({
        "currency": "USD", "nav": sleeve["nav"], "nav_after_tax": None, "principal": sleeve["principal"] or None,
        "today_pnl": sleeve["nav_change"], "total_pnl": sleeve["total_pnl"], "mdd": sleeve["mdd"],
        "win_rate": sleeve["win_rate"],
        "equity": sleeve["equity"], "cash_krw": 0.0, "cash_usd": sleeve["cash"], "equity_kr": 0.0,
        "daily_return": sleeve["daily_return"], "cumulative_return": sleeve["cumulative_return"],
        "index_value": sleeve["index_value"], "drawdown": sleeve["drawdown"],
        "exposure": sleeve["equity"] / sleeve["nav"] if sleeve["nav"] > 0 else None,
        # 장중 달러 시세는 아직 안 붙였다 — 없는 값을 0 으로 보이지 않게 비운다. `live_is_close` 도
        # 비운다: 화면은 그것이 true 면 live_nav(없음)를 총자산으로 써서 0 이 된다(9/4 폰 실측).
        "live_is_close": None, "live_session_open": None,
        "live_nav": None, "live_change": None, "live_today_pnl": None, "live_drawdown": None, "live_mdd": None,
        "live_equity": None, "live_covered": 0,
    })
    view["kpis"] = k
    view["equity"] = sleeve["curve"]
    p = view.get("performance")
    if p:
        p.update({
            "currency": "USD", "nav": sleeve["nav"], "previous_nav": sleeve["previous_nav"],
            "nav_change": sleeve["nav_change"], "pnl": sleeve["nav_change"], "inflow": 0.0,
            "daily_return": sleeve["daily_return"], "cumulative_return": sleeve["cumulative_return"],
            "index_value": sleeve["index_value"], "drawdown": sleeve["drawdown"],
            "principal": sleeve["principal"], "total_pnl": sleeve["total_pnl"],
            "mode_note": f"{p.get('mode_note') or ''} · 달러 슬리브(USD)",
        })


# -- 종합(국장 모의계좌 + 미장 슬리브) ------------------------------------------------


def combined_payload(
    store_kr: Store, store_us: Store, clock: Any, *, as_of: datetime, lookback: int, live_quotes: Any = None
) -> dict[str, Any]:
    """종합 탭 — 두 장부를 원화로 합친 총자산·증감·수익률·곡선. **종목·주문은 없다** (사용자 요청 2026-09-05).

    국장 = LS 모의계좌 장부(data/_paper), 미장 = shadow 장부의 달러 슬리브(data/_shadow, `_us_sleeve`).
    환산은 국장 스냅샷의 환율 하나로 한다 — 곡선의 과거 날짜도 같은 환율을 쓴다(환율 손익을 섞지 않고
    "지금 환율로 본 자산" 을 보여준다; 문구로 적는다). 원금은 두 장부의 입출금 합(달러는 환산).
    """
    ctx_kr = build_context(store_kr, clock, as_of=as_of, market="KR", live_quotes=live_quotes)
    k_kr = kpis(store_kr, ctx_kr)
    perf_kr = performance_module.daily(store_kr, as_of=as_of, snapshot=ctx_kr.snapshot, fill_limit=None).as_dict()
    eq_kr = equity_curve(store_kr, ctx_kr, lookback=lookback)
    fx = float(ctx_kr.snapshot.valuation.fx_rate or 0.0)
    sleeve = None
    try:
        ctx_us = build_context(store_us, clock, as_of=as_of, market="US", live_quotes=None)
        sleeve = _us_sleeve(store_us, ctx_us, lookback=lookback)
        us_positions = len([p for e, p in ctx_us.book.positions.items() if str(e).startswith("US:") and p.quantity > 0])
    except LookupError:
        us_positions = 0
    us_nav = (sleeve["nav"] * fx) if sleeve else 0.0
    us_change = ((sleeve["nav_change"] or 0.0) * fx) if sleeve else 0.0
    us_principal = (sleeve["principal"] * fx) if sleeve else 0.0
    us_equity = (sleeve["equity"] * fx) if sleeve else 0.0
    us_cash = (sleeve["cash"] * fx) if sleeve else 0.0
    nav = float(k_kr["nav"]) + us_nav
    today_pnl = float(k_kr.get("today_pnl") or 0.0) + us_change
    principal = float(k_kr.get("principal") or 0.0) + us_principal
    total_pnl = nav - principal if principal > 0 else None
    # 곡선 — 날짜 합집합, 각 장부는 마지막 알던 값을 끌고 간다(forward fill).
    kr_series = pd.Series(eq_kr["nav"], index=pd.to_datetime(eq_kr["sessions"])) if eq_kr["sessions"] else pd.Series(dtype=float)
    us_series = pd.Series(sleeve["curve"]["nav"], index=pd.to_datetime(sleeve["curve"]["sessions"])) * fx if sleeve else pd.Series(dtype=float)
    idx = kr_series.index.union(us_series.index).sort_values()
    total = kr_series.reindex(idx).ffill().fillna(0.0) + us_series.reindex(idx).ffill().fillna(0.0)
    total = total[total > 0]
    sessions = [d.date().isoformat() for d in total.index]
    navs = [float(v) for v in total.to_list()]
    base = navs[0] if navs else nav
    index = [100.0 * v / base for v in navs]
    peak = pd.Series(navs).cummax()
    drawdown = [float(v / p - 1.0) for v, p in zip(navs, peak)] if navs else []
    peak_now = max(navs + [nav]) if navs else nav
    # 일별 수익률은 **입출금을 뺀다** — 9/2 달러 입금이 +96% 로 찍히면 달력이 거짓말을 한다. 두 장부의 capital_flows
    # 를 날짜별로 합쳐(달러는 같은 환율) (NAV_t − 입금_t) / NAV_{t−1} − 1 로 잰다.
    flows_by_day: dict[str, float] = {}
    for st, mult_of in ((store_kr, lambda c: 1.0 if c == "KRW" else fx), (store_us, lambda c: fx if c == "USD" else 0.0)):
        try:
            fl = st.get("capital_flows", as_of=as_of, entity=ledger_module.ACCOUNT, lookback=lookback * 3)
        except Exception:  # noqa: BLE001 — 장부가 없으면 입출금도 없다
            continue
        for row in ([] if fl.empty else fl.to_dict(orient="records")):
            key = pd.Timestamp(row["valid_from"]).date().isoformat()
            flows_by_day[key] = flows_by_day.get(key, 0.0) + float(row["amount"]) * mult_of(str(row["currency"]))
    daily = [0.0]
    for i in range(1, len(navs)):
        prev_nav = navs[i - 1]
        daily.append((navs[i] - flows_by_day.get(sessions[i], 0.0)) / prev_nav - 1.0 if prev_nav > 0 else 0.0)
    days = [{"session": s_, "return": float(r), "nav": float(v)} for s_, r, v in zip(sessions, daily, navs)]
    months: dict[str, float] = {}
    for day in days:
        key = day["session"][:7]
        months[key] = (1.0 + months.get(key, 0.0)) * (1.0 + day["return"]) - 1.0
    previous_nav = navs[-1] if navs and sessions[-1] < as_of.date().isoformat() else (navs[-2] if len(navs) > 1 else None)
    k = {
        "currency": "KRW", "combined": True, "nav": nav, "nav_after_tax": None,
        "principal": principal or None, "today_pnl": today_pnl, "total_pnl": total_pnl,
        "daily_return": today_pnl / (nav - today_pnl) if nav - today_pnl > 0 else None,
        "cumulative_return": nav / principal - 1.0 if principal > 0 else None,
        "index_value": 100.0 * nav / base if base else None, "drawdown": nav / peak_now - 1.0 if peak_now else None,
        "mdd": min(drawdown + [nav / peak_now - 1.0]) if navs else None, "win_rate": None, "win_samples": 0,
        "equity": float(k_kr.get("equity") or 0.0) + us_equity, "equity_kr": float(k_kr.get("equity_kr") or 0.0),
        "equity_us": (sleeve["equity"] if sleeve else 0.0), "cash_krw": float(k_kr.get("cash_krw") or 0.0) + us_cash,
        "cash_usd": (sleeve["cash"] if sleeve else 0.0), "fx_rate": fx,
        "exposure": (float(k_kr.get("equity") or 0.0) + us_equity) / nav if nav > 0 else None,
        "action_reflection": k_kr.get("action_reflection"), "action_reflection_floor": k_kr.get("action_reflection_floor"),
        "positions": int(k_kr.get("positions") or 0) + us_positions,
        "live_nav": None, "live_change": None, "live_today_pnl": None, "live_drawdown": None, "live_mdd": None,
        "live_equity": None, "live_covered": 0, "live_is_close": None, "live_session_open": None,
        "kr_nav": float(k_kr["nav"]), "us_nav_usd": (sleeve["nav"] if sleeve else 0.0), "us_nav_krw": us_nav,
    }
    for key in ("killswitch", "rejected", "orders_total", "ai_state", "policy"):
        if key in k_kr:
            k[key] = k_kr[key]
    perf = dict(perf_kr)
    perf.update({
        "currency": "KRW", "nav": nav, "previous_nav": previous_nav, "nav_change": today_pnl, "pnl": today_pnl,
        "inflow": 0.0, "daily_return": k["daily_return"], "cumulative_return": k["cumulative_return"],
        "index_value": k["index_value"], "drawdown": k["drawdown"], "principal": principal, "total_pnl": total_pnl,
        "mode": "PAPER+SHADOW", "mode_note": f"국장 모의계좌 + 미장 shadow 슬리브 · 달러는 현재 환율 {fx:,.0f}원 으로 환산",
        "fills": [], "fill_count": 0, "buy_count": 0, "sell_count": 0, "realized_pnl": None,
    })
    return {
        # risk 는 국장 장부의 것 — KPI 줄이 킬스위치·낙폭 밴드 문구를 여기서 읽는다(None 이면 화면이 죽는다).
        "market": "ALL", "system": system(store_kr, ctx_kr), "kpis": k, "risk": risk(store_kr, ctx_kr), "alerts": [],
        "positions": [], "watchlist": [], "decision": None, "orders": [],
        "equity": {"sessions": sessions, "nav": navs, "index": index, "drawdown": drawdown,
                   "benchmark": [None] * len(navs), "benchmark_drawdown": [None] * len(navs),
                   "benchmark_note": "종합 — 벤치마크 없음(두 시장 합산)", "benchmark_label": {"label": "—"}},
        "calendar": {"days": days, "months": [{"month": m, "return": v} for m, v in sorted(months.items())],
                     "indices": _index_daily_returns(store_kr, as_of=as_of, lookback=lookback)},
        "performance": perf,
    }


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
    # **장중인지 아닌지를 같이 내려준다.** 장외에는 t8407 이 마지막 체결가를
    # 주는데 그게 곧 전일 종가라 변화율이 0.00% 로 나온다. 그 0.00% 는
    # "장이 열렸는데 안 움직였다" 와 뜻이 완전히 다른데, 화면이 못 가르면
    # 정상 동작이 고장으로 읽힌다 — 2026-08-19 08:51(개장 9분 전)에 실제로
    # "실시간이 왜 안 움직이냐" 는 물음이 나왔다.
    session_open = is_regular_session(Market.KR, context.as_of)
    blank = {
        "nav": None, "change": None, "equity": None,
        "covered": 0, "session_open": session_open,
    }
    if cache is None or not positions:
        return blank

    quotes = cache.get(list(positions))
    if not quotes:
        return blank

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
        "session_open": session_open,
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
    # 환산은 회계가 한다. 여기서 amount 를 그냥 더하면 달러 입금이 1원으로
    # 섞인다 — 실측(2026-08-22) 실전 창고에서 원금 18,422원이 5,009원으로
    # 세어져 총 수익금이 +272% 로 나왔다.
    principal = ledger_module.principal(store, as_of=as_of)
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
    peak_nav = None
    if not curve.empty:
        ordered = curve.sort_values(["valid_from", "observed_at"])
        returns = ordered["twr_return"].astype(float)
        # **승률은 일간이다.** LS_KR 은 종목별 매도 기준이었는데, 우리는 아직
        # 매도 이력이 거의 없다. 없는 것을 재면 표본 두세 건짜리 승률이 나오고
        # 그 숫자는 성적이 아니라 노이즈다. 무엇을 세는지 화면에 적는다.
        traded = returns[returns != 0.0]
        win_rate = float((traded > 0).mean()) if len(traded) else None
        mdd = float(ordered["drawdown"].astype(float).min())
        peak_nav = float(ordered["nav"].astype(float).max())

    live = _live_valuation(context, valuation)

    # **장중 낙폭 — 화면용이다. 킬스위치는 이걸 안 본다.**
    #
    # 사용자가 MDD 를 장중으로 보고 싶다고 했다(2026-08-19). 화면에서는 맞는
    # 요구다 — 지금 얼마나 빠져 있는지가 궁금한 것이 자연스럽다.
    #
    # **그런데 킬스위치는 이 값으로 판정하면 안 된다.** 하루 안의 출렁임이
    # 낙폭으로 잡히면 `killswitch.drawdown_trigger`(30%)가 멀쩡한 날 매수를
    # 막는다. 그리고 M3 완료 기준(OOS MDD ≤20%)은 백테스트 종가 기준이라
    # 장중 값과 섞으면 비교 자체가 깨진다.
    #
    # 그래서 **별도 필드**로 내려보내고, 판정 경로(executor/guards)는 손대지
    # 않는다. 화면이 어느 쪽인지 배지로 말한다.
    live_drawdown = None
    if live["nav"] is not None and peak_nav and peak_nav > 0:
        live_drawdown = live["nav"] / peak_nav - 1.0

    return {
        "nav": nav,
        "nav_after_tax": valuation.nav_after_tax,
        # **장중 재평가 — 참고값이다.** 위의 nav·daily_return·mdd 는 전부
        # 종가 기준이고(accounting.md §2 — 15:40 하루 한 번), 벤치마크도 같은
        # 시각으로 잰다. 그 둘을 섞으면 차이가 통째로 가짜 초과수익이 된다.
        # 그래서 **따로 담고 화면도 따로 그린다.** nav_daily 에 쓰지 않는다.
        "live_nav": live["nav"],
        "live_change": live["change"],
        "live_session_open": live.get("session_open"),
        # **장이 끝나면 마지막 체결가가 곧 오늘 종가다.**
        #
        # 위 `nav` 는 창고의 종가로 선다. 그런데 일봉 수집은 장이 끝난 뒤에야
        # 돌기 때문에, 15:30~수집 완료 사이에는 창고에 오늘 종가가 없고 `nav`
        # 는 **어제** 종가를 쓴다. 그동안 `today_pnl` 은 0 이 되는데, 그건
        # "오늘 안 움직였다" 가 아니라 "오늘을 아직 모른다" 이다.
        #
        # 실측 2026-08-19 16:22(shadow): nav 9,881,077(8/18 종가) ·
        # 실시간 9,769,387(8/19 종가) · today_pnl 0 · 실제 -1.13%.
        # 화면이 "오늘 수익률 0.00%" 를 보여줬다.
        #
        # 그 구간에서는 실시간 값이 **참고값이 아니라 확정값**이다. 화면이
        # 그걸 알고 고를 수 있도록 사실을 내려보낸다 — 화면이 시각을 보고
        # 스스로 판단하면 장 마감 시각이 두 곳에 생긴다.
        #
        # 수집이 따라잡으면 `nav` 와 실시간이 같아지므로, 그 뒤에도 이 값을
        # 써서 틀리는 경우는 없다.
        "live_is_close": (
            live.get("session_open") is False and live["nav"] is not None
        ),
        # **오늘 손익의 장중 판.** `today_pnl` 은 마지막 회계 스냅샷(=직전
        # 세션 종가)까지의 확정 손익이라 장중에 안 움직인다. 그것과 지금
        # 평가액의 차이가 오늘 장중에 생긴 손익이다.
        #
        # 여기서 계산해 내려주는 이유: 화면이 `live_nav - nav` 를 하면 같은
        # 규칙이 두 곳에 생기고, 나중에 한쪽만 고쳐진다. 회계 규칙은 서버가
        # 한 번만 정한다.
        "live_today_pnl": (
            None if live["nav"] is None else live["nav"] - nav
        ),
        # 장중 낙폭. **킬스위치는 이 값을 안 본다**(위 주석 참고).
        "live_drawdown": live_drawdown,
        # 장중을 포함한 최대낙폭 — 둘 중 더 깊은 쪽. 화면 표시용이다.
        "live_mdd": (
            mdd if live_drawdown is None or mdd is None
            else min(mdd, live_drawdown)
        ),
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
    # 장부 하나에 두 시장이 살 수 있다(shadow). 이 시장 것만 — 미장 보기에 국장
    # 24종목이 서면 "미장 포지션" 으로 읽힌다(2026-09-02 실측).
    prefix = f"{context.market}:"
    for entity_id, position in sorted(context.book.positions.items()):
        if not str(entity_id).startswith(prefix):
            continue
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
    targets = _target_weights(store, as_of=as_of)
    # **선정된 후보 그 자체를 보여준다** — 점수 상위 12 는 "관찰 목록" 이라 KPI 의
    # "후보 24" 와 어긋나 읽는 사람이 무엇인지 몰랐다(사용자 지적 2026-09-02). 목표
    # 비중이 기록된 종목이 곧 그날의 후보이고, 정렬은 합성 점수다. 목표 비중이
    # 없는 날(세션 전)만 점수 상위로 대신한다.
    prefix = f"{context.market}:"
    chosen = [e for e in targets if str(e).startswith(prefix)] if targets else []
    if chosen:
        top = sorted(((e, scores.get(e, 0.0)) for e in chosen), key=lambda item: item[1], reverse=True)
    else:
        top = sorted(scores.items(), key=lambda item: item[1], reverse=True)[:WATCHLIST_ROWS]
    entities = [entity for entity, _ in top]
    names = _names(store, as_of=as_of, entities=entities)
    quotes = _quotes(store, as_of=as_of, entities=entities, market=context.market)
    held = {
        entity: position
        for entity, position in context.book.positions.items()
        if position.quantity > 0
    }

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
    """체결별 실현손익. **계산은 회계가 한다** (accounting/performance.py).

    여기서 다시 접으면 화면과 메일이 다른 식으로 실현손익을 내고, 그때 어느
    쪽이 맞는지 판정할 방법이 없다 (accounting.md §8).
    """
    return performance_module.realized_by_trade(store, as_of=as_of)


def orders(store: Store, context: Context) -> list[dict[str, Any]]:
    """주문과 체결을 한 표로. 최근이 위다.

    체결은 ``trades``, 주문은 ``orders`` 다. 두 표를 따로 두면 "낸 주문이
    체결됐는지" 를 사람이 눈으로 맞춰야 한다.
    """
    as_of = context.as_of
    frame = store.get(ORDERS, as_of=as_of, lookback=10)
    # 이 시장 것만 — shadow 장부엔 국장·미장 주문이 같이 살아 미장 보기에 국장 매도가 섰다
    # (2026-09-03 폰 실측). 포지션과 같은 규칙이다.
    if not frame.empty:
        frame = frame[frame["entity_id"].astype(str).str.startswith(f"{context.market}:")]
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


#: 캘린더에서 하루를 눌렀을 때 같이 보여줄 지수. **창고에 실제로 있는 이름**
#: 이다 — 없는 지수를 넣으면 그 줄만 조용히 빈다.
#:
#: 이름을 여기 적는 이유: `benchmark.kr_index` 는 보상 함수가 쓰는 **공식**
#: 벤치마크 하나뿐이고, 이 화면은 "그날 시장이 어땠나" 를 보는 참고용이라
#: 목록이 다르다. 둘을 한 키로 묶으면 벤치마크를 바꿀 때 화면이 같이 바뀐다.
CALENDAR_INDICES: tuple[tuple[str, str], ...] = (
    ("KR:IDX:KOSPI", "코스피"),
    ("KR:IDX:KOSDAQ", "코스닥"),
    ("US:IDX:SP500", "S&P 500"),
    ("US:IDX:NASDAQ", "나스닥"),
    ("US:IDX:DJIA", "다우"),
)


def _index_daily_returns(
    store: Store, *, as_of: datetime, lookback: int
) -> dict[str, dict[str, float]]:
    """세션 → {지수 이름: 그날 등락률}.

    **미장은 하루 밀린다.** 뉴욕 8/18 종가는 한국 8/19 새벽에 나온다. 그래도
    같은 날짜 칸에 넣는 이유는, 사람이 "8/18 에 시장이 어땠나" 를 물을 때
    떠올리는 것이 그 세션이기 때문이다. 날짜를 맞춰 밀면 오히려 "내 8/18
    수익률" 과 "미장 8/17" 이 한 줄에 서서 더 헷갈린다.

    없는 날은 **키를 안 만든다.** 0 으로 채우면 휴장이 보합으로 보인다.
    """
    frame = store.get(
        INDICES, as_of=as_of, lookback=lookback, columns=["close", "valid_from"],
        entity=[entity for entity, _ in CALENDAR_INDICES],
    )
    if frame.empty:
        return {}

    out: dict[str, dict[str, float]] = {}
    for entity, label in CALENDAR_INDICES:
        rows = frame[frame["entity_id"] == entity].sort_values("valid_from")
        if rows.empty:
            continue
        closes = rows["close"].astype(float)
        # 종가 0 은 휴장일 행이다. 그대로 두면 등락률이 -100% 로 튄다.
        keep = closes > 0.0
        closes = closes[keep]
        sessions = rows.loc[keep.index[keep], "valid_from"]
        changes = closes.pct_change()
        for stamp, change in zip(sessions, changes, strict=False):
            if pd.isna(change):
                continue
            day = pd.Timestamp(stamp).date().isoformat()
            out.setdefault(day, {})[label] = float(change)
    return out


def returns_calendar(
    store: Store, *, as_of: datetime, lookback: int, market: str = "KR"
) -> dict[str, Any]:
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
        return {"days": [], "months": [], "indices": {}}
    ordered = frame.sort_values(["valid_from", "observed_at"])
    if str(market).upper() == "US":
        # **달러 슬리브** — 장부 전체 TWR 이 아니라 equity_us+cash_usd 의 일간 변화. 달러 입출금이 있는
        # 날은 (NAV_t − 입금) / NAV_{t−1} 로 입금을 뺀다 — 안 빼면 9/2 입금일이 +∞% 가 된다.
        flows = store.get("capital_flows", as_of=as_of, entity=ledger_module.ACCOUNT, lookback=lookback * 3)
        usd_flow: dict[str, float] = {}
        if not flows.empty:
            usd = flows[flows["currency"].astype(str) == "USD"]
            for row in usd.to_dict(orient="records"):
                key = pd.Timestamp(row["valid_from"]).date().isoformat()
                usd_flow[key] = usd_flow.get(key, 0.0) + float(row["amount"])
        days = []
        previous: float | None = None
        for row in ordered.to_dict(orient="records"):
            nav_usd = float(row["equity_us"]) + float(row["cash_usd"])
            session = pd.Timestamp(row["valid_from"]).date().isoformat()
            if previous is None or previous <= 0:
                ret = 0.0
            else:
                ret = (nav_usd - usd_flow.get(session, 0.0)) / previous - 1.0
            if nav_usd > 0:
                days.append({"session": session, "return": ret, "nav": nav_usd})
                previous = nav_usd
        # 같은 세션의 스냅샷이 둘(05:20·16:00)이면 마지막 것만 — 앞 것은 국장 마감 전 중간값이다.
        collapsed: dict[str, dict[str, Any]] = {}
        for day in days:
            if day["session"] in collapsed:
                collapsed[day["session"]]["return"] = (1.0 + collapsed[day["session"]]["return"]) * (1.0 + day["return"]) - 1.0
                collapsed[day["session"]]["nav"] = day["nav"]
            else:
                collapsed[day["session"]] = dict(day)
        days = list(collapsed.values())
    else:
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
        # 하루를 눌렀을 때 "그날 시장은 어땠나" 를 같이 보여주기 위한 참고값.
        # **우리 수익률과 같은 칸에 섞지 않는다** — 지수는 지수고 우리는 우리다.
        "indices": _index_daily_returns(store, as_of=as_of, lookback=lookback),
    }


def calendar_payload(
    store: Store, *, as_of: datetime, lookback: int, market: str = "KR"
) -> dict[str, Any]:
    """별도 창의 캘린더가 쓰는 전부. ``nav_daily`` 만 읽는다.

    ``최고/최악의 날`` 을 여기서 고르는 이유는, 화면이 고르면 표시된 달만
    보고 고르게 되기 때문이다. 창 전체에서 골라야 "이 창의 최악" 이 된다.
    """
    calendar = returns_calendar(store, as_of=as_of, lookback=lookback, market=market)
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


# -- 분봉 ------------------------------------------------------------------

#: prices(일봉)와 절대 섞지 않는 별도 표. store/tables.py 참고.
INTRADAY_TABLE = "prices_intraday"

#: 화면 버튼과 같은 이름·순서. collectors/intraday_collector.py 의
#: INTERVAL_NCNT 와 짝이다 — 여기서 새 이름을 짓지 않는다.
INTRADAY_INTERVALS = ("1m", "5m", "15m", "1H", "4H")

#: 분봉은 하루에도 수백 개다. 전부 보내면 응답이 무거워지고 화면은 어차피
#: 최근 구간만 그린다(일봉 캔들도 dataZoom 으로 최근 120세션만 먼저 보여주는
#: 것과 같은 이유) — 대시보드 속도 최적화는 별도 진행 중이라 여기서 새로운
#: 무거운 응답을 만들지 않는다. 서버에서 자르고 나머지는 안 보낸다.
INTRADAY_ROW_LIMIT = 500

#: **당일만 보여주는 구간.** 장중에 계속 새 봉이 생기는 것들이다.
#:
#: 4H 를 뺀 이유: 하루에 두 봉뿐이라 "당일" 이 캔들 두 개다. 그건 차트가
#: 아니라 점이고, 애초에 4H 를 여는 사람은 며칠치 흐름을 보려는 것이다.
#: 1H(하루 7봉)도 장 초반에는 한두 개지만 마감 무렵이면 하루가 찬다.
INTRADAY_TODAY_ONLY = ("1m", "5m", "15m", "1H")

#: available_intraday_intervals 가 열어 보는 창. 수집기 자체가 보유·
#: 워치리스트 종목의 최근 며칠만 받으므로(intraday_collector.py 모듈독스트링),
#: 이보다 긴 창을 열어도 어차피 빈 파티션만 늘어난다.
INTRADAY_LOOKBACK_DAYS = 30


def available_intraday_intervals(
    store: Store, *, as_of: datetime, entity_id: str, market: str
) -> list[str]:
    """이 종목에 **실제로 분봉이 있는** interval 만 돌려준다.

    "없는 봉을 그리지 마라" 원칙의 반대쪽이다. 화면의 1m/5m/15m/1H/4H
    버튼은 창고에 그 구간이 있을 때만 켜진다(``templates/trading.html``) —
    없는데 켜 두면 눌렀을 때 빈 화면이 뜨고, 그게 "고장" 인지 "원래
    수집 범위 밖" 인지 사용자가 구분 못 한다.
    """
    return available_intraday_intervals_by_entity(
        store, as_of=as_of, entities=[entity_id], market=market
    ).get(entity_id, [])


def available_intraday_intervals_by_entity(
    store: Store, *, as_of: datetime, entities: list[str], market: str
) -> dict[str, list[str]]:
    """여러 종목의 분봉 보유 현황을 **한 번 읽어서** 나눠 준다.

    마켓 탭은 한 화면에 패널이 여섯이다(지수 둘 + ETF 넷). 패널마다
    :func:`available_intraday_intervals` 를 부르면 같은 파티션을 여섯 번
    연다 — 이 저장소가 반복해서 데인 지점이라(같은 표를 패널 수만큼 읽기)
    묶어서 읽는다. 판단 규칙 자체는 한 곳이다: 위 함수가 이걸 부른다.
    """
    if not entities:
        return {}
    frame = store.get(
        INTRADAY_TABLE,
        as_of=as_of,
        entity=entities,
        market=market,
        lookback=INTRADAY_LOOKBACK_DAYS,
        columns=["entity_id", "interval"],
    )
    if frame.empty:
        return {entity: [] for entity in entities}
    have: dict[str, set[str]] = {}
    for row in frame.itertuples(index=False):
        have.setdefault(str(row.entity_id), set()).add(str(row.interval))
    return {
        entity: [i for i in INTRADAY_INTERVALS if i in have.get(entity, set())]
        for entity in entities
    }


def intraday_candles(
    store: Store, *, as_of: datetime, entity_id: str, market: str, interval: str
) -> dict[str, Any]:
    """한 종목의 분봉. ``candles()`` 와 같은 모양(``sessions``·``ohlc``·
    ``volume``·``ma``)으로 돌려준다 — 화면의 캔들 그리기 코드가 일봉·분봉을
    분기 없이 같이 쓸 수 있게 하기 위해서다.

    ``sessions`` 는 날짜가 아니라 **타임스탬프**(ISO, 초 단위)다. 일봉의
    "그 날" 과 달리 분봉은 하루 안에도 여러 봉이 있어 날짜만으로는 못 가른다
    — 이 차이를 화면(trading.js)이 x축 라벨 포맷에서 알아야 한다.

    체결 흔적(``trades``)은 아직 안 얹는다 — 일봉 ``candles()`` 의 마킹은
    체결일(날짜) 기준이고, 분봉은 체결 **시각**까지 맞춰야 정확한 자리에
    찍힌다. 지금은 화면이 일봉에서만 흔적을 보여주는 것으로 범위를
    좁혔다(분봉 버튼을 켜는 것 자체가 이번 작업의 목표다).
    """
    if interval not in INTRADAY_INTERVALS:
        raise ValueError(f"모르는 interval: {interval!r} ({INTRADAY_INTERVALS} 중 하나여야 한다)")

    empty = {
        "entity_id": entity_id, "interval": interval,
        "sessions": [], "ohlc": [], "volume": [], "ma": {},
    }

    frame = store.get(
        INTRADAY_TABLE,
        as_of=as_of,
        entity=entity_id,
        market=market,
        lookback=INTRADAY_LOOKBACK_DAYS,
        columns=["open", "high", "low", "close", "volume", "valid_from", "interval"],
    )
    if frame.empty:
        return empty

    # interval 은 store.get 의 필터 축이 아니다(market·entity 만 SQL 단계에서
    # 거른다) — 여기서 좁힌다. 한 종목·한 창의 행 수가 작아 비용이 안 된다.
    scoped = frame[frame["interval"] == interval].sort_values("valid_from")
    if scoped.empty:
        return empty

    # **당일 것만 보여준다** (사용자 요청 2026-08-19).
    #
    # 분봉을 여러 날 이어 붙이면 장 마감(15:30)과 다음 날 개장(09:00) 사이의
    # 17시간 반이 **봉 하나 폭**으로 붙는다. 그 자리에 밤사이 갭이 통째로
    # 들어가 캔들 하나가 유난히 길어지고, 이동평균도 그 점프를 그대로 넘는다 —
    # 화면은 "장중에 급변했다" 로 보이는데 실제로는 장이 닫혀 있던 시간이다.
    #
    # 창고에는 여러 날이 남아 있다(1m 은 1.3거래일, 4H 는 300거래일+). 지우지
    # 않고 **읽을 때만** 좁힌다 — 백테스트·피처는 그 과거를 쓴다.
    # **개수로 되돌리지 않는다.** 장 초반에는 당일 봉이 한두 개인 것이 정상이고,
    # 그때 며칠치로 도로 넓히면 화면이 아침마다 다른 규칙으로 그려진다.
    if interval in INTRADAY_TODAY_ONLY:
        last_day = pd.Timestamp(scoped["valid_from"].max()).date()
        scoped = scoped[scoped["valid_from"].dt.date == last_day]
        if scoped.empty:
            return empty
    # 자르는 자리는 정렬 뒤, 최근 N개만 — INTRADAY_ROW_LIMIT 문서 참고.
    scoped = scoped.tail(INTRADAY_ROW_LIMIT)

    closes = scoped["close"].astype(float)
    sessions = [pd.Timestamp(value).isoformat() for value in scoped["valid_from"]]

    return {
        "entity_id": entity_id,
        "interval": interval,
        "sessions": sessions,
        "ohlc": [
            [float(row["open"]), float(row["close"]), float(row["low"]), float(row["high"])]
            for row in scoped.to_dict(orient="records")
        ],
        "volume": [float(value) for value in scoped["volume"].fillna(0.0)],
        "ma": {
            f"ma{window}": [
                None if pd.isna(value) else float(value)
                for value in closes.rolling(window).mean()
            ]
            for window in (5, 20, 60)
        },
    }


# -- 내부 ----------------------------------------------------------------------


def _latest_scores(store: Store, *, as_of: datetime) -> dict[str, float]:
    """종목별 최신 합성 점수. 신호가 없으면 빈 dict — 0 으로 채우지 않는다."""
    # **하루치만 읽는다.** 5일창은 229,000행(0.9초)이고 합성은 최신 세션만 쓴다 —
    # 주말·연휴처럼 하루창이 비면 그때만 5일로 넓힌다 (2026-08-28 실측 3.4초 → 1.4초).
    columns = ["entity_id", "analyst", "score", "confidence", "observed_at", "valid_from"]
    signals = store.get(SIGNALS, as_of=as_of, lookback=1, columns=columns)
    if signals.empty:
        signals = store.get(SIGNALS, as_of=as_of, lookback=5, columns=columns)
    if signals.empty:
        return {}
    signals = signals[signals["valid_from"] == signals["valid_from"].max()]
    weights = analyst_weights(store, as_of=as_of, market="KR")
    if not weights:
        return {}
    from quant_rl_trading.selector.combine import combined_scores

    combined = combined_scores(signals, weights)
    return {str(entity): float(value) for entity, value in combined.items()}


def _names(store: Store, *, as_of: datetime, entities: list[str]) -> dict[str, str]:
    """종목 이름. 창고에 묻는 규칙은 ``store/names.py`` 한 벌뿐이다."""
    return names_module.of(store, as_of=as_of, entities=entities)


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


def _broker_label(store: Store, mode_code: str, *, as_of: datetime) -> str:
    """배지에 적는 브로커. 모드마다 다르다 — 모의계좌 장부에 "미연결" 이라 적으면
    주문이 실제로 나가는 계좌를 없는 것으로 읽는다(2026-08-28 실측)."""
    if mode_code == mode_module.PAPER:
        try:
            print_ = str(store.config("execution.live_account_fingerprint_paper", as_of=as_of))[:12]
        except Exception:
            print_ = "?"
        return f"LS 모의투자 · 계좌 {print_}"
    if mode_code == mode_module.LIVE:
        return "LS 실전 (세션이 붙일 때만 전송)"
    return "PaperBroker · 내부 시뮬레이션"


def system(store: Store, context: Context) -> dict[str, Any]:
    """상단 상태 바. **모드와 창고를 화면이 항상 말한다.**

    shadow 창고를 보면서 실전이라고 착각하는 것이 이 화면에서 가능한 가장
    비싼 오해다. 그래서 창고 경로에서 모드를 유도해 배지로 띄운다 — 사람이
    설정을 기억하게 두지 않는다.
    """
    root = str(store.root)
    # 판정 명단은 ``store/mode.py`` 한 벌뿐이다. 화면과 메일이 각자 판정하면
    # 언젠가 한쪽만 새 창고 이름을 배우고, 그때 어느 쪽이 맞는지 알 수 없다.
    mode = mode_module.of(root)

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

    signals = store.get(SIGNALS, as_of=as_of, lookback=1, columns=["observed_at"])
    if signals.empty:
        signals = store.get(SIGNALS, as_of=as_of, lookback=3, columns=["observed_at"])
    return {
        "mode": mode.code,
        "mode_note": mode.note,
        "store_root": root,
        "broker": _broker_label(store, mode.code, as_of=as_of),
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
                "mode": mode_module.of(store.root).code,
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
            "performance": None,
        }
    kpi = kpis(store, context)
    risk_state = risk(store, context)
    view = {
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
        "calendar": returns_calendar(store, as_of=context.as_of, lookback=lookback, market=market),
        # 성과 넷(매매내역·수익률·총수익률·자산증감)은 **회계가 접어 준다.**
        # 메일도 같은 함수를 읽는다 — 화면과 메일이 다른 숫자를 말하면 어느
        # 쪽이 맞는지 판정할 방법이 없다 (accounting.md §8).
        # **이 요청이 접은 스냅샷을 그대로 넘긴다.** 창고의 nav_daily 만 읽게
        # 두면 회계 크론(23:20)이 아직 안 돈 시각에 KPI 는 오늘을, 성과 칸은
        # 어제를 말한다. 화면 안에서 두 숫자가 갈리는 것이 여기서 가능한
        # 가장 흔한 사고다.
        "performance": performance_module.daily(
            store, as_of=context.as_of, snapshot=context.snapshot, fill_limit=None
        ).as_dict(),
    }
    if str(market).upper() == "US":
        sleeve = _us_sleeve(store, context, lookback=lookback)
        if sleeve is not None:
            _apply_us_sleeve(view, sleeve)
    return view
