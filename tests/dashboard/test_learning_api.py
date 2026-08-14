"""학습 탭 API.

이 화면의 존재 이유는 두 개다.

1. **없는 것을 지어내지 않는다.** RL(M4)은 아직 없다 — ``/status`` 가 그
   사실을 명시적으로 돌려줘야 하고, 값이 0 이나 가짜 숫자로 채워지면 안 된다.
2. **있는 것은 정확히 보여준다.** Analyst IC 게이트는 Agent Health 화면과
   같은 사실이어야 한다 — 계산을 두 번 하면 언젠가 두 화면이 어긋난다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from flask import Flask

from quant_rl_trading.dashboard.api import learning as learning_api
from quant_rl_trading.dashboard.app import SafeJSONProvider
from quant_rl_trading.dashboard.services.agent_health import PLANNED
from quant_rl_trading.replay.clock import ReplayClock

NOW = datetime(2026, 8, 14, tzinfo=UTC)
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


def make_app(store: Any, clock: Any) -> Flask:
    """app.py 를 건드리지 않고 이 탭의 블루프린트만 단 최소 앱.

    실제 배선(app.py 에 register_blueprint)은 별도로 이뤄진다 — 여기서는
    API 자체의 계약만 검증한다.
    """
    app = Flask(__name__)
    app.json = SafeJSONProvider(app)
    app.config["QUANT_RL_STORE"] = store
    app.config["QUANT_RL_CLOCK"] = clock
    app.json.ensure_ascii = False  # type: ignore[attr-defined]
    app.register_blueprint(learning_api.bp)
    app.config.update(TESTING=True)
    return app


@pytest.fixture
def seeded(store):  # type: ignore[no-untyped-def]
    store.seed_config_defaults()
    store.append(
        "analyst_weights",
        [
            weight_row("risk", 0.0777, True, 300),
            weight_row("chart", 0.0014, False, 300),
        ],
        ingest_run_id="ic-seed",
    )
    return store


@pytest.fixture
def client(seeded):  # type: ignore[no-untyped-def]
    return make_app(seeded, ReplayClock(NOW)).test_client()


def body(response: Any) -> dict[str, Any]:
    return response.get_json()  # type: ignore[no-any-return]


# -- 없는 것은 지어내지 않는다 ---------------------------------------------------


def test_m4_status_says_it_is_not_active(client) -> None:
    """RL(M4)은 아직 없다. 화면이 그 사실을 명시적으로 말해야 한다."""
    data = body(client.get("/api/learning/status"))["data"]

    assert data["active"] is False
    assert data["milestone"] == "M4"
    assert "M4" in data["note"]
    # 자리는 지키되(§5 의 위젯 6개) 값은 없다.
    assert len(data["widgets"]) == 6
    assert all("key" in w and "label" in w for w in data["widgets"])


def test_status_accepts_as_of_even_though_answer_is_static(client) -> None:
    """불변식 9 — 답이 시점에 안 달라져도 파라미터는 받아야 한다."""
    past = (MEASURED_AT - timedelta(days=100)).isoformat()
    response = client.get(f"/api/learning/status?as_of={past}")

    assert response.status_code == 200
    assert body(response)["as_of"] == past


# -- 있는 것은 정확히 보여준다 ---------------------------------------------------


def test_gate_matches_agent_health_facts(client) -> None:
    """가중치 게이트는 agent_health 와 같은 사실이어야 한다."""
    data = body(client.get("/api/learning/gate"))["data"]

    assert data["total"] == len(PLANNED)
    assert data["active"] == ["risk"]
    assert data["active_count"] == 1
    assert data["measured_count"] == 2
    assert data["active_weight"] == 1.0

    roster = {item["analyst"]: item for item in data["roster"]}
    assert roster["risk"]["passed"] is True
    assert roster["chart"]["passed"] is False
    assert roster["flow_us"]["measured"] is False


def test_ic_history_carries_threshold_from_config(client) -> None:
    """합격선은 코드가 아니라 store.config 에서 온다 (불변식 10)."""
    data = body(client.get("/api/learning/ic-history"))["data"]

    assert data["threshold"] == pytest.approx(0.03)
    assert data["points"] == 2
    analysts = {series["analyst"] for series in data["series"]}
    assert analysts == {"risk", "chart"}


def test_ic_history_is_empty_not_fabricated_when_nothing_measured(seeded) -> None:
    """측정이 없으면 빈 배열이지 0 으로 채운 계열이 아니다."""
    empty_store_app = make_app(seeded, ReplayClock(MEASURED_AT - timedelta(days=200)))
    client = empty_store_app.test_client()

    data = body(client.get("/api/learning/ic-history"))["data"]

    assert data["series"] == []
    assert data["points"] == 0


def test_every_route_returns_the_as_of_it_was_given(client) -> None:
    """불변식 9 — 이 탭의 모든 GET 라우트가 as_of 를 받고 되돌려준다."""
    past = MEASURED_AT.isoformat()
    for path in ("/api/learning/status", "/api/learning/gate", "/api/learning/ic-history"):
        response = client.get(f"{path}?as_of={past}")
        assert response.status_code == 200, path
        assert body(response)["as_of"] == past, path
