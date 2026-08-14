"""``NewsScreen`` 이 실제 토큰 사용량을 ``llm_usage`` 에 남기는지.

AI 리뷰 탭이 "LLM 호출 몇 번 · 얼마 썼는지" 를 보여주려면 원재료(토큰 수)가
창고에 있어야 한다. 여기서는 진짜 Claude 를 부르지 않는다 — 응답을 흉내 낸
스텁 클라이언트로 기록 경로만 검증한다. 돈을 쓰지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from quant_rl_trading.analysts.news_screen import AGENT, VERSION, Candidate, NewsScreen
from quant_rl_trading.replay.clock import ReplayClock
from quant_rl_trading.schemas.verdict import Category

AS_OF = datetime(2026, 8, 14, 16, 0, tzinfo=UTC)


@dataclass
class FakeUsage:
    input_tokens: int = 1200
    output_tokens: int = 340
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


@dataclass
class FakeBlock:
    type: str
    input: dict[str, Any]


@dataclass
class FakeResponse:
    content: list[FakeBlock]
    usage: FakeUsage | None
    _request_id: str | None = "req_test123"


class FakeMessages:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls = 0

    def create(self, **kwargs: Any) -> FakeResponse:
        self.calls += 1
        return self.response


class FakeClient:
    def __init__(self, response: FakeResponse) -> None:
        self.messages = FakeMessages(response)


def make_candidate(entity_id: str = "KR:005930") -> Candidate:
    return Candidate(
        entity_id=entity_id,
        category=Category.INSIDER_SELL,
        severity=0.6,
        reason="테스트 사유",
        title="테스트 제목",
    )


def test_usage_is_recorded_after_a_real_call(store) -> None:  # type: ignore[no-untyped-def]
    candidate = make_candidate()
    response = FakeResponse(
        content=[FakeBlock(type="tool_use", input={"verdicts": [
            {"id": candidate.fingerprint, "keep": False, "reason": "오탐"},
        ]})],
        usage=FakeUsage(input_tokens=1500, output_tokens=200),
    )
    screener = NewsScreen(
        store=store, clock=ReplayClock(AS_OF), api_key="unused", client=FakeClient(response)
    )

    screener.screen([candidate], as_of=AS_OF)

    usage = store.get("llm_usage", as_of=AS_OF, lookback=1)
    assert len(usage) == 1
    row = usage.iloc[0]
    assert row["agent"] == AGENT
    assert row["agent_version"] == VERSION
    assert row["input_tokens"] == 1500
    assert row["output_tokens"] == 200
    assert row["items"] == 1


def test_usage_not_recorded_when_response_has_no_usage(store) -> None:  # type: ignore[no-untyped-def]
    """스텁이 usage 를 안 주면(예: 옛 테스트 픽스처) 조용히 넘어간다."""
    candidate = make_candidate()
    response = FakeResponse(
        content=[FakeBlock(type="tool_use", input={"verdicts": []})],
        usage=None,
    )
    screener = NewsScreen(
        store=store, clock=ReplayClock(AS_OF), api_key="unused", client=FakeClient(response)
    )

    screener.screen([candidate], as_of=AS_OF)

    usage = store.get("llm_usage", as_of=AS_OF, lookback=1)
    assert usage.empty


def test_usage_records_once_per_call_not_per_item(store) -> None:  # type: ignore[no-untyped-def]
    """한 배치에 여러 건을 같이 물어도 llm_usage 행은 하나다 — 항목 수만큼
    비용이 부풀려지면 안 된다."""
    candidates = [make_candidate("KR:005930"), make_candidate("KR:000660")]
    response = FakeResponse(
        content=[FakeBlock(type="tool_use", input={"verdicts": [
            {"id": c.fingerprint, "keep": True, "reason": ""} for c in candidates
        ]})],
        usage=FakeUsage(input_tokens=2000, output_tokens=400),
    )
    screener = NewsScreen(
        store=store, clock=ReplayClock(AS_OF), api_key="unused", client=FakeClient(response)
    )

    screener.screen(candidates, as_of=AS_OF)

    usage = store.get("llm_usage", as_of=AS_OF, lookback=1)
    assert len(usage) == 1
    assert usage.iloc[0]["items"] == 2
