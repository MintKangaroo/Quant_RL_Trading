"""뉴스·일정 탭 — **월별 일정**: 지표 발표(시각·예측치) + 실적 발표(확정/추정).

세 표를 합친다.

- ``macro_releases`` — FRED 공표 일정(미장). 시각은 ``scheduled_at``. H.15 금리처럼
  매일 나오는 것은 뺀다 — 달력을 채우기만 하고 아무도 안 본다.
- ``macro_consensus`` — ForexFactory 이번 주 일정(예측치·중요도). 같은 지표가
  FRED 일정에도 있으면 **예측치가 있는 쪽**만 남긴다.
- ``earnings_calendar`` — 실적 발표. 미장은 확정(pre/post), 국장은 작년 공시일
  기준 추정(estimate). 추정은 화면에 "예상" 을 붙인다.

전부 ``as_of`` 게이트를 지난 행이다 — 미래 시각의 행이라도 **그때 알 수 있었던**
일정만 나온다(불변식 9). 시각은 서울 시간으로 내보낸다.
"""
from __future__ import annotations

import calendar as _calendar
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from quant_rl_trading.store import Store

SEOUL = ZoneInfo("Asia/Seoul")
#: 매일 나오는 시계열은 일정이 아니다.
DAILY_NOISE = frozenset({"FED_FUNDS"})
IMPACT_RANK = {"High": 3, "Medium": 2, "Low": 1}
TIMING_LABEL = {"pre": "장 전", "post": "장 후", "unknown": "시각 미정", "estimate": "예상"}


def _month_bounds(month: str) -> tuple[datetime, datetime]:
    year, mon = int(month[:4]), int(month[5:7])
    first = datetime(year, mon, 1, tzinfo=SEOUL)
    last = datetime(year, mon, _calendar.monthrange(year, mon)[1], 23, 59, 59, tzinfo=SEOUL)
    return first, last


def _lookback_for(as_of: datetime, first: datetime) -> int:
    """월 시작보다 앞서 관측된 일정도 보려면 창이 그만큼 넓어야 한다."""
    return max(120, (as_of - first).days + 120)


