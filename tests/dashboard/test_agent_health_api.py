"""Agent Health API.

이 화면의 존재 이유는 **검증되지 않은 Analyst 가 조용히 켜져 있는 일을
막는 것**이다. 그래서 여기서 고정하는 사실은 두 개다.

1. 가중치는 코드가 아니라 **측정 결과**에서만 나온다
2. 미측정 Analyst 를 명단에서 빼지 않는다 — 빠지면 "왜 없지" 를 아무도 묻지 않는다
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from quant_rl_trading.dashboard import create_app
from quant_rl_trading.dashboard.services.agent_health import PLANNED
from quant_rl_trading.replay.clock import ReplayClock

NOW = datetime(2026, 8, 11, tzinfo=UTC)
MEASURED_AT = NOW - timedelta(days=1)


def weight_row(analyst: str, ic: float, passed: bool, sample_days: int) -> dict[str, Any]:
    return {
        "entity_id": analyst,
        "valid_from": MEASURED_AT,
        "observed_at": MEASURED_AT,
        "source": "ic-measure",
        "analyst_version": f"{analyst}-v0.1.0",
        "weight": 1.0 if passed else 0.0,
        "ic": ic,
        "ic_threshold": 0.03,
        "sample_days": sample_days,
        "passed": passed,
        "market": "KR",
    }


@pytest.fixture
def seeded(store):  # type: ignore[no-untyped-def]
    store.seed_config_defaults()
    store.append(
        "analyst_weights",
        [
            weight_row("chart", 0.0412, True, 300),
            weight_row("risk", 0.0118, False, 300),
        ],
        ingest_run_id="ic-seed",
    )
    store.append(
        "signals",
        [
            {
                "entity_id": "KR:005930",
                "valid_from": NOW - timedelta(days=2),
                "observed_at": NOW - timedelta(days=2),
                "source": "analyst",
                "analyst": "chart",
                "analyst_version": "chart-v0.1.0",
                "score": 0.42,
                "confidence": 0.31,
                "horizon_days": 5,
                "features_hash": "abc123",
                "evidence_json": "[]",
                "latency_ms": 120.0,
            }
        ],
        ingest_run_id="signals-seed",
    )
    return store


@pytest.fixture
def client(seeded):  # type: ignore[no-untyped-def]
    app = create_app(store=seeded, clock=ReplayClock(NOW))
    app.config.update(TESTING=True)
    return app.test_client()


def body(response: Any) -> dict[str, Any]:
    return response.get_json()  # type: ignore[no-any-return]


def _by_name(client) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    return {item["analyst"]: item for item in body(client.get("/api/agent-health/roster"))["data"]}


# -- 가중치는 측정 결과에서만 나온다 -------------------------------------------


def test_failing_analyst_gets_zero_weight(client) -> None:
    """IC 합격선을 못 넘으면 가중치 0. 관찰 모드다."""
    roster = _by_name(client)

    assert roster["chart"]["passed"] is True
    assert roster["chart"]["weight"] == 1.0
    assert roster["risk"]["passed"] is False
    assert roster["risk"]["weight"] == 0.0


def test_unmeasured_analysts_stay_on_the_roster(client) -> None:
    """명단에서 빼면 '왜 없지' 를 아무도 묻지 않게 된다."""
    roster = _by_name(client)

    assert set(roster) == set(PLANNED)
    assert roster["flow_us"]["measured"] is False
    # 측정 전은 '모름' 이 아니라 '자격 없음' 이다.
    assert roster["flow_us"]["weight"] == 0.0
    assert roster["flow_us"]["ic"] is None


def test_summary_counts_the_m2_gate(client) -> None:
    """M2 완료 기준은 IC 통과 2명이다. 1명이면 경고가 떠야 한다."""
    data = body(client.get("/api/agent-health/summary"))["data"]

    assert data["total"] == len(PLANNED)
    assert data["passed"] == 1
    assert data["observing"] == len(PLANNED) - 1
    assert data["active_weight"] == 1.0
    assert any("M2 완료 기준" in text for text in data["warnings"])


# -- 시점 -----------------------------------------------------------------------


def test_measurement_is_invisible_before_it_happened(client) -> None:
    """어제 잰 IC 는 그저께 화면에 없어야 한다 (불변식 9)."""
    before = (MEASURED_AT - timedelta(hours=1)).isoformat()
    roster = {
        item["analyst"]: item
        for item in body(client.get(f"/api/agent-health/roster?as_of={before}"))["data"]
    }

    assert roster["chart"]["measured"] is False
    assert roster["chart"]["weight"] == 0.0


def test_latest_measurement_wins(seeded, client) -> None:
    """재측정하면 최신 결과가 현재 상태다. 옛 행은 지우지 않는다 (append-only)."""
    seeded.append(
        "analyst_weights",
        [{**weight_row("chart", 0.0091, False, 320), "valid_from": NOW, "observed_at": NOW}],
        ingest_run_id="ic-seed-2",
    )
    roster = _by_name(client)

    assert roster["chart"]["passed"] is False
    assert roster["chart"]["weight"] == 0.0

    # 이력은 두 점 다 남는다 — 알파 감쇠는 이 추이에서 먼저 보인다.
    history = body(client.get("/api/agent-health/ic-history"))["data"]
    chart_points = next(s for s in history["series"] if s["analyst"] == "chart")["points"]
    assert len(chart_points) == 2


# -- 기록 현황 -------------------------------------------------------------------


def test_signal_activity_is_reported(client) -> None:
    """관찰 모드여도 Signal 은 기록돼야 한다. 기록이 멈추면 켤 근거가 없다."""
    data = body(client.get("/api/agent-health/signals"))["data"]

    assert data["total"] == 1
    assert data["analysts"][0]["analyst"] == "chart"
    assert data["analysts"][0]["entities"] == 1


def test_empty_verdicts_are_reported_as_empty(client) -> None:
    """0 과 '모름' 은 다른 사실이다. 없는 것을 0으로 채우지 않는다."""
    data = body(client.get("/api/agent-health/verdicts"))["data"]

    assert data["blocks"] == 0
    assert data["by_category"] == []


def test_page_renders(client) -> None:
    response = client.get("/agent-health")

    assert response.status_code == 200
    assert b"Agent Health" in response.data
