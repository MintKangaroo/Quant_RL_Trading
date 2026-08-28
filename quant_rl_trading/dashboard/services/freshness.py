"""데이터 기준일 — **화면의 숫자가 어느 날 것인지, 그리고 그날이 맞는지.**

브리핑·마켓·트레이딩이 각자 날짜를 적다가 자꾸 엇갈렸다(2026-08-28: 달력은 27일
지수를, 브리핑은 26일 미장 지수를, 트레이딩은 오늘 시세를). 기대 세션은
`reporting.sessions.expected_session`(공표 정책 기준 "이미 나왔어야 하는 마지막 거래일")
하나로 재고, 창고의 최신 세션과 견줘 **몇 세션 늦었는지**를 모든 탭 머리에 띄운다.
0 이면 ✓, 아니면 ⚠ 와 지연 일수 — 숫자 옆에서 "며칠 전 것" 이라고 말하게.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from quant_rl_trading.collectors.market_hours import Market, trading_days
from quant_rl_trading.reporting.sessions import expected_session
from quant_rl_trading.store import Store

#: (키, 이름, 테이블, 달력 시장, market 필터, entity 필터)
DATASETS: tuple[tuple[str, str, str, Market, str | None, str | None], ...] = (
    ("kr_prices", "국장 시세", "prices", Market.KR, "KR", None),
    ("kr_index", "국장 지수", "indices", Market.KR, None, "KR:IDX:KOSPI"),
    ("us_index", "미장 지수", "indices", Market.US, None, "US:IDX:SP500"),
    ("us_prices", "미장 시세", "prices", Market.US, "US", None),
    ("fx", "환율", "fx", Market.US, None, "USDKRW"),
)


def _latest_session(store: Store, table: str, *, as_of: datetime, market: str | None, entity: str | None) -> date | None:
    kwargs: dict[str, Any] = {"as_of": as_of, "lookback": 12, "columns": ["valid_from"]}
    if market:
        kwargs["market"] = market
    if entity:
        kwargs["entity"] = entity
    try:
        frame = store.get(table, **kwargs)
    except Exception:
        # entity 필터가 안 맞는 테이블(fx 의 entity 이름 등)은 필터 없이 다시 본다.
        kwargs.pop("entity", None)
        try:
            frame = store.get(table, **kwargs)
        except Exception:
            return None
    if frame.empty and entity:
        # entity 이름이 다른 테이블(fx 등) — 필터 없이 한 번 더.
        kwargs.pop("entity", None)
        try:
            frame = store.get(table, **kwargs)
        except Exception:
            return None
    if frame.empty:
        return None
    return frame["valid_from"].max().date()


def _lag_sessions(market: Market, observed: date, expected: date) -> int:
    if observed >= expected:
        return 0
    return len(trading_days(market, observed + timedelta(days=1), expected))


def summary(store: Store, *, as_of: datetime) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for key, label, table, market, market_filter, entity in DATASETS:
        expected = expected_session(store, market, as_of=as_of)
        observed = _latest_session(store, table, as_of=as_of, market=market_filter, entity=entity)
        lag = _lag_sessions(market, observed, expected) if (observed and expected) else None
        items.append({
            "key": key, "label": label,
            "expected": expected.isoformat() if expected else None,
            "observed": observed.isoformat() if observed else None,
            "lag_sessions": lag,
            "status": "ok" if lag == 0 else ("stale" if lag else "unknown"),
        })
    return {"as_of": as_of.isoformat(), "items": items, "stale": [i["key"] for i in items if i["status"] == "stale"]}
