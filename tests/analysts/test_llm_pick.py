"""``LlmPickAnalyst`` (사전등록 시행 G) — 입력·캐시·예산·계약.

**진짜 Claude 를 부르지 않는다.** 스텁 클라이언트로 경로만 검증한다 — 돈을 쓰지 않는다.
여기서 지키는 것 넷:

1. 프롬프트 입력에 **미래가 없다** (as_of 게이트가 재무·시세·공시를 자른다).
2. 같은 입력이면 **캐시가 답한다** — 두 번째 실행은 호출 0.
3. 예산을 넘기면 **안 부르고 점수도 없다** (0 으로 안 채운다).
4. 점수가 Analyst 계약(`features` → `raw_score` → `run`)에 그대로 실린다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from quant_rl_trading.analysts.llm_pick import AGENT, VERSION, LlmPickAnalyst
from quant_rl_trading.collectors.market_hours import Market
from quant_rl_trading.replay.clock import ReplayClock

NOW = datetime(2026, 8, 12, 6, 40, tzinfo=UTC)
ENTITIES = ("KR:000100", "KR:000200")
QUARTER_END = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
FILING_LAG = timedelta(days=45)


# -- 스텁 클라이언트 ---------------------------------------------------------------


@dataclass
class FakeUsage:
    input_tokens: int = 900
    output_tokens: int = 120
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


@dataclass
class FakeBlock:
    type: str
    input: dict[str, Any]


@dataclass
class FakeResponse:
    content: list[FakeBlock]
    usage: FakeUsage | None = field(default_factory=FakeUsage)
    id: str = "msg_test"


class FakeMessages:
    def __init__(self) -> None:
        self.calls = 0
        self.payloads: list[Any] = []

    def create(self, **kwargs: Any) -> FakeResponse:
        self.calls += 1
        import json

        batch = json.loads(kwargs["messages"][0]["content"])
        self.payloads.append(batch)
        picks = [
            {
                "id": item["id"],
                # 결정적으로: 코드가 큰 종목에 높은 점수.
                "outlook": 0.5 if item["id"] == "KR:000100" else -0.5,
                "confidence": 0.7,
                "reason": "테스트",
            }
            for item in batch
        ]
        return FakeResponse(content=[FakeBlock(type="tool_use", input={"picks": picks})])


class FakeClient:
    def __init__(self) -> None:
        self.messages = FakeMessages()

    @property
    def calls(self) -> int:
        return self.messages.calls


# -- 창고 심기 ---------------------------------------------------------------------


def _fundamental_row(entity: str, year: int, quarter: int, metric: str, value: float) -> dict:
    month, day = QUARTER_END[quarter]
    period_end = datetime(year, month, day, tzinfo=UTC)
    return {
        "entity_id": entity, "valid_from": period_end,
        "observed_at": period_end + FILING_LAG, "source": "dart", "market": "KR",
        "metric": metric, "value": value, "fiscal_period": f"{year}Q{quarter}",
        "report_type": "dart_cfs",
    }


@pytest.fixture
def seeded(store):  # type: ignore[no-untyped-def]
    """두 종목 × 재무·시세·시총·공시. 하나는 이익이 크고 하나는 작다."""
    store.seed_config_defaults()

    rows: list[dict] = []
    for entity, margin in zip(ENTITIES, (0.20, 0.02), strict=True):
        for year, quarter in ((2025, 3), (2025, 4), (2026, 1), (2026, 2)):
            factor = 4.0 if quarter == 4 else 1.0
            rows += [
                _fundamental_row(entity, year, quarter, "revenue", 100.0 * factor),
                _fundamental_row(entity, year, quarter, "operating_income", 100.0 * margin * factor),
                _fundamental_row(entity, year, quarter, "net_income", 100.0 * margin * factor),
                _fundamental_row(entity, year, quarter, "total_equity", 500.0),
                _fundamental_row(entity, year, quarter, "total_liabilities", 200.0),
                _fundamental_row(entity, year, quarter, "total_assets", 700.0),
            ]
    store.append("fundamentals", rows, ingest_run_id="f-seed")

    universe, prices, caps, docs = [], [], [], []
    for index in range(40):
        moment = NOW - timedelta(days=40 - index)
        for offset, entity in enumerate(ENTITIES):
            universe.append({
                "entity_id": entity, "valid_from": moment, "observed_at": moment,
                "source": "test", "market": "KR", "name": entity,
                "is_listed": True, "is_tradable": True, "delisted_on": None,
            })
            close = 10_000.0 + index * (5 + offset)
            prices.append({
                "entity_id": entity, "valid_from": moment, "observed_at": moment,
                "source": "test", "market": "KR", "open": close, "high": close,
                "low": close, "close": close, "volume": 100_000.0,
                "value": 1_000_000_000.0, "adj_factor": None,
            })
            caps.append({
                "entity_id": entity, "valid_from": moment, "observed_at": moment,
                "source": "test", "market": "KR", "metric": "market_cap",
                "value": 1e11 * (offset + 1),
            })
    store.append("universe", universe, ingest_run_id="u-seed")
    store.append("prices", prices, ingest_run_id="p-seed")
    store.append("market_stats", caps, ingest_run_id="m-seed")

    for offset, entity in enumerate(ENTITIES):
        # 하나는 과거 공시, 하나는 **미래 공시** — 미래는 프롬프트에 들어오면 안 된다.
        docs.append({
            "entity_id": entity, "valid_from": NOW - timedelta(days=5),
            "observed_at": NOW - timedelta(days=5), "source": "dart",
            "doc_id": f"past-{offset}", "doc_type": "earnings", "title": "과거 공시",
            "filer": entity, "url": "", "raw_path": None,
        })
        docs.append({
            "entity_id": entity, "valid_from": NOW + timedelta(days=3),
            "observed_at": NOW + timedelta(days=3), "source": "dart",
            "doc_id": f"future-{offset}", "doc_type": "earnings", "title": "미래 공시",
            "filer": entity, "url": "", "raw_path": None,
        })
    store.append("documents", docs, ingest_run_id="d-seed")
    return store


def _analyst(store, client=None, **kw):  # type: ignore[no-untyped-def]
    return LlmPickAnalyst(
        store, ReplayClock(NOW), market=Market.KR,
        entities=frozenset(ENTITIES), client=client, **kw,
    )


# -- 1. 입력에 미래가 없다 ----------------------------------------------------------


def test_payload_has_no_future_data(seeded) -> None:
    payloads = _analyst(seeded).payloads(NOW)

    assert set(payloads) == set(ENTITIES)
    for payload in payloads.values():
        assert "미래 공시" not in payload["recent_disclosures"]
        assert "과거 공시" in payload["recent_disclosures"]
        # 재무는 게이트가 자른다 — 공시일(기간말+45일)이 as_of 를 넘는 분기는 안 보인다.
        for quarter in payload["quarters"]:
            year, q = quarter["period"].split("Q")
            month, day = QUARTER_END[int(q)]
            filed = datetime(int(year), month, day, tzinfo=UTC) + FILING_LAG
            assert filed <= NOW, f"{quarter['period']} 은 아직 공시 전이다"


def test_payload_carries_only_the_declared_inputs(seeded) -> None:
    """프로토콜이 적은 것만 보낸다 — 가격 예측을 유도할 재료를 더 넣지 않는다."""
    payload = next(iter(_analyst(seeded).payloads(NOW).values()))

    assert set(payload) == {
        "id", "quarters", "valuation", "price", "recent_disclosures",
        "currency", "revenue_ttm_krw",
    }
    assert set(payload["price"]) == {"return_20d", "volatility_20d"}
    # 배당 이력이 창고에 없다 — 0 이 아니라 null 이어야 한다.
    assert payload["valuation"]["dividend_yield"] is None


# -- 2. 캐시가 답한다 --------------------------------------------------------------


def test_second_run_is_answered_by_cache(seeded) -> None:
    client = FakeClient()
    first = _analyst(seeded, client).features(NOW)
    assert client.calls == 1
    assert list(first.index) == list(ENTITIES)

    second_client = FakeClient()
    second = _analyst(seeded, second_client)
    frame = second.features(NOW)

    assert second_client.calls == 0, "같은 입력인데 다시 물었다"
    assert second.cache_hits == len(ENTITIES)
    assert frame["llm_outlook"].tolist() == first["llm_outlook"].tolist()


def test_usage_is_recorded(seeded) -> None:
    _analyst(seeded, FakeClient()).features(NOW)

    usage = seeded.get("llm_usage", as_of=NOW + timedelta(minutes=1))
    assert len(usage) == 1
    assert usage.iloc[0]["agent"] == AGENT
    assert usage.iloc[0]["agent_version"] == VERSION
    assert int(usage.iloc[0]["input_tokens"]) == 900


# -- 3. 예산 --------------------------------------------------------------------


def test_budget_exhausted_records_nothing(seeded) -> None:
    client = FakeClient()
    analyst = _analyst(seeded, client, budget_usd=100.0, month_to_date_usd=100.0)

    frame = analyst.features(NOW)

    assert client.calls == 0
    assert frame.empty, "예산이 없으면 점수도 없다 — 0 으로 채우지 않는다"
    assert analyst.skipped_budget == len(ENTITIES)
    assert seeded.get("llm_usage", as_of=NOW + timedelta(minutes=1)).empty


def test_missing_key_does_not_invent_scores(seeded) -> None:
    analyst = _analyst(seeded)  # client 도 api_key 도 없다

    assert analyst.features(NOW).empty
    assert analyst.failures


# -- 4. Analyst 계약 -------------------------------------------------------------


def test_scores_flow_into_the_analyst_contract(seeded) -> None:
    analyst = _analyst(seeded, FakeClient())

    signals = analyst.run(NOW, confidence=0.5)

    assert [signal.entity_id for signal in signals] == list(ENTITIES)
    assert {signal.analyst for signal in signals} == {AGENT}
    assert {signal.analyst_version for signal in signals} == {VERSION}
    # 큰 점수를 받은 쪽이 큰 score 여야 한다 (rank_score 는 단조다).
    by_entity = {signal.entity_id: signal.score for signal in signals}
    assert by_entity["KR:000100"] > by_entity["KR:000200"]
    assert all(evidence for evidence in (signal.evidence for signal in signals))


def test_raw_score_is_cross_sectional_rank(seeded) -> None:
    analyst = _analyst(seeded, FakeClient())
    frame = analyst.features(NOW)

    scores = analyst.raw_score(frame)

    # 순위 정규화 — 값 자체가 아니라 **순서**만 남는다. outlook 이 0.5/-0.5 든
    # 5.0/-5.0 이든 같은 점수가 나와야 한다.
    assert scores["KR:000100"] > scores["KR:000200"]
    assert scores.notna().all()
    doubled = analyst.raw_score(frame.assign(llm_outlook=frame["llm_outlook"] * 10.0))
    assert doubled.tolist() == scores.tolist()
