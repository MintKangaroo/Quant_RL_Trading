"""AI 리뷰 탭 집계 — LLM 이 실제로 무엇을 했는지.

세 테이블을 본다.

- ``agent_cache`` — 실제로 부른 LLM 호출의 기록. 캐시 적중은 새 행을 만들지
  않으므로(``replay/cache.py`` 의 ``get`` 은 조회만 하고 ``put`` 만 append 한다),
  이 표의 행 수가 곧 "다시 계산한 횟수" 다.
- ``verdicts`` — News·SNS 의 매수 거부 판정. 매도 권한은 없다 (불변식).
- ``documents`` — 판정의 입력이 된 공시·뉴스.

**비용은 잴 수 없다.** ``agent_cache.output`` 에는 모델 응답만 있고 토큰
사용량은 기록되지 않는다 (``replay/cache.py`` 스키마 참고). 없는 값을 0 으로
채우지 않고 그대로 비워 둔다 — 0 은 "쟀는데 0" 이라는 다른 사실이다.

**``verdicts`` 가 거의 비어 있는 것은 고장이 아니다.** `docs/milestones.md`
M2 가 못 박았다 — 뉴스 40건 → 패턴 4건 → LLM 4건 전부 기각, 0건은 "거부할
사유가 없었다" 는 정답이다. 화면이 그 구분을 말해야 한다.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pandas as pd

from quant_rl_trading.analysts import scorecard
from quant_rl_trading.store import Store

CACHE = "agent_cache"
VERDICTS = "verdicts"
DOCUMENTS = "documents"

#: 최근 목록 패널이 한 화면에 들어가려면 길이를 자른다. 패널 안 스크롤은
#: 되지만(dashboard.md §2) 무한정 늘리면 창고 조회가 무거워진다.
RECENT_LIMIT = 40


def _summarize_output(raw: Any) -> str:
    """``output`` JSON 에서 사람이 읽을 한 줄.

    에이전트마다 스키마가 다르다 — news-screen 은 ``{keep, reason}``,
    macro-brief 는 ``{tone, headline, reading}`` (분석기 소스 참고). 아는
    필드만 조심스럽게 뽑고, 모르면 원문을 자른다. 모르는 스키마를 억지로
    파싱해 잘못된 요약을 만드는 것보다 원문 조각이 정직하다.
    """
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return str(raw)[:100]
    if not isinstance(payload, dict):
        return str(payload)[:100]
    for key in ("headline", "reading", "reason"):
        value = payload.get(key)
        if value:
            return str(value)[:140]
    if "keep" in payload:
        keep = payload.get("keep")
        reason = payload.get("reason") or ""
        return f"{'통과' if keep else '기각'} — {reason}"[:140]
    return json.dumps(payload, ensure_ascii=False)[:140]


def agent_activity(store: Store, *, as_of: datetime, lookback: int) -> dict[str, Any]:
    """에이전트·버전별 호출 현황. ``비용`` 은 없다 — 잴 방법이 없다."""
    frame = store.get(CACHE, as_of=as_of, lookback=lookback)
    if frame.empty:
        return {"agents": [], "total": 0}

    rows: list[dict[str, Any]] = []
    for (agent, version), group in frame.groupby(["agent", "agent_version"]):
        rows.append(
            {
                "agent": str(agent),
                "version": str(version),
                "calls": len(group),
                "entities": int(group["entity_id"].nunique()),
                "first_computed_at": group["computed_at"].min().isoformat(),
                "last_computed_at": group["computed_at"].max().isoformat(),
            }
        )
    return {"agents": sorted(rows, key=lambda item: str(item["agent"])), "total": len(frame)}


def recent_calls(store: Store, *, as_of: datetime, lookback: int) -> list[dict[str, Any]]:
    """최근 호출 목록. 캐시에 새로 쌓인 순서(``computed_at``)로 본다."""
    frame = store.get(CACHE, as_of=as_of, lookback=lookback)
    if frame.empty:
        return []
    recent = frame.sort_values("computed_at", ascending=False).head(RECENT_LIMIT)
    return [
        {
            "entity_id": str(row["entity_id"]),
            "agent": str(row["agent"]),
            "version": str(row["agent_version"]),
            "as_of": row["valid_from"].isoformat(),
            "computed_at": row["computed_at"].isoformat(),
            "summary": _summarize_output(row["output"]),
        }
        for row in recent.to_dict(orient="records")
    ]


def verdict_activity(store: Store, *, as_of: datetime, lookback: int) -> dict[str, Any]:
    """News·SNS 거부 판정. ``scorecard`` 로 사후 성적까지 낸다."""
    frame = store.get(VERDICTS, as_of=as_of, lookback=lookback)
    card = scorecard.evaluate_blocks(store, as_of=as_of, lookback=lookback)
    if frame.empty:
        return {"total": 0, "blocked": 0, "by_analyst": [], "recent": [], "scorecard": card}

    blocked = frame[frame["decision"] == "block"]
    recent = frame.sort_values("valid_from", ascending=False).head(RECENT_LIMIT)
    return {
        "total": len(frame),
        "blocked": len(blocked),
        "by_analyst": [
            {"analyst": str(name), "count": int(count)}
            for name, count in blocked["analyst"].value_counts().items()
        ],
        "recent": [
            {
                "entity_id": str(row["entity_id"]),
                "analyst": str(row["analyst"]),
                "decision": str(row["decision"]),
                "category": row.get("category"),
                "reason": row.get("reason"),
                "at": row["valid_from"].isoformat(),
                "expires_at": (
                    row["expires_at"].isoformat() if pd.notna(row.get("expires_at")) else None
                ),
            }
            for row in recent.to_dict(orient="records")
        ],
        "scorecard": card,
    }


def document_activity(store: Store, *, as_of: datetime, lookback: int) -> dict[str, Any]:
    """판정의 입력이 된 공시·뉴스."""
    frame = store.get(DOCUMENTS, as_of=as_of, lookback=lookback)
    if frame.empty:
        return {"total": 0, "by_type": [], "recent": []}

    recent = frame.sort_values("valid_from", ascending=False).head(RECENT_LIMIT)
    return {
        "total": len(frame),
        "by_type": [
            {"doc_type": str(name), "count": int(count)}
            for name, count in frame["doc_type"].value_counts().items()
        ],
        "recent": [
            {
                "entity_id": str(row["entity_id"]),
                "doc_type": str(row["doc_type"]),
                "title": str(row["title"]),
                "filer": row.get("filer") or None,
                "at": row["valid_from"].isoformat(),
            }
            for row in recent.to_dict(orient="records")
        ],
    }


def summary(store: Store, *, as_of: datetime, lookback: int) -> dict[str, Any]:
    activity = agent_activity(store, as_of=as_of, lookback=lookback)
    verdicts = verdict_activity(store, as_of=as_of, lookback=lookback)
    documents = document_activity(store, as_of=as_of, lookback=lookback)
    return {
        "calls": activity["total"],
        "agents": len(activity["agents"]),
        "blocked": verdicts["blocked"],
        "verdicts_total": verdicts["total"],
        "documents": documents["total"],
        "warnings": _warnings(activity),
    }


def _warnings(activity: dict[str, Any]) -> list[str]:
    found: list[str] = []
    if not activity["agents"]:
        found.append("LLM 호출 기록이 없다. news_screen · macro_brief 가 아직 안 돌았을 수 있다")
    return found


__all__ = [
    "agent_activity",
    "document_activity",
    "recent_calls",
    "summary",
    "verdict_activity",
]
