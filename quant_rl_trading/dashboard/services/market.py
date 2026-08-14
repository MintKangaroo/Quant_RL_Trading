"""마켓 탭 집계 — **지금 시장이 어떤 상태인가.**

트레이딩 탭이 "우리 포트폴리오" 를 보여준다면, 이 화면은 그 바깥의 **환경**을
보여준다. 지수·환율·거시지표·시가총액 상위 종목 — 전부 우리가 아직 사지
않았어도 알아야 하는 것들이다.

## 이 화면에서 계산하지 않는 것

NAV·낙폭·손익은 여기 없다. 그건 트레이딩 탭의 것이고 회계(`accounting/`)
에서만 온다. 이 화면이 스스로 수익률을 계산하면 두 화면의 숫자가 어긋난다.

## 없는 것은 없다고 말한다

- 지수·시총 리더는 **그날 값이 창고에 없으면 목록에서 빠진다.** 0 으로
  채우면 "그 종목은 시총이 0" 이라는 다른 사실이 된다.
- 거시지표 실측이 아직 없는(예정) 건은 이 화면에 올리지 않는다 — 그건
  뉴스·일정 탭의 "예정" 패널이 하는 일이다. 여기는 **이미 일어난 것**만 본다.
- 트리맵에서 등락률을 못 잰 종목(그날 거래가 없어 직전 종가가 없는 등)은
  ``change`` 를 ``null`` 로 둔다. 화면이 그걸 회색으로 그린다 — 0% 로 채우면
  "보합" 이라는 다른 사실이 된다.

## 트리맵의 묶는 축 — 왜 업종이 아니라 시장인가, 왜 KR/US 두 맵인가

finviz 식 맵은 보통 섹터로 묶는다. 이 창고에도 `sectors` 테이블이 있지만
**업종 분류가 아니다.** `SECT_TP_NM`(KRX Open API 일별매매) 은 소속부 —
KOSDAQ 만 우량기업부·벤처기업부 등으로 갈리고, **KOSPI 942 종목은 전부
빈 문자열(미상)** 이다 (`store/tables.py` sectors TableSpec 참고). 이 축으로
묶으면 코스피가 통째로 "미상" 한 덩어리가 되어, 없는 업종 분류를 있는 것처럼
그리게 된다. 그래서 확실히 아는 축 — **시장(KR/US)** — 으로 나눈다. 한 맵
안에 두 시장을 그룹으로 넣는 대신 KR·US 를 **완전히 다른 맵 둘로** 쪼갠다 —
원·달러가 섞인 시가총액 크기를 한 사각형 트리에 나란히 두면 두 시장의
스케일이 뒤섞여 읽힌다(원화 시총이 절대적으로 더 크다).

## 미장 시가총액 — 아직 못 그린다

`leaders(market="US")` 와 `market_treemap()` 이 참조하는 `market_stats` 는
**시가총액 = 주가 × 상장주식수** 로 계산된 값인데, 이 창고에서 상장주식수
(shares outstanding)를 수집하는 곳은 `collectors/krx_openapi.py`
(KRX Open API, KR 전용) 하나뿐이다. `market_stats` 를 시장별로 세어 보면
KR 675만 행·US 0행이다 — US 종목은 시세(`prices`)와 유니버스(`universe`)는
있어도 상장주식수가 없어 market_cap 자체가 만들어지지 않는다. 지어내지
않고 US 리더·트리맵을 빈 목록으로 둔다 — 화면이 그 이유를 문구로 말한다.
**필요한 수집 작업**: US 상장주식수(shares outstanding) 소스를 찾아
`market_stats`(market="US", metric="shares"/"market_cap") 를 채우는 새
수집기. LS 해외주식 API 든 다른 소스든, `collectors/*` 는 이 탭의 소유가
아니라 여기서 만들지 않는다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from quant_rl_trading.store import Store

INDICES = "indices"
MARKET_STATS = "market_stats"
FX = "fx"
MACRO_RELEASES = "macro_releases"
UNIVERSE = "universe"
PRICES = "prices"

#: 등락을 재는 창. 화면의 lookback(타임머신 창)과 다르다 — 여기는 "어제 대비"
#: 하나만 있으면 되고, 창을 키워도 최신 두 세션만 쓴다.
RECENT_DAYS = 5

#: 대표 지수. **전부 다 강조하면 아무것도 강조되지 않는다.** 나머지는 전체
#: 표에 그대로 남는다 — 지운 것이 아니라 강조만 안 한 것이다.
INDEX_HIGHLIGHTS = ("KR:IDX:KRX TMI", "KR:IDX:KRX 300", "US:IDX:SP500")

#: 시가총액 상위 표의 행수. 화면은 한눈에 보는 것이지 전종목 스크리너가 아니다.
LEADER_ROWS = 15

#: 트리맵 시장별 상위 종목 수. **화면 상수다 — store.config 임계치가 아니다**
#: (LEADER_ROWS 와 같은 이유). 수천 종목을 다 그리면 사각형이 1픽셀이 되어
#: 브라우저가 느려진다. 60은 finviz 가 한 섹터 타일에 보통 담는 규모다.
TREEMAP_TOP_N = 60

#: 최근 거시지표 발표 카드 개수. 지표별 전체 현황은 뉴스·일정 탭이 맡는다 —
#: 여기는 "방금 무엇이 발표됐나" 만 짚는다.
MACRO_ROWS = 8


def _names(store: Store, *, as_of: datetime, entities: list[str]) -> dict[str, str]:
    if not entities:
        return {}
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


# -- 지수 ------------------------------------------------------------------


def indices(store: Store, *, as_of: datetime) -> dict[str, Any]:
    """지수 현황. 종가와 직전 세션 대비 등락률.

    `indices` 테이블에는 국장 KRX 업종·테마 지수와 해외 지수(US:IDX:SP500)가
    함께 산다 (prices 와 나눈 이유는 store/tables.py 참고 — 종목 유니버스에
    지수가 섞이면 커버리지 통계가 오염된다).
    """
    frame = store.get(
        INDICES,
        as_of=as_of,
        lookback=RECENT_DAYS,
        columns=["entity_id", "market", "close", "valid_from"],
    )
    if frame.empty:
        return {"highlights": [], "table": []}

    rows: list[dict[str, Any]] = []
    for entity, group in frame.sort_values("valid_from").groupby("entity_id"):
        # 휴장·미수집 세션은 종가가 0 이나 NaN 으로 들어올 수 있다 — 없는 값을
        # 등락 계산에 섞으면 지수가 하루 만에 -100% 로 보인다.
        closes = group["close"].astype(float)
        closes = closes[closes > 0]
        if closes.empty:
            continue
        last = float(closes.iloc[-1])
        previous = float(closes.iloc[-2]) if len(closes) >= 2 else None
        rows.append(
            {
                "entity_id": str(entity),
                "market": str(group["market"].iloc[-1]),
                "close": last,
                "change": (last / previous - 1.0) if previous else None,
            }
        )
    rows.sort(key=lambda row: (row["market"], row["entity_id"]))
    by_entity = {row["entity_id"]: row for row in rows}
    highlights = [by_entity[e] for e in INDEX_HIGHLIGHTS if e in by_entity]
    return {"highlights": highlights, "table": rows}


# -- 환율 --------------------------------------------------------------------


def fx(store: Store, *, as_of: datetime, lookback: int) -> dict[str, Any]:
    """원달러 환율. 최신값·전일대비·시계열.

    창고에 있는 것은 USDKRW 하나다 (fx-KR-USA 사이에서만 거래하므로). 다른
    통화쌍이 필요해지면 여기서 entity 를 넓힌다.
    """
    frame = store.get(FX, as_of=as_of, entity="FX:USDKRW", lookback=lookback)
    if frame.empty:
        return {"rate": None, "change": None, "sessions": [], "rates": []}
    ordered = frame.sort_values("valid_from")
    rates = ordered["rate"].astype(float)
    last = float(rates.iloc[-1])
    previous = float(rates.iloc[-2]) if len(rates) >= 2 else None
    return {
        "rate": last,
        "change": (last / previous - 1.0) if previous else None,
        "sessions": [pd.Timestamp(v).date().isoformat() for v in ordered["valid_from"]],
        "rates": [float(v) for v in rates],
    }


# -- 시가총액 상위 -------------------------------------------------------------


def _leaders(store: Store, *, as_of: datetime, market: str, limit: int) -> list[dict[str, Any]]:
    """시가총액 상위 ``limit`` 종목. `leaders()`(LEADER_ROWS) 와
    `market_treemap()`(TREEMAP_TOP_N) 이 같은 조인을 다른 상한으로 쓴다 —
    행수만 다르고 로직은 하나다.

    `market_stats` 는 종목마다 하루 두 행(market_cap·shares)이라 KR 만 해도
    수천 종목이다. 창을 짧게 열고(RECENT_DAYS) market 으로 SQL 단계에서
    거른다 — 안 그러면 국장·미장을 함께 스캔한다.
    """
    stats = store.get(
        MARKET_STATS,
        as_of=as_of,
        lookback=RECENT_DAYS,
        market=market,
        columns=["entity_id", "metric", "value", "valid_from"],
    )
    caps = stats[stats["metric"] == "market_cap"]
    if caps.empty:
        return []
    latest = caps.sort_values("valid_from").groupby("entity_id").tail(1)
    top = latest.sort_values("value", ascending=False).head(limit)
    entities = [str(e) for e in top["entity_id"]]

    names = _names(store, as_of=as_of, entities=entities)
    prices = store.get(
        PRICES,
        as_of=as_of,
        entity=entities,
        lookback=RECENT_DAYS,
        market=market,
        columns=["entity_id", "close", "valid_from"],
    )
    changes: dict[str, float | None] = {}
    if not prices.empty:
        for entity, group in prices.sort_values("valid_from").groupby("entity_id"):
            closes = group["close"].astype(float)
            closes = closes[closes > 0]
            if len(closes) >= 2:
                changes[str(entity)] = float(closes.iloc[-1]) / float(closes.iloc[-2]) - 1.0
            elif len(closes) == 1:
                # 종가는 있는데 직전이 없다 — 등락을 잴 수 없다. None 을 그대로
                # 둔다. 0 으로 채우면 "보합" 이라는 다른 사실이 된다.
                changes[str(entity)] = None

    rows: list[dict[str, Any]] = []
    for row in top.to_dict(orient="records"):
        entity = str(row["entity_id"])
        rows.append(
            {
                "entity_id": entity,
                "name": names.get(entity, entity),
                "market_cap": float(row["value"]),
                # 종가 자체가 없는 종목(오늘 거래정지 등)은 changes 에 키가
                # 없다 — .get() 이 None 을 돌려준다. 거래는 있었는데 직전이
                # 없는 경우와 같은 값(None)이지만, 둘 다 "등락을 못 잰다" 라는
                # 같은 사실이라 화면에서 구분할 필요가 없다.
                "change": changes.get(entity),
            }
        )
    return rows


def leaders(store: Store, *, as_of: datetime, market: str) -> list[dict[str, Any]]:
    """시가총액 상위 종목. 순위는 오늘 알 수 있는 마지막 시총 기준이다."""
    return _leaders(store, as_of=as_of, market=market, limit=LEADER_ROWS)


# -- 시가총액 트리맵 -------------------------------------------------------------


def market_treemap(store: Store, *, as_of: datetime) -> dict[str, Any]:
    """시가총액 트리맵 — finviz 식. 넓이는 시총, 색은 등락률.

    **KR·US 두 맵으로 완전히 나눈다** — 모듈 docstring "트리맵의 묶는 축"
    참고. 시장마다 상위 ``TREEMAP_TOP_N`` 종목만 담는다 — 전 종목을 그리면
    사각형이 안 보이는 크기가 된다. 화면이 "상위 N 만" 이라고 말해야 하므로
    ``top_n`` 을 응답에 함께 싣는다.

    US 는 지금 항상 빈 리스트다 — `market_stats` 에 US 상장주식수가 없어
    market_cap 자체가 없다(모듈 docstring "미장 시가총액 — 아직 못 그린다").
    """
    return {
        "KR": _leaders(store, as_of=as_of, market="KR", limit=TREEMAP_TOP_N),
        "US": _leaders(store, as_of=as_of, market="US", limit=TREEMAP_TOP_N),
        "top_n": TREEMAP_TOP_N,
    }


# -- 거시지표 ------------------------------------------------------------------


def macro_recent(store: Store, *, as_of: datetime) -> list[dict[str, Any]]:
    """최근 발표된 거시지표. **이미 발표된 것만** — 예정은 뉴스·일정 탭의 몫이다.

    지표당 최신 관측만 남긴다. 같은 발표가 여러 수집 회차에 걸쳐 여러 행으로
    쌓일 수 있어서다(자연키 = entity_id·scheduled_at, 정정본은 새 행).
    """
    frame = store.get(
        MACRO_RELEASES,
        as_of=as_of,
        lookback=60,
        columns=[
            "entity_id", "market", "indicator", "release_name", "scheduled_at",
            "actual", "previous", "unit", "status", "observed_at",
        ],
    )
    if frame.empty:
        return []
    released = frame[
        (frame["status"] == "released") & frame["actual"].notna() & (frame["scheduled_at"] <= as_of)
    ]
    if released.empty:
        return []
    # 같은 자연키(entity_id, scheduled_at)의 최신 관측만.
    deduped = released.sort_values("observed_at").groupby(
        ["entity_id", "scheduled_at"], as_index=False
    ).last()
    top = deduped.sort_values("scheduled_at", ascending=False).head(MACRO_ROWS)
    return [
        {
            "entity_id": str(row["entity_id"]),
            "market": str(row["market"]),
            "indicator": str(row["indicator"]),
            "release_name": str(row["release_name"]),
            "scheduled_at": row["scheduled_at"].isoformat(),
            "actual": float(row["actual"]) if pd.notna(row["actual"]) else None,
            "previous": float(row["previous"]) if pd.notna(row["previous"]) else None,
            "unit": str(row["unit"]) if pd.notna(row["unit"]) else "",
        }
        for row in top.to_dict(orient="records")
    ]


# -- 한 판 ---------------------------------------------------------------------


def payload(store: Store, *, as_of: datetime, lookback: int) -> dict[str, Any]:
    """마켓 탭 한 판. 국장·미장을 함께 본다 — 트레이딩 탭과 달리 market
    파라미터로 가르지 않는다. 이 화면의 질문은 "지금 시장이 어떤가" 이지
    "우리가 어느 시장에 있나" 가 아니다.
    """
    return {
        "indices": indices(store, as_of=as_of),
        "fx": fx(store, as_of=as_of, lookback=lookback),
        "leaders": {
            "KR": leaders(store, as_of=as_of, market="KR"),
            "US": leaders(store, as_of=as_of, market="US"),
        },
        "macro": macro_recent(store, as_of=as_of),
        "treemap": market_treemap(store, as_of=as_of),
    }
