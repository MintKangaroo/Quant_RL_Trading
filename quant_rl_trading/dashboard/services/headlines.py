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

#: dart 공시 11,414건의 분포를 보고 골랐다(모듈 docstring). "ownership"·
#: "other"·"earnings" 는 부피가 커서 뺐다 — 통상적 신고가 대부분이다.
IMPORTANT_DOC_TYPES = frozenset(
    {"distress", "dilution", "split", "buyback", "contract", "dividend", "news"}
)

DOCUMENT_ROWS = 30
VERDICT_ROWS = 40
EVENT_ROWS = 30


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


# -- 한 판 ---------------------------------------------------------------------


def payload(store: Store, *, as_of: datetime, lookback: int) -> dict[str, Any]:
    verdict_rows = verdicts(store, as_of=as_of, lookback=lookback)
    return {
        "verdicts": verdict_rows,
        "active_verdicts": sum(1 for row in verdict_rows if row["active"]),
        "documents": important_documents(store, as_of=as_of, lookback=lookback),
        "events": system_events(store, as_of=as_of, lookback=lookback),
    }
