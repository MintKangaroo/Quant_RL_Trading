"""미장 뉴스 제목 번역기.

진짜 Claude 를 부르지 않는다 — 응답을 흉내 낸 스텁 클라이언트로 캐시·폴백
경로만 검증한다. 돈을 쓰지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from quant_rl_trading.replay.clock import ReplayClock
from quant_rl_trading.reporting.translate import (
    AGENT,
    VERSION,
    Headline,
    NewsTitleTranslate,
)
from quant_rl_trading.store import Store

AS_OF = datetime(2026, 8, 14, 16, 0, tzinfo=UTC)


@dataclass
class FakeUsage:
    input_tokens: int = 400
    output_tokens: int = 80
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


def _headline(title: str = "META Zuckerberg's $250 Billion Data Center") -> Headline:
    return Headline(entity_id="US:META", title=title)


def test_no_key_returns_no_translation_and_original_survives(store: Store) -> None:
    """키가 없으면 아무것도 안 옮긴다 — 호출부가 원문으로 폴백한다."""
    headline = _headline()
    translator = NewsTitleTranslate(store=store, clock=ReplayClock(AS_OF), api_key="")
    out = translator.translate([headline], as_of=AS_OF)
    assert out == {}
    assert translator.failures


def test_translation_is_returned_by_fingerprint(store: Store) -> None:
    headline = _headline()
    response = FakeResponse(
        content=[FakeBlock(type="tool_use", input={"translations": [
            {"id": headline.fingerprint, "ko": "메타 저커버그의 2,500억 달러 데이터센터"},
        ]})],
        usage=FakeUsage(),
    )
    translator = NewsTitleTranslate(
        store=store, clock=ReplayClock(AS_OF), api_key="unused", client=FakeClient(response)
    )
    out = translator.translate([headline], as_of=AS_OF)
    assert out[headline.fingerprint] == "메타 저커버그의 2,500억 달러 데이터센터"


def test_api_failure_falls_back_silently(store: Store) -> None:
    """API 가 죽으면 예외를 던지지 않는다 — 빈 dict, 호출부가 원문으로 폴백한다."""

    class BoomClient:
        class messages:
            @staticmethod
            def create(**kwargs: Any) -> Any:
                raise RuntimeError("network down")

    translator = NewsTitleTranslate(
        store=store, clock=ReplayClock(AS_OF), api_key="unused", client=BoomClient()
    )
    out = translator.translate([_headline()], as_of=AS_OF)
    assert out == {}
    assert any("번역 실패" in failure for failure in translator.failures)


def test_persist_defaults_to_true_and_writes_the_cache(store: Store) -> None:
    """**기본값은 켬** (팀 리드 승인, 2026-08-15) — 크론이 같은 기사를 매일
    다시 번역하면 돈을 버린다."""
    headline = _headline()
    response = FakeResponse(
        content=[FakeBlock(type="tool_use", input={"translations": [
            {"id": headline.fingerprint, "ko": "번역"},
        ]})],
        usage=FakeUsage(),
    )
    translator = NewsTitleTranslate(
        store=store, clock=ReplayClock(AS_OF), api_key="unused", client=FakeClient(response)
    )
    assert translator.persist is True
    translator.translate([headline], as_of=AS_OF)

    cache = store.get("agent_cache", as_of=AS_OF, lookback=1)
    assert len(cache) == 1, "persist 기본값이 켜져 있는데 agent_cache 에 안 썼다"


def test_persist_false_can_be_turned_off(store: Store) -> None:
    """캐시 오염을 의심할 때처럼 끄고 돌려야 할 때가 있다."""
    headline = _headline()
    response = FakeResponse(
        content=[FakeBlock(type="tool_use", input={"translations": [
            {"id": headline.fingerprint, "ko": "번역"},
        ]})],
        usage=FakeUsage(),
    )
    translator = NewsTitleTranslate(
        store=store, clock=ReplayClock(AS_OF), api_key="unused",
        client=FakeClient(response), persist=False,
    )
    translator.translate([headline], as_of=AS_OF)

    cache = store.get("agent_cache", as_of=AS_OF, lookback=1)
    assert cache.empty, "persist=False 인데 agent_cache 에 썼다"


def test_persist_true_caches_and_second_call_skips_the_api(store: Store) -> None:
    """``persist=True`` 면 캐시에 남고, 같은 제목을 다시 물으면 API 를 안 부른다."""
    headline = _headline()
    response = FakeResponse(
        content=[FakeBlock(type="tool_use", input={"translations": [
            {"id": headline.fingerprint, "ko": "번역됨"},
        ]})],
        usage=FakeUsage(),
    )
    client = FakeClient(response)
    translator = NewsTitleTranslate(
        store=store, clock=ReplayClock(AS_OF), api_key="unused", client=client, persist=True
    )
    first = translator.translate([headline], as_of=AS_OF)
    assert first[headline.fingerprint] == "번역됨"
    assert client.messages.calls == 1

    cache = store.get("agent_cache", as_of=AS_OF, lookback=1)
    assert len(cache) == 1
    assert cache.iloc[0]["agent"] == AGENT
    assert cache.iloc[0]["agent_version"] == VERSION

    second = translator.translate([headline], as_of=AS_OF)
    assert second[headline.fingerprint] == "번역됨"
    assert client.messages.calls == 1, "캐시가 있는데 API 를 다시 불렀다"


def test_cache_hits_and_misses_are_logged(store, caplog) -> None:  # type: ignore[no-untyped-def]
    """캐시가 실제로 먹는지 로그로 알 수 있어야 한다 — 안 남기면 매번 미스가
    나도 조용히 돈만 나간다."""
    headline = _headline()
    response = FakeResponse(
        content=[FakeBlock(type="tool_use", input={"translations": [
            {"id": headline.fingerprint, "ko": "번역됨"},
        ]})],
        usage=FakeUsage(),
    )
    translator = NewsTitleTranslate(
        store=store, clock=ReplayClock(AS_OF), api_key="unused",
        client=FakeClient(response), persist=True,
    )
    with caplog.at_level("INFO", logger="quant_rl_trading.reporting.translate"):
        translator.translate([headline], as_of=AS_OF)  # 첫 호출 — 전부 미스
    assert any("미스 1건" in record.message for record in caplog.records)
    assert any("히트 0건" in record.message for record in caplog.records)

    caplog.clear()
    with caplog.at_level("INFO", logger="quant_rl_trading.reporting.translate"):
        translator.translate([headline], as_of=AS_OF)  # 두 번째 — 캐시 히트
    assert any("히트 1건" in record.message for record in caplog.records)
    assert any("미스 0건" in record.message for record in caplog.records)


def test_empty_input_is_a_noop(store: Store) -> None:
    translator = NewsTitleTranslate(store=store, clock=ReplayClock(AS_OF), api_key="unused")
    assert translator.translate([], as_of=AS_OF) == {}


def test_usage_is_recorded_regardless_of_persist(store: Store) -> None:
    """비용 집계는 캐시 저장 여부와 무관하다."""
    headline = _headline()
    response = FakeResponse(
        content=[FakeBlock(type="tool_use", input={"translations": [
            {"id": headline.fingerprint, "ko": "번역"},
        ]})],
        usage=FakeUsage(input_tokens=999, output_tokens=111),
    )
    translator = NewsTitleTranslate(
        store=store, clock=ReplayClock(AS_OF), api_key="unused",
        client=FakeClient(response), persist=False,
    )
    translator.translate([headline], as_of=AS_OF)

    usage = store.get("llm_usage", as_of=AS_OF, lookback=1)
    assert len(usage) == 1
    assert usage.iloc[0]["agent"] == AGENT
    assert usage.iloc[0]["input_tokens"] == 999
