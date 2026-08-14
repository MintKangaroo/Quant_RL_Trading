"""중요 시황 탭 집계 — **지금 시장에서 우리와 관련해 무슨 일이 벌어졌나.**

뉴스·일정 탭(`briefing.py`)이 "세상에서 무슨 일이 있었나" 를 넓게 훑는다면,
이 화면은 **판정으로 이어진 것**만 좁혀 본다: News Analyst 가 실제로 매수를
막은 건(verdicts), 그럴 만한 공시(documents 중 중요도 높은 것), 그리고
파이프라인이 남긴 판단 이벤트(events).

## "중요도" 를 어떻게 가르나

`documents` 테이블에는 severity 도 importance 도 없다. 대신 `doc_type` 분포를
보면 답이 나온다 — dart 공시 11,414건 중 "ownership"(3,718)·"other"(4,908)가
부피의 대부분이고, 대부분 일상적 지분 변동·기타 신고다. `IMPORTANT_DOC_TYPES`
는 그 부피에 덮이지 않는, 실제로 주가에 영향을 주는 유형만 고른 것이다.
숫자가 아니라 카테고리 선정이라 store.config 임계치가 아니다 — 다른 화면들의
`ORDER_ROWS`·`WATCHLIST_ROWS` 처럼 화면 상수다.

## 없는 것은 없다고 말한다

- `events` 는 이 창고에서 현재 0행이다 (replay/live 파이프라인이 아직 이
  창고에 이벤트를 안 남겼다). 지어내지 않고 빈 패널로 둔다 — 채워지면
  관측→Signal→Selector→Allocator→주문 단계가 그대로 화면에 뜬다.
- 트리맵에서 등락률을 못 잰 종목(그날 거래가 없어 직전 종가가 없는 등)은
  ``change`` 를 ``null`` 로 둔다. 화면이 그걸 회색으로 그린다 — 0% 로 채우면
  "보합" 이라는 다른 사실이 된다.

## 트리맵의 묶는 축 — 왜 업종이 아니라 시장인가

finviz 식 맵은 보통 섹터로 묶는다. 이 창고에도 `sectors` 테이블이 있지만
**업종 분류가 아니다.** `SECT_TP_NM`(KRX Open API 일별매매) 은 소속부 —
KOSDAQ 만 우량기업부·벤처기업부 등으로 갈리고, **KOSPI 942 종목은 전부
빈 문자열(미상)** 이다 (`store/tables.py` sectors TableSpec 참고). 이 축으로
묶으면 코스피가 통째로 "미상" 한 덩어리가 되어, 없는 업종 분류를 있는 것처럼
그리게 된다. 그래서 확실히 아는 축 — **시장(KR/US)** — 으로만 묶는다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from quant_rl_trading.store import Store

DOCUMENTS = "documents"
VERDICTS = "verdicts"
EVENTS = "events"
UNIVERSE = "universe"
MARKET_STATS = "market_stats"
PRICES = "prices"

#: dart 공시 11,414건의 분포를 보고 골랐다(모듈 docstring). "ownership"·
#: "other"·"earnings" 는 부피가 커서 뺐다 — 통상적 신고가 대부분이다.
IMPORTANT_DOC_TYPES = frozenset(
    {"distress", "dilution", "split", "buyback", "contract", "dividend", "news"}
)

DOCUMENT_ROWS = 30
VERDICT_ROWS = 40
EVENT_ROWS = 30

#: 트리맵 시장별 상위 종목 수. **화면 상수다 — store.config 임계치가 아니다**
#: (다른 화면의 WATCHLIST_ROWS·LEADER_ROWS 와 같은 이유, 모듈 docstring).
#: 2,871개 KR 종목을 다 그리면 사각형이 1픽셀이 되어 브라우저가 느려진다.
#: 60은 finviz 가 한 섹터 타일에 보통 담는 규모다.
TREEMAP_TOP_N = 60

#: 시총·등락 계산에 쓰는 창. 화면의 lookback(타임머신 창)과 다르다 — "어제
#: 대비" 하나만 있으면 된다. market.py 의 RECENT_DAYS 와 같은 값이다.
RECENT_DAYS = 5


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


# -- 판정 (verdicts) -----------------------------------------------------------


def verdicts(store: Store, *, as_of: datetime, lookback: int) -> list[dict[str, Any]]:
    """News·SNS 판정. **매수 금지만 가능하다** — decision 은 항상 block 이다
    (CLAUDE.md 금지 사항: News·SNS Analyst 에 매도 권한 없음).

    ``active`` 는 ``expires_at > as_of`` 다. 만료된 차단도 표에 남긴다 — 방금
    풀린 것과 여태 걸려 있는 것을 구분해야 "지금 왜 이 종목이 안 사지는지" 를
    설명할 수 있다.
    """
    frame = store.get(VERDICTS, as_of=as_of, lookback=lookback)
    if frame.empty:
        return []
    names = _names(store, as_of=as_of, entities=sorted(set(frame["entity_id"])))
    ordered = frame.sort_values(["valid_from", "observed_at"], ascending=False)
    rows: list[dict[str, Any]] = []
    for row in ordered.head(VERDICT_ROWS).to_dict(orient="records"):
        entity = str(row["entity_id"])
        expires = pd.Timestamp(row["expires_at"])
        rows.append(
            {
                "entity_id": entity,
                "name": names.get(entity, entity),
                "analyst": str(row["analyst"]),
                "decision": str(row["decision"]),
                "severity": float(row["severity"]) if pd.notna(row["severity"]) else None,
                "category": str(row["category"]),
                "reason": str(row["reason"]),
                "valid_from": pd.Timestamp(row["valid_from"]).isoformat(),
                "expires_at": expires.isoformat(),
                "active": bool(expires > as_of),
            }
        )
    # 살아있는 차단을 위로, 그 안에서는 심각도 순으로 — 지금 매수를 막고
    # 있는 것이 먼저 보여야 한다.
    rows.sort(key=lambda r: (not r["active"], -(r["severity"] or 0.0)))
    return rows


# -- 공시 · 뉴스 ----------------------------------------------------------------


def important_documents(store: Store, *, as_of: datetime, lookback: int) -> list[dict[str, Any]]:
    """중요도 높은 공시·뉴스. **doc_type 이 부피가 큰 통상 신고를 뺀다**
    (IMPORTANT_DOC_TYPES, 모듈 docstring).

    같은 문서가 여러 종목에 걸리면(검색어가 달라도 같은 doc_id) 한 줄로 접고
    걸린 종목을 함께 남긴다 — briefing.latest_news 와 같은 규칙이다.
    """
    frame = store.get(DOCUMENTS, as_of=as_of, lookback=lookback)
    if frame.empty:
        return []
    frame = frame[frame["doc_type"].isin(IMPORTANT_DOC_TYPES)]
    if frame.empty:
        return []
    frame = frame.sort_values("valid_from", ascending=False)

    seen: dict[str, dict[str, Any]] = {}
    for row in frame.to_dict(orient="records"):
        doc_id = str(row.get("doc_id") or "")
        if not doc_id:
            continue
        entity = str(row.get("entity_id") or "")
        if doc_id in seen:
            entities = seen[doc_id]["entities"]
            if entity and entity not in entities:
                entities.append(entity)
            continue
        if len(seen) >= DOCUMENT_ROWS:
            continue
        seen[doc_id] = {
            "doc_id": doc_id,
            "doc_type": str(row.get("doc_type") or ""),
            "title": str(row.get("title") or ""),
            "filer": str(row.get("filer") or ""),
            "url": str(row.get("url") or ""),
            "published_at": row["valid_from"].isoformat(),
            "entities": [entity] if entity else [],
        }

    names = _names(
        store,
        as_of=as_of,
        entities=sorted({e for item in seen.values() for e in item["entities"]}),
    )
    out = list(seen.values())
    for item in out:
        item["entity_names"] = [names.get(e, e) for e in item["entities"]]
    return out


# -- 이벤트 로그 ----------------------------------------------------------------


def system_events(store: Store, *, as_of: datetime, lookback: int) -> list[dict[str, Any]]:
    """파이프라인 결정 이벤트. 현재 창고에 0행일 수 있다 — **그대로 빈
    리스트를 돌려준다.** replay/live 가 events 를 남기기 시작하면 이 화면이
    자동으로 채워진다 (replay/events.py 참고).
    """
    frame = store.get(EVENTS, as_of=as_of, lookback=lookback)
    if frame.empty:
        return []
    ordered = frame.sort_values(["valid_from", "seq"], ascending=False)
    return [
        {
            "run_id": str(row["entity_id"]),
            "seq": int(row["seq"]),
            "stage": str(row["stage"]),
            "actor": str(row["actor"]),
            "ts_sim": pd.Timestamp(row["valid_from"]).isoformat(),
            "ts_wall": pd.Timestamp(row["observed_at"]).isoformat(),
            "payload": str(row["payload"]),
        }
        for row in ordered.head(EVENT_ROWS).to_dict(orient="records")
    ]


# -- 트리맵 --------------------------------------------------------------------


def _market_cap_leaders(
    store: Store, *, as_of: datetime, market: str, limit: int
) -> list[dict[str, Any]]:
    """시가총액 상위 N. market.py 의 leaders() 와 같은 조인(market_stats ×
    universe × prices)이지만, 그 파일은 다른 에이전트가 동시에 고치고 있어
    임포트로 묶지 않고 여기서 다시 짠다 — 서로 건드리지 않아도 되게.
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