def month_schedule(store: Store, *, as_of: datetime, month: str | None = None) -> dict[str, Any]:
    month = month or as_of.astimezone(SEOUL).strftime("%Y-%m")
    first, last = _month_bounds(month)
    lookback = _lookback_for(as_of, first)
    items: list[dict[str, Any]] = []

    consensus = store.get("macro_consensus", as_of=as_of, lookback=lookback)
    seen: set[tuple[str, date]] = set()
    if not consensus.empty:
        when = consensus["valid_from"].dt.tz_convert(SEOUL)
        rows = consensus[(when >= first) & (when <= last)]
        for row in rows.to_dict(orient="records"):
            at = pd.Timestamp(row["valid_from"]).tz_convert(SEOUL)
            key = (str(row["entity_id"]), at.date())
            seen.add(key)
            impact = str(row.get("impact") or "")
            items.append({
                "date": at.date().isoformat(), "time": at.strftime("%H:%M"),
                "kind": "macro", "market": str(row["market"]), "label": str(row["title"]),
                "detail": " · ".join(
                    part for part in (
                        f"예측 {row['forecast']}" if row.get("forecast") else "",
                        f"이전 {row['previous']}" if row.get("previous") else "",
                        f"실제 {row['actual']}" if row.get("actual") else "",
                    ) if part
                ),
                "importance": IMPACT_RANK.get(impact, 1), "impact": impact,
                "entity_id": str(row["entity_id"]),
            })

    releases = store.get("macro_releases", as_of=as_of, lookback=lookback)
    if not releases.empty:
        when = releases["scheduled_at"].dt.tz_convert(SEOUL)
        rows = releases[(when >= first) & (when <= last) & ~releases["indicator"].isin(DAILY_NOISE)]
        rows = rows.drop_duplicates(subset=["entity_id", "scheduled_at"])
        for row in rows.to_dict(orient="records"):
            at = pd.Timestamp(row["scheduled_at"]).tz_convert(SEOUL)
            if (str(row["entity_id"]), at.date()) in seen:
                continue  # ForexFactory 쪽에 예측치와 함께 이미 있다
            items.append({
                "date": at.date().isoformat(), "time": at.strftime("%H:%M"),
                "kind": "macro", "market": str(row["market"]),
                "label": str(row.get("release_name") or row["indicator"]),
                "detail": f"{row['indicator']}" + (f" · 이전 {row['previous']}" if pd.notna(row.get("previous")) else ""),
                "importance": 2, "impact": "", "entity_id": str(row["entity_id"]),
            })

    earnings = store.get("earnings_calendar", as_of=as_of, lookback=lookback)
    if not earnings.empty:
        when = earnings["valid_from"].dt.tz_convert(SEOUL)
        rows = earnings[(when >= first) & (when <= last)]
        # 같은 종목이 다른 날로 옮겨 적혔으면 **나중에 관측된 것**이 지금의 일정이다.
        rows = rows.sort_values("observed_at").drop_duplicates(subset=["entity_id"], keep="last")
        for row in rows.to_dict(orient="records"):
            at = pd.Timestamp(row["valid_from"]).tz_convert(SEOUL)
            timing = str(row.get("timing") or "unknown")
            estimated = str(row.get("status") or "") == "estimated"
            detail = TIMING_LABEL.get(timing, timing)
            if row.get("eps_forecast"):
                detail += f" · EPS 예측 {row['eps_forecast']}"
            if estimated and row.get("fiscal_quarter"):
                detail += f" · {row['fiscal_quarter']}"
            items.append({
                "date": at.date().isoformat(), "time": "" if estimated else at.strftime("%H:%M"),
                "kind": "earnings", "market": str(row["market"]),
                "label": f"{row['name']} 실적" + (" (예상)" if estimated else ""),
                "detail": detail,
                "importance": 3 if float(row.get("market_cap") or 0) >= 1e11 else 2,
                "impact": "", "entity_id": str(row["entity_id"]), "estimated": estimated,
            })

    items.sort(key=lambda item: (item["date"], item["time"] or "99:99", -item["importance"], item["label"]))
    days: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        days.setdefault(item["date"], []).append(item)
    return {
        "month": month,
        "days": days,
        "counts": {
            "macro": sum(1 for i in items if i["kind"] == "macro"),
            "earnings": sum(1 for i in items if i["kind"] == "earnings"),
            "estimated": sum(1 for i in items if i.get("estimated")),
        },
        "prev": (first - timedelta(days=1)).strftime("%Y-%m"),
        "next": (last + timedelta(days=1)).strftime("%Y-%m"),
    }


def upcoming(store: Store, *, as_of: datetime, days: int = 30) -> dict[str, Any]:
    """오늘부터 ``days`` 일 — 휴대폰 목록용. 달 경계를 넘긴다.

    월말에 "이달" 만 보여 주면 목록이 비어 보인다(2026-08-30 일요일 실측: 8월 남은
    일정 0건). 달력이 아니라 "앞으로 뭐가 있나" 가 질문이므로 달을 넘겨 이어 붙인다.
    """
    today = as_of.astimezone(SEOUL).date()
    last = today + timedelta(days=max(1, days))
    months: list[str] = []
    cursor = today.replace(day=1)
    while cursor <= last:
        months.append(cursor.strftime("%Y-%m"))
        cursor = (cursor + timedelta(days=32)).replace(day=1)
    merged: dict[str, list[dict[str, Any]]] = {}
    for month in months:
        for day, items in month_schedule(store, as_of=as_of, month=month)["days"].items():
            if today.isoformat() <= day <= last.isoformat():
                merged[day] = items
    items = [i for day in sorted(merged) for i in merged[day]]
    return {
        "from": today.isoformat(), "to": last.isoformat(), "days": dict(sorted(merged.items())),
        "counts": {
            "macro": sum(1 for i in items if i["kind"] == "macro"),
            "earnings": sum(1 for i in items if i["kind"] == "earnings"),
            "estimated": sum(1 for i in items if i.get("estimated")),
        },
    }
