"""일일 리뷰 — 캐시가 답하면 LLM 을 안 부르고, 예산을 넘기면 안 부르며, 안 부른 날도 기록한다."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from quant_rl_trading.auditor import daily_review
from quant_rl_trading.replay.clock import ReplayClock

NOW = datetime(2026, 8, 28, 14, 35, tzinfo=UTC)


@dataclass
class _Block:
    type: str
    input: dict


@dataclass
class _Usage:
    input_tokens: int = 1200
    output_tokens: int = 300
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


@dataclass
class _Response:
    content: list
    usage: _Usage
    id: str = "msg_test"


class _Client:
    def __init__(self) -> None:
        self.calls = 0
        self.messages = self

    def create(self, **_kwargs):  # type: ignore[no-untyped-def]
        self.calls += 1
        return _Response(
            content=[_Block("tool_use", {"tone": "quiet", "headline": "기록 없음 — 조용한 날", "body": "회계 기록이 없다."})],
            usage=_Usage(),
        )


def _reviewer(store, client, **kw):  # type: ignore[no-untyped-def]
    store.seed_config_defaults()
    return daily_review.DailyReviewer(
        store=store, facts_store=store, clock=ReplayClock(NOW), client=client,
        model="claude-sonnet-5", **kw,
    )


def test_second_run_with_same_facts_is_answered_by_cache(store) -> None:
    client = _Client()
    reviewer = _reviewer(store, client, budget_usd=50.0, month_to_date_usd=1.0)
    first = reviewer.review(as_of=NOW, market="KR")
    second = reviewer.review(as_of=NOW, market="KR")
    assert client.calls == 1
    assert first["status"] == "written" and second["status"] == "cached"
    assert first["headline"] == second["headline"]
    reviews = store.get("reviews", as_of=NOW, entity="LIVE:KR")
    assert len(reviews) == 1  # 같은 사실·같은 세션은 한 편
    usage = store.get("llm_usage", as_of=NOW)
    assert len(usage) == 1 and int(usage.iloc[0]["input_tokens"]) == 1200


def test_budget_exhausted_skips_the_call_but_leaves_a_record(store) -> None:
    client = _Client()
    reviewer = _reviewer(store, client, budget_usd=50.0, month_to_date_usd=50.0)
    result = reviewer.review(as_of=NOW, market="KR")
    assert client.calls == 0
    assert result["status"] == "skipped_budget"
    latest = daily_review.latest_review(store, as_of=NOW, market="KR")
    assert latest is not None and latest["status"] == "skipped_budget"
    assert "예산" in latest["headline"]


def test_latest_review_respects_as_of(store) -> None:
    client = _Client()
    reviewer = _reviewer(store, client, budget_usd=50.0, month_to_date_usd=0.0)
    reviewer.review(as_of=NOW, market="KR")
    assert daily_review.latest_review(store, as_of=NOW, market="KR") is not None
    earlier = datetime(2026, 8, 27, 14, 35, tzinfo=UTC)
    assert daily_review.latest_review(store, as_of=earlier, market="KR") is None