def market_treemap(store: Store, *, as_of: datetime) -> dict[str, Any]:
    """시가총액 트리맵 — finviz 식. 넓이는 시총, 색은 등락률.

    묶는 축은 **시장(KR/US)** 이다. 이유는 모듈 docstring "트리맵의 묶는
    축" 참고 — sectors 테이블이 업종이 아니라 KOSDAQ 소속부이고 KOSPI 는
    전 종목 미상이라, 업종으로 묶으면 코스피가 통째로 "미상" 덩어리가 된다.

    시장마다 상위 ``TREEMAP_TOP_N`` 종목만 담는다 — 전 종목을 그리면 사각형이
    안 보이는 크기가 된다. 화면이 "상위 N 만" 이라고 말해야 하므로 ``top_n``
    을 응답에 함께 싣는다.
    """
    groups: list[dict[str, Any]] = []
    for market in ("KR", "US"):
        rows = _market_cap_leaders(store, as_of=as_of, market=market, limit=TREEMAP_TOP_N)
        if rows:
            groups.append({"market": market, "children": rows})
    return {"groups": groups, "top_n": TREEMAP_TOP_N}


# -- 한 판 ---------------------------------------------------------------------


def payload(store: Store, *, as_of: datetime, lookback: int) -> dict[str, Any]:
    verdict_rows = verdicts(store, as_of=as_of, lookback=lookback)
    return {
        "verdicts": verdict_rows,
        "active_verdicts": sum(1 for row in verdict_rows if row["active"]),
        "documents": important_documents(store, as_of=as_of, lookback=lookback),
        "events": system_events(store, as_of=as_of, lookback=lookback),
        "treemap": market_treemap(store, as_of=as_of),
    }
