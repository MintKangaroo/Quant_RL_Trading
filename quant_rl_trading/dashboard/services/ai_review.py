"""AI 리뷰 탭 집계 — LLM 이 실제로 무엇을 했고 얼마가 들었는지.

네 테이블을 본다.

- ``agent_cache`` — 실제로 부른 LLM 호출의 기록. 캐시 적중은 새 행을 만들지
  않으므로(``replay/cache.py`` 의 ``get`` 은 조회만 하고 ``put`` 만 append 한다),
  이 표의 행 수가 곧 "다시 계산한 횟수" 다.
- ``llm_usage`` — 호출 하나(HTTP 왕복)당 실제 토큰 사용량. 비용은 여기 토큰과
  ``config.llm.pricing`` 단가로 계산한다(``store/tables.py`` 참고 — 단가는
  창고가 아니라 store.config 소관, 불변식 10). **이 표가 생기기 전 호출은
  기록이 없다.** 0원이 아니라 "몰랐다" 다 — ``cost_activity`` 가 창(lookback)
  안 첫 기록 시각을 ``since`` 로 돌려주고, 화면이 그걸 명시해야 한다.
- ``verdicts`` — News·SNS 의 매수 거부 판정. 매도 권한은 없다 (불변식).
- ``documents`` — 판정의 입력이 된 공시·뉴스.

**``verdicts`` 가 거의 비어 있는 것은 고장이 아니다.** `docs/milestones.md`
M2 가 못 박았다 — 뉴스 40건 → 패턴 4건 → LLM 4건 전부 기각, 0건은 "거부할
사유가 없었다" 는 정답이다. 화면이 그 구분을 말해야 한다.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from quant_rl_trading.analysts import scorecard
from quant_rl_trading.store import ConfigNotFound, Store

CACHE = "agent_cache"
VERDICTS = "verdicts"
DOCUMENTS = "documents"
USAGE = "llm_usage"

#: llm_usage 의 토큰 필드 → config 의 단가 필드. 순서가 비용 계산의 근거다.
_TOKEN_PRICE_FIELDS = (
    ("input_tokens", "input_per_mtok"),
    ("output_tokens", "output_per_mtok"),
    ("cache_creation_input_tokens", "cache_write_per_mtok"),
    ("cache_read_input_tokens", "cache_read_per_mtok"),
)

#: 최근 목록 패널이 한 화면에 들어가려면 길이를 자른다. 패널 안 스크롤은
#: 되지만(dashboard.md §2) 무한정 늘리면 창고 조회가 무거워진다.
RECENT_LIMIT = 40

#: ``entity_id`` 에 시장 접두어가 없을 때의 이름. 빼거나 KR 에 몰아넣지
#: 않는다 — 접두어 없는 행이 있다는 것 자체가 알아야 할 사실이다.
UNKNOWN_MARKET = "기타"


def _market_of(entity_id: Any) -> str:
    """``KR:005930`` · ``US:CPI`` 에서 시장을 뗀다.

    합계 하나로 뭉개면 국장 4건이 미장 300건에 묻힌다 — 뉴스·일정 탭에서
    실제로 겪은 일이다 (dashboard.md §8-2). 여기서도 같은 이유로 나눈다.
    """
    text = str(entity_id)
    head, sep, _ = text.partition(":")
    return head if sep and head else UNKNOWN_MARKET


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
        return {"agents": [], "total": 0, "by_market": []}

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
    market = frame["entity_id"].map(_market_of)
    by_market = [
        {
            "market": str(name),
            "calls": len(group),
            "entities": int(frame.loc[group.index, "entity_id"].nunique()),
        }
        for name, group in market.groupby(market)
    ]
    return {
        "agents": sorted(rows, key=lambda item: str(item["agent"])),
        "total": len(frame),
        "by_market": sorted(by_market, key=lambda item: -int(item["calls"])),
    }


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
        return {
            "total": 0, "blocked": 0, "by_analyst": [], "by_market": [],
            "recent": [], "scorecard": card,
        }

    blocked = frame[frame["decision"] == "block"]
    recent = frame.sort_values("valid_from", ascending=False).head(RECENT_LIMIT)
    market = frame["entity_id"].map(_market_of)
    blocked_market = market.loc[blocked.index]
    return {
        "total": len(frame),
        "blocked": len(blocked),
        "by_market": sorted(
            (
                {
                    "market": str(name),
                    "total": int(count),
                    "blocked": int((blocked_market == name).sum()),
                }
                for name, count in market.value_counts().items()
            ),
            key=lambda item: -int(item["total"]),
        ),
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
        return {"total": 0, "by_type": [], "by_market": [], "recent": []}

    recent = frame.sort_values("valid_from", ascending=False).head(RECENT_LIMIT)
    market = frame["entity_id"].map(_market_of)
    return {
        "total": len(frame),
        "by_market": [
            {"market": str(name), "total": int(count)}
            for name, count in market.value_counts().items()
        ],
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


def _pricing_table(store: Store, *, as_of: datetime) -> dict[str, dict[str, float]]:
    """``config.llm.pricing`` 을 ``{모델: {필드: 단가}}`` 로 되접는다.

    ``store.config()`` 는 이름에 점이 있으면(``"llm.pricing"``) 무조건 값
    하나로 취급한다 — ``"reward.w_free"`` 처럼 "섹션.키" 한 단계만 상정하기
    때문이다. ``llm.pricing.<모델>.<필드>`` 는 두 단계라 그 경로로는 못
    읽는다. 그래서 점 없는 ``"llm"`` 섹션 전체를 받아(``read_section`` 이
    ``pricing.claude-opus-5.input_per_mtok`` 처럼 나머지를 평평하게 둔 채
    돌려준다) 여기서 ``pricing.`` 접두어만 골라 모델 단위로 다시 묶는다.
    섹션 자체가 없으면(설정 시딩 전) 빈 dict — 비용을 0 으로 지어내지 않고
    "단가를 모른다" 로 넘긴다.
    """
    try:
        section = store.config("llm", as_of=as_of)
    except ConfigNotFound:
        return {}
    table: dict[str, dict[str, float]] = {}
    for key, value in section.items():
        if not key.startswith("pricing."):
            continue
        model, field = key[len("pricing.") :].split(".", 1)
        table.setdefault(model, {})[field] = float(value)
    return table


def _row_cost_usd(row: pd.Series, rates: dict[str, float]) -> float:
    return sum(
        float(row[token_field] or 0) * rates.get(price_field, 0.0)
        for token_field, price_field in _TOKEN_PRICE_FIELDS
    ) / 1_000_000


def cost_activity(store: Store, *, as_of: datetime, lookback: int) -> dict[str, Any]:
    """``llm_usage`` 의 실제 토큰 × ``config.llm.pricing`` 단가 = 비용.

    **이 표가 생기기 전 호출은 기록이 없다** — 화면은 그걸 "0원" 이 아니라
    "몰랐다" 로 말해야 한다. ``since`` 가 이 창(lookback) 안 첫 기록 시각이다
    (그 이전 이력은 이 창고가 알 방법이 없다).

    단가가 없는 모델(설정에 안 실린 모델 문자열)은 합계에서 빼고
    ``unpriced_calls`` 로 따로 센다 — 조용히 0 으로 채우면 총액이 실제보다
    낮게 보인다.
    """
    frame = store.get(USAGE, as_of=as_of, lookback=lookback)
    krw_rate = float(store.config("llm.usd_krw_rate", as_of=as_of))
    budget_usd = float(store.config("llm.monthly_budget_usd", as_of=as_of))
    # 예산은 **달** 단위인데 이 화면의 창(lookback)은 아니다. 창이 이달 1일에
    # 못 미치면 이달 누적은 실제보다 적게 나온다 — 그 사실을 ``month_covered``
    # 로 알린다. 모자란 채로 "예산의 12%" 라고 말하면 화면이 안심시킨다.
    month_start = as_of.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_covered = as_of - timedelta(days=lookback) <= month_start
    if frame.empty:
        return {
            "total_calls": 0,
            "since": None,
            "usd_krw_rate": krw_rate,
            "cost_usd": 0.0,
            "cost_krw": 0.0,
            "budget_usd": budget_usd,
            "month_start": month_start.isoformat(),
            "month_covered": month_covered,
            "month_to_date_usd": 0.0,
            "unpriced_calls": 0,
            "unpriced_models": [],
            "tokens": {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0},
            "by_agent": [],
        }

    pricing = _pricing_table(store, as_of=as_of)
    frame = frame.copy()
    frame["cost_usd"] = [
        _row_cost_usd(row, pricing[str(row["model"])]) if str(row["model"]) in pricing else None
        for _, row in frame.iterrows()
    ]
    priced = frame[frame["cost_usd"].notna()]
    unpriced = frame[frame["cost_usd"].isna()]

    tokens = {
        "input": int(frame["input_tokens"].sum()),
        "output": int(frame["output_tokens"].sum()),
        "cache_write": int(frame["cache_creation_input_tokens"].sum()),
        "cache_read": int(frame["cache_read_input_tokens"].sum()),
    }

    by_agent: list[dict[str, Any]] = []
    for (agent, model), group in frame.groupby(["agent", "model"]):
        priced_group = group[group["cost_usd"].notna()]
        cost_usd = float(priced_group["cost_usd"].sum())
        by_agent.append(
            {
                "agent": str(agent),
                "model": str(model),
                "calls": len(group),
                "items": int(group["items"].sum()),
                "priced": str(model) in pricing,
                "cost_usd": round(cost_usd, 6),
                "cost_krw": round(cost_usd * krw_rate),
                "tokens": {
                    "input": int(group["input_tokens"].sum()),
                    "output": int(group["output_tokens"].sum()),
                    "cache_write": int(group["cache_creation_input_tokens"].sum()),
                    "cache_read": int(group["cache_read_input_tokens"].sum()),
                },
            }
        )
    by_agent.sort(key=lambda row: -row["cost_usd"])

    cost_usd = float(priced["cost_usd"].sum())
    month_to_date = float(priced.loc[priced["computed_at"] >= month_start, "cost_usd"].sum())
    return {
        "total_calls": len(frame),
        "since": frame["computed_at"].min().isoformat(),
        "usd_krw_rate": krw_rate,
        "cost_usd": round(cost_usd, 6),
        "cost_krw": round(cost_usd * krw_rate),
        "budget_usd": budget_usd,
        "month_start": month_start.isoformat(),
        "month_covered": month_covered,
        "month_to_date_usd": round(month_to_date, 6),
        "unpriced_calls": len(unpriced),
        "unpriced_models": sorted(unpriced["model"].astype(str).unique().tolist()),
        "tokens": tokens,
        "by_agent": by_agent,
    }


def _merge_markets(
    activity: dict[str, Any], verdicts: dict[str, Any], documents: dict[str, Any]
) -> list[dict[str, Any]]:
    """세 표의 시장별 집계를 한 줄씩으로 합친다.

    ``llm_usage`` 는 여기 없다 — **비용은 시장으로 못 나눈다.** 그 표의
    ``entity_id`` 는 종목이 아니라 에이전트 이름이고, 한 번의 왕복이 여러
    종목(``items``)을 함께 처리한다(``store/tables.py``). 안분해서 채우면
    실측이 아닌 추정이 실측 칸에 들어간다.
    """
    rows: dict[str, dict[str, Any]] = {}

    def slot(name: str) -> dict[str, Any]:
        return rows.setdefault(
            name, {"market": name, "calls": 0, "verdicts": 0, "blocked": 0, "documents": 0}
        )

    for row in activity.get("by_market", []):
        slot(str(row["market"]))["calls"] = int(row["calls"])
    for row in verdicts.get("by_market", []):
        entry = slot(str(row["market"]))
        entry["verdicts"] = int(row["total"])
        entry["blocked"] = int(row["blocked"])
    for row in documents.get("by_market", []):
        slot(str(row["market"]))["documents"] = int(row["total"])

    return sorted(rows.values(), key=lambda item: (-int(item["documents"]), str(item["market"])))


def summary(store: Store, *, as_of: datetime, lookback: int) -> dict[str, Any]:
    activity = agent_activity(store, as_of=as_of, lookback=lookback)
    verdicts = verdict_activity(store, as_of=as_of, lookback=lookback)
    documents = document_activity(store, as_of=as_of, lookback=lookback)
    cost = cost_activity(store, as_of=as_of, lookback=lookback)
    return {
        "calls": activity["total"],
        "agents": len(activity["agents"]),
        "blocked": verdicts["blocked"],
        "verdicts_total": verdicts["total"],
        "documents": documents["total"],
        "llm_cost_usd": cost["cost_usd"],
        "llm_cost_krw": cost["cost_krw"],
        "llm_usage_calls": cost["total_calls"],
        "llm_usage_since": cost["since"],
        "llm_budget_usd": cost["budget_usd"],
        "llm_month_to_date_usd": cost["month_to_date_usd"],
        "llm_month_covered": cost["month_covered"],
        "markets": _merge_markets(activity, verdicts, documents),
        "warnings": _warnings(activity, cost),
    }


def _warnings(activity: dict[str, Any], cost: dict[str, Any]) -> list[str]:
    found: list[str] = []
    if not activity["agents"]:
        found.append("LLM 호출 기록이 없다. news_screen · macro_brief 가 아직 안 돌았을 수 있다")
    if cost["unpriced_models"]:
        found.append(
            "단가를 모르는 모델이 있다 — 이 모델의 비용은 합계에서 빠졌다: "
            + ", ".join(cost["unpriced_models"])
            + " (config/quant_rl_trading.yaml 의 llm.pricing 에 추가할 것)"
        )
    # 예산 경고는 **창이 이달을 다 덮을 때만** 낸다. 창이 짧으면 이달 누적이
    # 실제보다 작게 나오므로, 그 값으로 "예산 안이다" 라고 말하면 안심시킨다.
    if cost["month_covered"] and cost["month_to_date_usd"] > cost["budget_usd"]:
        found.append(
            f"이달 LLM 지출이 예산을 넘었다 — ${cost['month_to_date_usd']:.2f} / "
            f"${cost['budget_usd']:.2f} (llm.monthly_budget_usd)"
        )
    return found


__all__ = [
    "agent_activity",
    "cost_activity",
    "document_activity",
    "recent_calls",
    "summary",
    "verdict_activity",
]
