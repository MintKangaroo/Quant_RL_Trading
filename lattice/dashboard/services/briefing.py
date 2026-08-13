"""뉴스·일정 집계.

이 화면의 질문은 "**지금 무슨 일이 벌어지고 있고, 다음에 무엇이 오는가**" 다.
숫자 화면(Data Quality·Agent Health)과 달리 사람이 읽는 화면이라, 정확성보다
**시점 정합성**이 더 중요하다 — 어제 화면을 다시 열었을 때 어제까지의 뉴스만
보여야 한다 (불변식 9).

모든 조회는 ``store.get(table, as_of=...)`` 를 경유한다 (불변식 1).

## 다가오는 일정을 찾는 법

``macro_releases`` 의 ``valid_from`` 은 **우리가 안 시각**이고 발표 시각은
``scheduled_at`` 이다. 그래서 창은 과거로 열어 최근에 알게 된 일정을 가져온
뒤, ``scheduled_at`` 으로 과거/미래를 가른다. 발표 시각을 valid_from 에 넣었다면
미래 일정이 어느 창에도 안 걸렸을 것이다 (macro_source 모듈 docstring).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from lattice.store import Store

DOCUMENTS = "documents"
MACRO_RELEASES = "macro_releases"

#: 일정을 찾을 때 되돌아볼 날수. 발표 일정은 몇 주 전에 공표되므로 뉴스보다
#: 넉넉히 열어야 다음 달 일정이 잡힌다.
SCHEDULE_LOOKBACK_DAYS = 60


@dataclass(frozen=True)
class Briefing:
    news: list[dict[str, Any]]
    released: list[dict[str, Any]]
    upcoming: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {"news": self.news, "released": self.released, "upcoming": self.upcoming}


def _clean(value: Any) -> Any:
    """NaN 을 None 으로. JSON 에 NaN 을 넣으면 표준이 아니라 파서가 갈린다."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    return value


def latest_news(
    store: Store, *, as_of: datetime, lookback: int, limit: int = 40
) -> list[dict[str, Any]]:
    """최근 뉴스·공시. 최신순.

    같은 기사가 여러 종목에 붙을 수 있다 (검색어가 달라도 같은 URL). 화면에
    같은 제목이 세 번 뜨면 읽는 사람이 셋을 다른 사건으로 읽으므로 ``doc_id``
    로 접는다 — 대신 어느 종목들에 걸렸는지는 남긴다.
    """
    frame = store.get(DOCUMENTS, as_of=as_of, lookback=lookback)
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
        if len(seen) >= limit:
            continue
        seen[doc_id] = {
            "doc_id": doc_id,
            "doc_type": str(row.get("doc_type") or ""),
            "title": str(row.get("title") or ""),
            "filer": str(row.get("filer") or ""),
            "url": str(row.get("url") or ""),
            "published_at": row["valid_from"].isoformat(),
            "observed_at": row["observed_at"].isoformat(),
            "entities": [entity] if entity else [],
        }
    return list(seen.values())


def macro_calendar(
    store: Store, *, as_of: datetime, upcoming_limit: int = 20, released_limit: int = 20
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(발표 완료, 다가오는 일정). 완료는 최신순, 예정은 가까운 순."""
    frame = store.get(MACRO_RELEASES, as_of=as_of, lookback=SCHEDULE_LOOKBACK_DAYS)
    if frame.empty:
        return [], []

    # 같은 발표(자연키)의 최신 관측만 남긴다. 게이트가 정정본을 이미 골랐지만,
    # 여러 수집 회차가 같은 발표를 각각 남기므로 여기서 한 번 더 접는다.
    frame = frame.sort_values("observed_at").groupby(
        ["entity_id", "scheduled_at"], as_index=False
    ).last()

    def render(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "entity_id": str(row["entity_id"]),
            "market": str(row.get("market") or ""),
            "indicator": str(row.get("indicator") or ""),
            "release_name": str(row.get("release_name") or ""),
            "scheduled_at": row["scheduled_at"].isoformat(),
            "actual": _clean(row.get("actual")),
            "previous": _clean(row.get("previous")),
            "unit": str(row.get("unit") or ""),
            "status": str(row.get("status") or ""),
        }

    records = [render(row) for row in frame.to_dict(orient="records")]
    boundary = as_of

    # **지표당 한 줄이다.** 상위 N건으로 자르면 발표가 잦은 시장이 화면을
    # 독차지한다 — 실제로 미장 15건이 국장 4건을 밀어내 국장이 1건만 남았다.
    # 화면의 질문은 "최근 무엇이 발표됐나" 가 아니라 "지금 각 지표가 얼마인가" 다.
    def newest_per_indicator(
        items: list[dict[str, Any]], *, reverse: bool
    ) -> list[dict[str, Any]]:
        best: dict[str, dict[str, Any]] = {}
        for item in sorted(items, key=lambda r: r["scheduled_at"], reverse=reverse):
            best.setdefault(item["entity_id"], item)
        return sorted(best.values(), key=lambda r: r["scheduled_at"], reverse=reverse)

    released = newest_per_indicator(
        [r for r in records if datetime.fromisoformat(r["scheduled_at"]) <= boundary],
        reverse=True,
    )[:released_limit]
    upcoming = newest_per_indicator(
        [r for r in records if datetime.fromisoformat(r["scheduled_at"]) > boundary],
        reverse=False,
    )[:upcoming_limit]
    return released, upcoming


def collect_briefing(store: Store, *, as_of: datetime, lookback: int) -> Briefing:
    released, upcoming = macro_calendar(store, as_of=as_of)
    return Briefing(
        news=latest_news(store, as_of=as_of, lookback=lookback),
        released=released,
        upcoming=upcoming,
    )


def next_release(upcoming: list[dict[str, Any]], *, as_of: datetime) -> dict[str, Any] | None:
    """가장 가까운 발표와 남은 시간. 헤더 한 줄에 쓴다."""
    if not upcoming:
        return None
    head = upcoming[0]
    delta: timedelta = datetime.fromisoformat(head["scheduled_at"]) - as_of
    return {**head, "in_seconds": int(delta.total_seconds())}
