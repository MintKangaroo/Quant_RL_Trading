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

from quant_rl_trading.collectors.benchmark_etf import BENCHMARK_ETF
from quant_rl_trading.collectors.market_hours import Market, trading_days
from quant_rl_trading.collectors.publication import publication_policy
from quant_rl_trading.replay.clock import ReplayClock
from quant_rl_trading.reporting.sessions import expected_session
from quant_rl_trading.store import Store
from quant_rl_trading.store.errors import ConfigNotFound

#: (키, 이름, 테이블, 달력 시장, market 필터, entity 필터, **유예 키**)
#: 유예 키가 None 이면 그 시장의 기본값(`system.freshness_grace_seconds_{kr,us}`)을 쓴다.
#: **소스마다 나오는 시각이 다르다** — 시장 하나에 값 하나로는 못 맞춘다.
DATASETS: tuple[tuple[str, str, str, Market, str | None, str | None, str | None], ...] = (
    ("kr_prices", "국장 시세", "prices", Market.KR, "KR", None, None),
    ("kr_index", "국장 지수", "indices", Market.KR, None, "KR:IDX:KOSPI", None),
    ("us_index", "미장 지수", "indices", Market.US, None, "US:IDX:SP500", None),
    ("us_prices", "미장 시세", "prices", Market.US, "US", None, None),
    ("fx", "환율", "fx", Market.US, None, "FX:USDKRW", None),
    # FINRA 일별 공매도 거래량 — flow_us 의 입력. 2026-08-18 백필 뒤 일일 수집이 빠져
    # 열흘을 조용히 멈춰 있었다(8/29 발견). 띠에 올려 두면 다음엔 하루 만에 보인다.
    ("us_short", "미장 공매도", "short_flow", Market.US, "US", None, None),
    # **종료 판정의 대조군**(milestones.md). 2026-09-18 확인 시점에 창고에 0행이었다 —
    # ETF 는 유니버스에 없어 일상 수집 어디에도 안 걸리고, 띠에도 없어 그 공백이
    # 아무 데도 안 보였다. 판정일에 발견하면 소급해 채우는 수밖에 없고, 그때 채우면
    # "결과를 보고 창을 고른" 것이 된다.
    # **KRX 는 이 값을 다음 날 아침에 낸다** — 수집도 화~토 08:35 이다. 그래서 국장 기본
    # 유예(40분)로 재면 평일 16:40 부터 다음 날 아침까지 매일 빨간불이 켜진다. 유예를
    # 따로 준다(16:00 공표 → 다음 날 10:00).
    ("kr_benchmark", "판정 벤치마크", "indices", Market.KR, None, BENCHMARK_ETF,
     "system.freshness_grace_seconds_kr_benchmark"),
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
        # 다른 종목/시장 데이터로 결측 또는 읽기 실패를 정상화하지 않는다.
        return None
    if frame.empty:
        return None
    return frame["valid_from"].max().date()


def _lag_sessions(market: Market, observed: date, expected: date) -> int:
    if observed >= expected:
        return 0
    return len(trading_days(market, observed + timedelta(days=1), expected))


def _within_grace(
    store: Store, market: Market, *, expected: date, as_of: datetime, key: str | None = None
) -> bool:
    """기대 세션이 공표된 지 유예(초) 안인가 — 수집 크론이 아직 안 돌았을 뿐인 구간.

    "기대 세션" 은 공표 정책(미장 마감+20분 = 05:20 KST)으로 세는데 수집은 08:40 에
    돈다. 그 사이엔 매일 '1세션 지연' 넷이 떴다(2026-09-17). 유예는 config 가 정한다.
    """
    name = key or f"system.freshness_grace_seconds_{market.value.lower()}"
    try:
        grace = float(store.config(name, as_of=as_of))
    except ConfigNotFound:
        return False
    if grace <= 0:
        return False
    try:
        published = publication_policy(store, market, clock=ReplayClock(as_of)).for_session(expected)
    except Exception:  # 거래일이 아니거나 정책을 못 읽으면 유예를 주지 않는다
        return False
    return (as_of - published).total_seconds() < grace


def summary(store: Store, *, as_of: datetime) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for key, label, table, market, market_filter, entity, grace_key in DATASETS:
        expected = expected_session(store, market, as_of=as_of)
        observed = _latest_session(store, table, as_of=as_of, market=market_filter, entity=entity)
        lag = _lag_sessions(market, observed, expected) if (observed and expected) else None
        if observed and expected and observed > expected:
            status = "unexpected"
        elif lag == 0:
            status = "ok"
        elif lag == 1 and expected and _within_grace(
            store, market, expected=expected, as_of=as_of, key=grace_key
        ):
            status = "pending"  # 공표는 됐고 수집 크론이 아직 안 돈 구간
        elif lag:
            status = "stale"
        else:
            status = "unknown"
        items.append({
            "key": key, "label": label,
            "expected": expected.isoformat() if expected else None,
            "observed": observed.isoformat() if observed else None,
            "lag_sessions": lag,
            "status": status,
        })
    return {"as_of": as_of.isoformat(), "items": items, "stale": [i["key"] for i in items if i["status"] == "stale"]}
