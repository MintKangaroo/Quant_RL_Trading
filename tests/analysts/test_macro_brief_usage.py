"""``MacroBrief`` 이 실제 토큰 사용량을 ``llm_usage`` 에 남기는지.

news_screen 과 같은 검증이다 — 진짜 Claude 를 부르지 않고 스텁 응답으로
기록 경로만 확인한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from quant_rl_trading.analysts.macro_brief import AGENT, VERSION, MacroBrief
from quant_rl_trading.replay.clock import ReplayClock

AS_OF = datetime(2026, 8, 14, 16, 0, tzinfo=UTC)


@dataclass
class FakeUsage:
    input_tokens: int = 900
    output_tokens: int = 150
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
    _request_id: str | None = "req_macro456"


class FakeMessages:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response

    def create(self, **kwargs: Any) -> FakeResponse:
        return self.response


class FakeClient:
    def __init__(self, response: FakeResponse) -> None:
        self.messages = FakeMessages(response)


def release(indicator: str = "CPI") -> dict[str, Any]:
    return {
        "entity_id": "US:MACRO:CPI",
        "indicator": indicator,
        "market": "US",
        "scheduled_at": AS_OF.isoformat(),
        "actual": 3.1,
        "previous": 3.0,
    }


def test_usage_is_recorded_after_a_real_call(store) -> None:  # type: ignore[no-untyped-def]
    rel = release()
    brief = MacroBrief(store=store, clock=ReplayClock(AS_OF), api_key="unused")
    fingerprint = brief.fingerprint(rel, None)
    response = FakeResponse(
        content=[FakeBlock(type="tool_use", input={"briefs": [
            {"id": fingerprint, "tone": "neutral", "headline": "h", "reading": "r"},
        ]})],
        usage=FakeUsage(input_tokens=1100, output_tokens=250),
    )
    brief.client = FakeClient(response)

    brief.explain([rel], as_of=AS_OF)

    usage = store.get("llm_usage", as_of=AS_OF, lookback=1)
    assert len(usage) == 1
    row = usage.iloc[0]
    assert row["agent"] == AGENT
    assert row["agent_version"] == VERSION
    assert row["input_tokens"] == 1100
    assert row["output_tokens"] == 250
    assert row["items"] == 1


def test_usage_not_recorded_when_response_has_no_usage(store) -> None:  # type: ignore[no-untyped-def]
    rel = release()
    brief = MacroBrief(store=store, clock=ReplayClock(AS_OF), api_key="unused")
    response = FakeResponse(content=[FakeBlock(type="tool_use", input={"briefs": []})], usage=None)
    brief.client = FakeClient(response)

    brief.explain([rel], as_of=AS_OF)

    usage = store.get("llm_usage", as_of=AS_OF, lookback=1)
    assert usage.empty
