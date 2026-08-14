"""마켓 API — 지금 시장이 어떤 상태인가.

여기서 고정하는 사실은 셋이다.

1. **as_of 를 지킨다** — 되감으면 그 시점 이후 값이 안 보인다 (불변식 9)
2. **없는 것은 null 이다** — 환율·지수가 없으면 0 이 아니라 null
3. **예정된 거시지표는 여기 없다** — 발표 완료만. 예정은 뉴스·일정 탭의 몫이다
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from flask import Flask
from werkzeug.exceptions import HTTPException

from quant_rl_trading.dashboard.api import market as market_api
from quant_rl_trading.dashboard.app import SafeJSONProvider
from quant_rl_trading.replay.clock import Clock, ReplayClock
from quant_rl_trading.store import Store

NOW = datetime(2026, 8, 12, 6, 40, tzinfo=UTC)
YESTERDAY = NOW - timedelta(days=1)
KR_A, KR_B = "KR:000100", "KR:000200"
US_A = "US:AAPL"


def _row(entity: str, moment: datetime, **extra: Any) -> dict[str, Any]:
    return {
        "entity_id": entity,
        "valid_from": moment,
        "observed_at": moment,
        "source": "test",
        **extra,
    }


def _build_app(store: Store, clock: Clock) -> Flask:
    """``create_app`` 을 안 쓴다. app.py 는 team lead 가 배선한다 — 이 블루프린트는
    아직 등록되지 않았다. 배선 전에도 API 계약을 검증할 수 있어야 하므로
    최소한의 앱을 여기서 만든다. app.py 는 건드리지 않는다."""
    app = Flask(__name__)
    app.json = SafeJSONProvider(app)
    app.config["QUANT_RL_STORE"] = store
    app.config["QUANT_RL_CLOCK"] = clock
    app.json.ensure_ascii = False
    app.register_blueprint(market_api.bp)

    @app.errorhandler(HTTPException)
    def http_error(error: HTTPException) -> Any:
        return {"error": error.description, "status": error.code}, error.code

    return app


@pytest.fixture
def desk(store):  # type: ignore[no-untyped-def]
    """지수 2종 · 환율 · 시총 상위 KR/US 각 1 · 거시지표 발표완료 1건 + 예정 1건."""
    store.seed_config_defaults()

    store.append(
        "fx",
        [_row("FX:USDKRW", day, rate=rate) for day, rate in ((YESTERDAY, 1_400.0), (NOW, 1_420.0))],
        ingest_run_id="fx",
    )
    store.append(
        "indices",
        [
            _row("KR:IDX:KRX TMI", day, market="KR", board="KRX", close=close)
            for day, close in ((YESTERDAY, 1000.0), (NOW, 1010.0))
        ]
        + [
            _row("US:IDX:SP500", day, market="US", board="NYSE", close=close)
            for day, close in ((YESTERDAY, 5000.0), (NOW, 4950.0))
        ],
        ingest_run_id="indices",
    )
    store.append(
        "universe",
        [
            _row(KR_A, NOW, market="KR", name="가나전자", is_listed=True,
                 is_tradable=True, delisted_on=None),
            _row(KR_B, NOW, market="KR", name="다라상사", is_listed=True,
                 is_tradable=True, delisted_on=None),
            _row(US_A, NOW, market="US", name="애플", is_listed=True,
                 is_tradable=True, delisted_on=None),
        ],
        ingest_run_id="universe",
    )
    store.append(
        "market_stats",
        [
            _row(KR_A, NOW, market="KR", metric="market_cap", value=5_000_000_000_000.0),
            _row(KR_B, NOW, market="KR", metric="market_cap", value=1_000_000_000_000.0),
            _row(US_A, NOW, market="US", metric="market_cap", value=3_000_000_000_000.0),
        ],
        ingest_run_id="stats",
    )
    store.append(
        "prices",
        [
            _row(KR_A, day, market="KR", open=c, high=c, low=c, close=c, volume=1.0, value=c)
            for day, c in ((YESTERDAY, 70_000.0), (NOW, 71_400.0))
        ],
        ingest_run_id="prices",
    )
    store.append(
        "macro_releases",
        [
            _row(
                "KR:CPI", NOW, market="KR", indicator="CPI", release_name="소비자물가지수",
                scheduled_at=YESTERDAY, actual=119.8, previous=119.6, unit="2020=100",
                status="released",
            ),
            _row(
                "US:CPI", NOW, market="US", indicator="CPI", release_name="Consumer Price Index",
                scheduled_at=NOW + timedelta(days=5), actual=None, previous=310.0, unit="index",
                status="scheduled",
            ),
        ],
        ingest_run_id="macro",
    )
    return store


@pytest.fixture
def client(desk):  # type: ignore[no-untyped-def]
    return _build_app(store=desk, clock=ReplayClock(NOW)).test_client()


def test_지수는_직전_세션_대비_등락을_잰다(client) -> None:
    body = client.get("/api/market").get_json()
    highlights = {row["entity_id"]: row for row in body["data"]["indices"]["highlights"]}
    assert highlights["KR:IDX:KRX TMI"]["close"] == 1010.0
    assert highlights["KR:IDX:KRX TMI"]["change"] == pytest.approx(0.01)
    assert highlights["US:IDX:SP500"]["change"] == pytest.approx(-0.01)


def test_환율에는_시계열과_등락이_같이_온다(client) -> None:
    body = client.get("/api/market").get_json()
    fx = body["data"]["fx"]
    assert fx["rate"] == 1_420.0
    assert fx["change"] == pytest.approx(1_420.0 / 1_400.0 - 1.0)
    assert len(fx["sessions"]) == len(fx["rates"]) == 2


def test_시가총액_상위가_시장별로_갈린다(client) -> None:
    body = client.get("/api/market").get_json()
    leaders = body["data"]["leaders"]
    kr_ids = [row["entity_id"] for row in leaders["KR"]]
    us_ids = [row["entity_id"] for row in leaders["US"]]
    assert kr_ids == [KR_A, KR_B]  # 시총 내림차순
    assert us_ids == [US_A]
    assert leaders["KR"][0]["name"] == "가나전자"
    assert leaders["KR"][0]["change"] == pytest.approx(71_400.0 / 70_000.0 - 1.0)


def test_예정된_거시지표는_빠진다(client) -> None:
    body = client.get("/api/market").get_json()
    macro = body["data"]["macro"]
    assert [row["indicator"] for row in macro] == ["CPI"]
    assert macro[0]["market"] == "KR"


def test_없는_데이터는_0이_아니라_null이다(store) -> None:
    store.seed_config_defaults()
    client = _build_app(store=store, clock=ReplayClock(NOW)).test_client()
    body = client.get("/api/market").get_json()
    assert body["data"]["fx"]["rate"] is None
    assert body["data"]["indices"]["highlights"] == []
    assert body["data"]["leaders"]["KR"] == []
    assert body["data"]["macro"] == []


def test_되감으면_그_시점_이후_값이_안_보인다(desk) -> None:
    client = _build_app(store=desk, clock=ReplayClock(NOW)).test_client()
    body = client.get(f"/api/market?as_of={YESTERDAY.isoformat()}").get_json()
    fx = body["data"]["fx"]
    assert fx["rate"] == 1_400.0  # NOW 시점 값은 아직 안 보인다


def test_as_of_에_타임존이_없으면_거부한다(client) -> None:
    response = client.get("/api/market?as_of=2026-08-12T06:40:00")
    assert response.status_code == 400
