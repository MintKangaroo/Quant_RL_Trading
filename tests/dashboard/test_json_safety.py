"""응답에 ``NaN`` 이 실리지 않는다.

파이썬 ``json`` 은 기본으로 `NaN` 을 그대로 찍는다. 파이썬끼리는 되읽히므로
서버 테스트는 통과하는데, 브라우저 ``JSON.parse`` 는 거부한다:

    Unexpected token 'N', ..."ic":NaN,"passe"... is not valid JSON

**응답 하나가 통째로 죽는다.** 못 잰 IC 하나 때문에 화면 전체가 빈다.
창고에는 못 잰 값이 언제나 있으므로(0 으로 채우지 않는 것이 규칙이다) 이
검사는 특정 화면이 아니라 **직렬화 자체**를 겨눈다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from quant_rl_trading.dashboard.app import create_app
from quant_rl_trading.replay.clock import ReplayClock

NOW = datetime(2026, 8, 14, 6, 40, tzinfo=UTC)


@pytest.fixture
def client(store):  # type: ignore[no-untyped-def]
    store.seed_config_defaults()
    app = create_app(store=store, clock=ReplayClock(NOW))
    return app.test_client()


def test_nan_은_null_로_나간다(client) -> None:  # type: ignore[no-untyped-def]
    """못 잰 값은 ``null`` 이다. 0 으로 바꾸면 "쟀는데 0" 이 되어 거짓말이 된다."""
    app = client.application
    with app.app_context():
        raw = app.json.dumps({"ic": float("nan"), "sharpe": float("inf"), "ok": 1.5})
    assert json.loads(raw) == {"ic": None, "sharpe": None, "ok": 1.5}


def test_중첩된_구조도_훑는다(client) -> None:  # type: ignore[no-untyped-def]
    """실제 응답은 dict 안에 list 안에 dict 다. 한 겹만 보면 못 잡는다."""
    app = client.application
    with app.app_context():
        raw = app.json.dumps({"rows": [{"ic": float("nan")}, {"ic": 0.03}]})
    assert json.loads(raw) == {"rows": [{"ic": None}, {"ic": 0.03}]}


@pytest.mark.parametrize(
    "path",
    [
        "/api/data-quality/summary",
        "/api/agent-health/summary",
        "/api/agent-health/ic-history",
        "/api/trading",
        "/api/briefing/summary",
    ],
)
def test_엔드포인트_응답이_엄격한_JSON_이다(client, path: str) -> None:  # type: ignore[no-untyped-def]
    """브라우저와 같은 엄격도로 되읽는다 — 파이썬의 관대한 파서를 쓰지 않는다."""
    response = client.get(path)
    assert response.status_code == 200, response.data[:200]
    body = response.data.decode()
    assert "NaN" not in body and "Infinity" not in body
    json.loads(body)  # 실패하면 브라우저에서도 실패한다
