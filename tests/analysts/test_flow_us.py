"""flow_us — FINRA 공매도 잔고로 만든 피처 (시행 I, 2026-09-02).

고정하는 것 둘: **최신 공표 잔고 하나만 쓴다**(반월 값을 매일 펴지 않는다),
그리고 **일별 거래량 계열은 읽지 않는다**(거래량 피처는 IC 미달로 뺐다).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from quant_rl_trading.analysts.flow_us import FlowUsAnalyst
from quant_rl_trading.collectors.market_hours import Market
from quant_rl_trading.replay.clock import ReplayClock

NOW = datetime(2026, 8, 19, 22, 0, tzinfo=UTC)


def _interest(entity: str, *, settled: datetime, observed: datetime, position: float,
              previous: float, dtc: float) -> dict:  # type: ignore[type-arg]
    return {
        "entity_id": entity, "valid_from": settled, "observed_at": observed,
        "source": "finra", "market": "US", "kind": "interest",
        "short_volume": None, "short_exempt_volume": None, "total_volume": None,
        "short_position": position, "previous_short_position": previous,
        "days_to_cover": dtc, "average_daily_volume": 1_000.0,
    }


@pytest.fixture
def analyst(store):  # type: ignore[no-untyped-def]
    return FlowUsAnalyst(store, ReplayClock(NOW), market=Market.US)


def test_잔고가_크고_늘수록_점수가_낮다(store, analyst) -> None:  # type: ignore[no-untyped-def]
    settled = NOW - timedelta(days=20)
    observed = NOW - timedelta(days=5)
    store.append("short_flow", [
        _interest("US:HEAVY", settled=settled, observed=observed, position=2_000.0, previous=1_000.0, dtc=8.0),
        _interest("US:LIGHT", settled=settled, observed=observed, position=500.0, previous=600.0, dtc=1.0),
    ], ingest_run_id="si-1", source="finra")

    features = analyst.features(NOW)
    scores = analyst.raw_score(features)

    assert set(features.columns) == {"days_to_cover", "short_interest_change"}
    assert scores["US:HEAVY"] < scores["US:LIGHT"], scores.to_dict()


def test_공표_전_잔고는_안_보이고_최신_하나만_쓴다(store, analyst) -> None:  # type: ignore[no-untyped-def]
    """두 결제일이 있다. 최신 것은 아직 공표 전(observed_at 이 미래)이라 직전 것을
    써야 하고, 그 직전 것 하나만 피처가 된다."""
    older = _interest("US:AAA", settled=NOW - timedelta(days=30), observed=NOW - timedelta(days=16),
                      position=1_000.0, previous=1_000.0, dtc=2.0)
    unpublished = _interest("US:AAA", settled=NOW - timedelta(days=15), observed=NOW + timedelta(days=1),
                            position=9_000.0, previous=1_000.0, dtc=20.0)
    peer = _interest("US:BBB", settled=NOW - timedelta(days=30), observed=NOW - timedelta(days=16),
                     position=1_000.0, previous=1_000.0, dtc=2.0)
    store.append("short_flow", [older, unpublished, peer], ingest_run_id="si-2", source="finra")

    latest = analyst._latest_interest(NOW)

    assert latest is not None
    assert float(latest.loc["US:AAA", "days_to_cover"]) == 2.0, "공표 전 잔고를 봤다"
    assert len(latest) == 2


def test_일별_거래량만_있으면_빈_프레임(store, analyst) -> None:  # type: ignore[no-untyped-def]
    day = NOW - timedelta(days=1)
    store.append("short_flow", [{
        "entity_id": "US:VOL", "valid_from": day, "observed_at": day,
        "source": "finra", "market": "US", "kind": "volume",
        "short_volume": 500_000.0, "short_exempt_volume": 1_000.0, "total_volume": 1_000_000.0,
        "short_position": None, "previous_short_position": None,
        "days_to_cover": None, "average_daily_volume": None,
    }], ingest_run_id="vol", source="finra")

    assert analyst.features(NOW).empty
