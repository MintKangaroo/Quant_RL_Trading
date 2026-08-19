"""flow_us — FINRA 공매도로 만든 피처 (#50).

여기서 고정하는 것은 **"수준이 아니라 편차"** 라는 설계다.

공매도 비율의 시장 중앙값이 0.50 이다(2026-08-14 실측 0.4992). FINRA 집계에
시장조성자 헤지가 섞여 있어서 그렇고, 그걸 모르면 "0.5 넘으면 과열" 같은
규칙을 만들어 시장의 절반을 과열로 판정한다. 그래서 절대 수준이 아니라
**그 종목의 평소 대비 편차**를 본다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from quant_rl_trading.analysts.flow_us import (
    MIN_OBSERVATIONS,
    FlowUsAnalyst,
)
from quant_rl_trading.collectors.market_hours import Market
from quant_rl_trading.replay.clock import ReplayClock

NOW = datetime(2026, 8, 19, 22, 0, tzinfo=UTC)
SESSIONS = 25


def seed(store, ratios: dict[str, list[float]], *, exempt: float = 0.01) -> None:  # type: ignore[no-untyped-def]
    """종목별 공매도 비율 시계열을 심는다. 총거래량은 고정."""
    rows = []
    for offset in range(SESSIONS):
        day = NOW - timedelta(days=SESSIONS - offset)
        for entity, series in ratios.items():
            ratio = series[offset]
            total = 1_000_000.0
            rows.append({
                "entity_id": entity,
                "valid_from": day,
                "observed_at": day,
                "source": "finra",
                "market": "US",
                "kind": "volume",
                "short_volume": ratio * total,
                "short_exempt_volume": exempt * total,
                "total_volume": total,
                "short_position": None,
                "previous_short_position": None,
                "days_to_cover": None,
                "average_daily_volume": None,
            })
    store.append("short_flow", rows, ingest_run_id="finra-test", source="finra")


@pytest.fixture
def analyst(store):  # type: ignore[no-untyped-def]
    return FlowUsAnalyst(store, ReplayClock(NOW), market=Market.US)


def test_수준이_같아도_평소보다_높으면_눌린다(store, analyst) -> None:  # type: ignore[no-untyped-def]
    """**둘 다 오늘 비율이 0.60 이다.** 다른 것은 평소뿐이다.

    calm 은 늘 0.60 이었고 spike 는 0.40 이다가 올라왔다. 수준으로 재면 둘이
    같지만, 편차로 재면 spike 만 공매도 압력이다.
    """
    calm = [0.60] * SESSIONS
    spike = [0.40] * (SESSIONS - 5) + [0.60] * 5
    seed(store, {"US:CALM": calm, "US:SPIKE": spike})

    features = analyst.features(NOW)
    scores = analyst.raw_score(features)

    assert not features.empty
    # 공매도 압력이 높을수록 점수가 낮다.
    assert scores["US:SPIKE"] < scores["US:CALM"], (
        f"평소 대비 급등을 못 잡았다: {scores.to_dict()}"
    )


def test_관측이_얇으면_평소를_말하지_않는다(store, analyst) -> None:  # type: ignore[no-untyped-def]
    """며칠짜리 표본으로 "평소" 를 말하면 그 편차는 잡음이다.

    실제로 2026-08-19 에 창고가 7세션뿐이라 이 가드가 걸렸다 — 빈 프레임을
    내는 것이 맞는 동작이고, 0 을 지어내는 것이 틀린 동작이다.
    """
    rows = []
    for offset in range(MIN_OBSERVATIONS - 1):
        day = NOW - timedelta(days=offset + 1)
        rows.append({
            "entity_id": "US:THIN", "valid_from": day, "observed_at": day,
            "source": "finra", "market": "US", "kind": "volume",
            "short_volume": 500_000.0, "short_exempt_volume": 1_000.0,
            "total_volume": 1_000_000.0,
            "short_position": None, "previous_short_position": None,
            "days_to_cover": None, "average_daily_volume": None,
        })
    store.append("short_flow", rows, ingest_run_id="thin", source="finra")

    assert analyst.features(NOW).empty


def test_잔고_계열은_일별_피처에_안_섞인다(store, analyst) -> None:  # type: ignore[no-untyped-def]
    """월 2회 잔고를 같은 창에 넣으면 발표 사이 구간에 같은 값이 반복되고,
    그게 "변화 없음" 이 아니라 "관측됨" 으로 읽힌다."""
    seed(store, {"US:AAA": [0.5] * SESSIONS})
    store.append(
        "short_flow",
        [{
            "entity_id": "US:BBB", "valid_from": NOW - timedelta(days=1),
            "observed_at": NOW - timedelta(days=1), "source": "finra",
            "market": "US", "kind": "interest",
            "short_volume": None, "short_exempt_volume": None, "total_volume": None,
            "short_position": 1_000.0, "previous_short_position": 900.0,
            "days_to_cover": 2.0, "average_daily_volume": 500.0,
        }],
        ingest_run_id="interest", source="finra",
    )

    panel = analyst._short_panel(NOW)

    assert panel is not None
    assert "US:BBB" not in panel["ratio"].columns, "잔고가 일별 계열에 섞였다"
