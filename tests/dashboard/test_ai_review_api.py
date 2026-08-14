"""AI 리뷰 탭 API.

이 화면의 존재 이유는 **LLM 이 실제로 무엇을 했는지 정직하게 보여주는 것**이다.

1. ``agent_cache`` 행 수가 곧 실제 호출 수다 (캐시 적중은 새 행을 안 만든다).
   비용은 잴 수 없으므로 지어내지 않는다.
2. ``verdicts`` 0건은 고장이 아니라 "거부할 사유가 없었다" 는 정답일 수
   있다 (docs/milestones.md M2) — 화면이 그 구분을 말해야 한다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from flask import Flask

from quant_rl_trading.dashboard.api import ai_review as ai_review_api
from quant_rl_trading.dashboard.app import SafeJSONProvider
from quant_rl_trading.replay.clock import ReplayClock

NOW = datetime(2026, 8, 14, tzinfo=UTC)
CALLED_AT = NOW - timedelta(days=1)


def make_app(store: Any, clock: Any) -> Flask:
    """app.py 를 건드리지 않고 이 탭의 블루프린트만 단 최소 앱."""
    app = Flask(__name__)
    app.json = SafeJSONProvider(app)
    app.config["QUANT_RL_STORE"] = store
    app.config["QUANT_RL_CLOCK"] = clock
    app.json.ensure_ascii = False  # type: ignore[attr-defined]
    app.register_blueprint(ai_review_api.bp)
    app.config.update(TESTING=True)
    return app


def cache_row(entity: str, agent: str, version: str, output: dict[str, Any]) -> dict[str, Any]:
    return {
        "entity_id": entity,
        "valid_from": CALLED_AT,
        "observed_at": CALLED_AT,
        "source": f"{agent}@{version}",
        "agent": agent,
        "agent_version": version,
        "features_hash": f"hash-{entity}-{agent}",
        "output": json.dumps(output, ensure_ascii=False),
        "computed_at": CALLED_AT,
    }


def verdict_row(entity: str, analyst: str, decision: str, category: str) -> dict[str, Any]:
    return {
        "entity_id": entity,
        "valid_from": CALLED_AT,
        "observed_at": CALLED_AT,
        "source": analyst,
        "analyst": analyst,
        "analyst_version": f"{analyst}-v0.1.0",
        "decision": decision,
        "severity": 0.5,
        "category": category,
        "reason": "테스트 사유",
        "expires_at": NOW + timedelta(days=3),
    }


def document_row(entity: str, doc_type: str, title: str) -> dict[str, Any]:
    return {
        "entity_id": entity,
        "valid_from": CALLED_AT,
        "observed_at": CALLED_AT,
        "source": "collector",
        "doc_id": f"{entity}-{doc_type}-1",
        "doc_type": doc_type,
        "title": title,
        "filer": "테스트발행",
        "url": "https://example.invalid/doc",
        "raw_path": "",
    }


@pytest.fixture
def seeded(store):  # type: ignore[no-untyped-def]
    store.seed_config_defaults()
    store.append(
        "agent_cache",
        [
            cache_row("KR:005930", "news-screen", "news-screen-v0.1.0",
                      {"keep": False, "reason": "루머성"}),
            cache_row("KR:000660", "macro-brief", "macro-brief-v0.1.0",
                      {"tone": "risk_off", "headline": "금리 동결", "reading": "중립"}),
        ],
        ingest_run_id="cache-seed",
    )
    store.append(
        "documents",
        [document_row("KR:005930", "news", "테스트 기사 제목")],
        ingest_run_id="doc-seed",
    )
    return store


@pytest.fixture
def client(seeded):  # type: ignore[no-untyped-def]
    return make_app(seeded, ReplayClock(NOW)).test_client()


def body(response: Any) -> dict[str, Any]:
    return response.get_json()  # type: ignore[no-any-return]


# -- 호출은 정직하게 --------------------------------------------------------------


def test_summary_counts_calls_and_agents(client) -> None:
    data = body(client.get("/api/ai-review/summary"))["data"]

    assert data["calls"] == 2
    assert data["agents"] == 2
    assert data["documents"] == 1


def test_agent_activity_breaks_down_by_agent_and_version(client) -> None:
    data = body(client.get("/api/ai-review/calls"))["data"]

    agents = {row["agent"]: row for row in data["agents"]["agents"]}
    assert agents["news-screen"]["calls"] == 1
    assert agents["macro-brief"]["calls"] == 1
    # 비용은 어디에도 없다 — agent_cache.output 에 토큰 사용량이 없다.
    for row in data["agents"]["agents"]:
        assert "cost" not in row


def test_recent_calls_summarize_output_without_fabricating_schema(client) -> None:
    data = body(client.get("/api/ai-review/calls"))["data"]
    recent = {row["agent"]: row for row in data["recent"]}

    assert "루머성" in recent["news-screen"]["summary"]
    assert "금리 동결" in recent["macro-brief"]["summary"]


def test_no_calls_is_reported_as_empty_not_zero_filled(seeded) -> None:
    empty_client = make_app(seeded, ReplayClock(CALLED_AT - timedelta(days=200))).test_client()

    data = body(empty_client.get("/api/ai-review/calls"))["data"]

    assert data["agents"]["agents"] == []
    assert data["recent"] == []


# -- verdicts 0건은 고장이 아니다 -------------------------------------------------


def test_empty_verdicts_are_not_treated_as_failure(client) -> None:
    """이 창고에는 verdicts 행이 없다. 0건은 '거부할 사유가 없었다'는 정답이다."""
    data = body(client.get("/api/ai-review/verdicts"))["data"]

    assert data["total"] == 0
    assert data["blocked"] == 0
    assert data["recent"] == []
    # scorecard 는 항상 딸려 온다(빈 성적표라도) — evaluate_blocks 계약.
    assert "scorecard" in data


def test_verdicts_report_blocks_when_present(seeded) -> None:
    seeded.append(
        "verdicts",
        [verdict_row("KR:005930", "news", "block", "루머")],
        ingest_run_id="verdict-seed",
    )
    client = make_app(seeded, ReplayClock(NOW)).test_client()

    data = body(client.get("/api/ai-review/verdicts"))["data"]

    assert data["total"] == 1
    assert data["blocked"] == 1
    assert data["recent"][0]["entity_id"] == "KR:005930"
    assert data["recent"][0]["decision"] == "block"


# -- documents ---------------------------------------------------------------


def test_documents_are_listed_by_type(client) -> None:
    data = body(client.get("/api/ai-review/documents"))["data"]

    assert data["total"] == 1
    assert data["by_type"] == [{"doc_type": "news", "count": 1}]
    assert data["recent"][0]["title"] == "테스트 기사 제목"


# -- 시점 규약 -----------------------------------------------------------------


def test_every_route_accepts_and_returns_as_of(client) -> None:
    past = CALLED_AT.isoformat()
    for path in (
        "/api/ai-review/summary",
        "/api/ai-review/calls",
        "/api/ai-review/verdicts",
        "/api/ai-review/documents",
    ):
        response = client.get(f"{path}?as_of={past}")
        assert response.status_code == 200, path
        assert body(response)["as_of"] == past, path
