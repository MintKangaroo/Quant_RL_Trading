"""트레이딩 API — 실제 회계·주문 위에서.

목업이 있던 자리다. 여기서 고정하는 사실은 넷이다.

1. **NAV 는 회계에서만 온다** — 화면이 자기 계산을 하지 않는다
2. **as_of 를 지킨다** — 되감으면 그 시점 이후 체결이 안 보인다 (불변식 9)
3. **없는 것은 null 이다** — 체결 지연·미체결 종목을 0 으로 채우지 않는다
4. **RL 이 아닌 것을 RL 처럼 그리지 않는다** — `rl_active` 는 M4 전까지 false
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from quant_rl_trading.dashboard import create_app
from quant_rl_trading.replay.clock import ReplayClock

NOW = datetime(2026, 8, 12, 6, 40, tzinfo=UTC)  # 한국시간 15:40
YESTERDAY = NOW - timedelta(days=1)
ENTITY = "KR:000100"
OTHER = "KR:000200"


def _row(entity: str, moment: datetime, **extra: Any) -> dict[str, Any]:
    return {
        "entity_id": entity,
        "valid_from": moment,
        "observed_at": moment,
        "source": "test",
        **extra,
    }


@pytest.fixture
def desk(store):  # type: ignore[no-untyped-def]
    """입금 1억 · 2종목 시세 · 체결 1건 · 주문 1건 · 스냅샷 2일."""
    store.seed_config_defaults()

    store.append(
        "fx",
        [_row("FX:USDKRW", day, rate=1_350.0) for day in (YESTERDAY, NOW)],
        ingest_run_id="fx",
    )
    store.append(
        "capital_flows",
        [_row("FUND", YESTERDAY, currency="KRW", amount=100_000_000.0, kind="deposit")],
        ingest_run_id="flow",
    )

    prices = []
    universe = []
    for index, day in enumerate((YESTERDAY, NOW)):
        for offset, entity in enumerate((ENTITY, OTHER)):
            close = 10_000.0 + index * 200 + offset * 5_000
            prices.append(
                _row(
                    entity, day, market="KR", open=close, high=close, low=close,
                    close=close, volume=100_000.0, value=close * 100_000.0,
                    adj_factor=None,
                )
            )
            universe.append(
                _row(
                    entity, day, market="KR", name=f"종목{offset}", is_listed=True,
                    is_tradable=True, delisted_on=None,
                )
            )
    store.append("prices", prices, ingest_run_id="prices")
    store.append("universe", universe, ingest_run_id="universe")

    store.append(
        "analyst_weights",
        [_row("risk", YESTERDAY, market="KR", ic=0.077, weight=1.0)],
        ingest_run_id="weights",
    )
    store.append(
        "signals",
        [
            _row(
                entity, NOW, analyst="risk", analyst_version="risk-v0.1.0",
                score=score, confidence=1.0, horizon_days=5, features_hash="x",
                evidence_json="[]", latency_ms=1.0,
            )
            for entity, score in ((ENTITY, 0.9), (OTHER, 0.4))
        ],
        ingest_run_id="signals",
    )

    # 어제 낸 주문이 오늘 체결됐다. 주문과 체결이 한 표에서 맞춰져야 한다.
    store.append(
        "orders",
        [
            _row(
                ENTITY, YESTERDAY, market="KR", session_id="KR-2026-08-11",
                slice_seq=0, side="buy", quantity=100.0, limit_price=10_050.0,
                target_weight=0.15, status="planned", reason="",
            )
        ],
        ingest_run_id="orders",
    )
    store.append(
        "trades",
        [
            _row(
                ENTITY, NOW, market="KR", side="buy", quantity=100.0, price=10_200.0,
                currency="KRW", fee=150.0, tax=0.0, order_id="KR-2026-08-11|KR:000100|buy",
            )
        ],
        ingest_run_id="trades",
    )
    store.append(
        "realized_weights",
        [
            _row(
                ENTITY, NOW, market="KR", session_id="KR-2026-08-12",
                target_weight=0.15, realized_weight=0.0102,
            )
        ],
        ingest_run_id="realized",
    )
    return store


@pytest.fixture
def client(desk):  # type: ignore[no-untyped-def]
    return create_app(store=desk, clock=ReplayClock(NOW)).test_client()


def test_nav_은_회계에서_온다(client) -> None:
    body = client.get("/api/trading").get_json()
    kpis = body["data"]["kpis"]

    # 1억 입금 - 매수대금 1,020,000 - 수수료 150 + 평가액 1,020,000
    assert kpis["nav"] == pytest.approx(100_000_000.0 - 150.0)
    assert kpis["positions"] == 1
    assert body["as_of"] == NOW.isoformat()


def test_주문과_체결이_한_행에서_맞춰진다(client) -> None:
    rows = client.get("/api/trading").get_json()["data"]["orders"]

    assert len(rows) == 1
    assert rows[0]["status"] == "filled"
    assert rows[0]["fill_price"] == pytest.approx(10_200.0)
    # **체결 지연은 실거래에서만 잰다.** 0 으로 채우면 "빠르다" 로 읽힌다.
    assert rows[0]["latency_ms"] is None


def test_rl_이_아닌_것을_rl_처럼_그리지_않는다(client) -> None:
    decision = client.get("/api/trading").get_json()["data"]["decision"]

    assert decision["rl_active"] is False
    assert "M4" in decision["engine_note"]
    assert decision["entity_id"] == ENTITY  # 합성 점수 최상위
    assert [item["analyst"] for item in decision["contributions"]] == ["risk"]
    # 목표와 실현이 벌어진 사실이 화면까지 온다 (불변식 7).
    assert decision["target_weight"] == pytest.approx(0.15)
    assert decision["realized_weight"] == pytest.approx(0.0102)


def test_되감으면_그_시점_이후_체결이_안_보인다(client) -> None:
    body = client.get(f"/api/trading?as_of={YESTERDAY.isoformat()}").get_json()

    # 어제 낸 주문은 어제도 보인다. **체결되지 않은 상태로** 보여야 한다 —
    # 오늘 체결을 어제 화면이 알고 있으면 그게 미래 훔쳐보기다.
    assert [row["status"] for row in body["data"]["orders"]] == ["planned"]
    assert body["data"]["orders"][0]["fill_price"] is None
    assert body["data"]["positions"] == []
    # 입금은 어제 있었으므로 자본은 그대로다.
    assert body["data"]["kpis"]["nav"] == pytest.approx(100_000_000.0)


def test_as_of_에_타임존이_없으면_거부한다(client) -> None:
    assert client.get("/api/trading?as_of=2026-08-12T15:40:00").status_code == 400


def test_차트는_종목을_요구한다(client) -> None:
    assert client.get("/api/trading/chart").status_code == 400

    body = client.get(f"/api/trading/chart?entity={ENTITY}").get_json()
    assert body["data"]["entity_id"] == ENTITY
    assert len(body["data"]["sessions"]) == 2
    # 우리 체결이 봉 위에 얹힌다.
    assert body["data"]["trades"][0]["side"] == "buy"


def test_리스크_임계치는_설정에서_온다(client) -> None:
    risk = client.get("/api/trading").get_json()["data"]["risk"]

    assert risk["bands"] == {"free": 0.12, "warn": 0.22, "hard": 0.30}
    assert risk["band"] == "free"
    assert risk["killswitch"]["engaged"] is False


def test_알_수_없는_시장은_거부한다(client) -> None:
    assert client.get("/api/trading?market=JP").status_code == 400


# -- EMERGENCY STOP ------------------------------------------------------------


def test_킬스위치는_이유를_요구한다(client) -> None:
    """이유 없는 발동·해제는 나중에 '왜 그랬나' 를 답할 수 없게 만든다."""
    assert client.post("/api/trading/killswitch", json={"action": "engage"}).status_code == 400
    assert client.post(
        "/api/trading/killswitch", json={"action": "engage", "reason": ""}
    ).status_code == 400
    assert client.post(
        "/api/trading/killswitch", json={"action": "무엇", "reason": "테스트"}
    ).status_code == 400


def test_킬스위치를_걸면_화면과_executor_가_같은_상태를_본다(client, desk) -> None:
    from quant_rl_trading.executor import guards

    response = client.post(
        "/api/trading/killswitch", json={"action": "engage", "reason": "손으로 정지"}
    )
    assert response.status_code == 200
    assert response.get_json()["data"]["state"] == "engaged"

    # **화면이 자기 상태를 따로 들지 않는다.** Executor 가 보는 것과 같아야 한다.
    assert not guards.check_killswitch(desk, as_of=NOW)
    body = client.get("/api/trading").get_json()
    assert body["data"]["risk"]["killswitch"]["engaged"] is True
    assert any(item["level"] == "critical" for item in body["data"]["alerts"])


def test_되감은_채로는_킬스위치를_못_건다(client) -> None:
    """과거 시점으로 발동하면 기록의 valid_from 이 과거가 된다."""
    response = client.post(
        f"/api/trading/killswitch?as_of={NOW.isoformat()}",
        json={"action": "engage", "reason": "테스트"},
    )
    assert response.status_code == 400


def test_모드가_창고에서_유도된다(client) -> None:
    """shadow 창고를 실전으로 착각하는 것이 가장 비싼 오해다."""
    system = client.get("/api/trading").get_json()["data"]["system"]
    assert system["mode"] in {"LIVE", "SHADOW", "BACKTEST"}
    assert system["store_root"]
