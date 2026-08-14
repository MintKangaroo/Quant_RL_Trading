"""중요 시황 API — 지금 시장에서 우리와 관련해 무슨 일이 벌어졌나.

여기서 고정하는 사실은 셋이다.

1. **매수 차단만 있다** — News·SNS Analyst 는 매도 권한이 없다 (금지 사항)
2. **부피가 큰 통상 신고는 빠진다** — ownership·other·earnings 는 중요 공시 표에 안 든다
3. **events 가 0행이면 빈 리스트다** — 지어내지 않는다
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from flask import Flask
from werkzeug.exceptions import HTTPException

from quant_rl_trading.dashboard.api import headlines as headlines_api
from quant_rl_trading.dashboard.app import SafeJSONProvider
from quant_rl_trading.replay.clock import Clock, ReplayClock
from quant_rl_trading.store import Store

NOW = datetime(2026, 8, 14, 7, 0, tzinfo=UTC)
YESTERDAY = NOW - timedelta(days=1)
ENTITY = "KR:001000"


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
    app.register_blueprint(headlines_api.bp)

    @app.errorhandler(HTTPException)
    def http_error(error: HTTPException) -> Any:
        return {"error": error.description, "status": error.code}, error.code

    return app


@pytest.fixture
def desk(store):  # type: ignore[no-untyped-def]
    """매수 차단 1건(활성) · 만료 1건 · 중요 공시 1건 · 통상 신고 1건."""
    store.seed_config_defaults()

    store.append(
        "universe",
        [
            _row(ENTITY, NOW, market="KR", name="관리종목전자", is_listed=True,
                 is_tradable=True, delisted_on=None),
        ],
        ingest_run_id="universe",
    )
    store.append(
        "verdicts",
        [
            _row(
                ENTITY, NOW, analyst="news", analyst_version="news-v0.2.0", decision="block",
                severity=1.0, category="delisting", reason="관리종목 지정 우려",
                expires_at=NOW + timedelta(days=5),
            ),
            _row(
                ENTITY, YESTERDAY, analyst="news", analyst_version="news-v0.2.0", decision="block",
                severity=0.5, category="litigation", reason="소송 제기 — 만료됨",
                expires_at=NOW - timedelta(hours=1),
            ),
        ],
        ingest_run_id="verdicts",
    )
    store.append(
        "documents",
        [
            _row(
                ENTITY, NOW, doc_id="D1", doc_type="dilution", title="유상증자 결정",
                filer="관리종목전자", url="https://dart.fss.or.kr/D1", raw_path=None,
            ),
            _row(
                ENTITY, NOW, doc_id="D2", doc_type="ownership", title="대량보유상황보고서",
                filer="관리종목전자", url="https://dart.fss.or.kr/D2", raw_path=None,
            ),
        ],
        ingest_run_id="documents",
    )
    return store


@pytest.fixture
def client(desk):  # type: ignore[no-untyped-def]
    return _build_app(store=desk, clock=ReplayClock(NOW)).test_client()


def test_매수_차단은_전부_block이다(client) -> None:
    body = client.get("/api/headlines").get_json()
    verdicts = body["data"]["verdicts"]
    assert {row["decision"] for row in verdicts} == {"block"}
    assert body["data"]["active_verdicts"] == 1


def test_살아있는_차단이_먼저_온다(client) -> None:
    body = client.get("/api/headlines").get_json()
    verdicts = body["data"]["verdicts"]
    assert verdicts[0]["active"] is True
    assert verdicts[0]["category"] == "delisting"
    assert verdicts[-1]["active"] is False


def test_통상_신고는_중요_공시에서_빠진다(client) -> None:
    body = client.get("/api/headlines").get_json()
    docs = body["data"]["documents"]
    assert [d["doc_id"] for d in docs] == ["D1"]
    assert docs[0]["doc_type"] == "dilution"
    assert docs[0]["entity_names"] == ["관리종목전자"]


def test_이벤트가_0행이면_빈_리스트다(client) -> None:
    body = client.get("/api/headlines").get_json()
    assert body["data"]["events"] == []


def test_없는_창고는_전부_빈_상태다(store) -> None:
    store.seed_config_defaults()
    client = _build_app(store=store, clock=ReplayClock(NOW)).test_client()
    body = client.get("/api/headlines").get_json()
    assert body["data"] == {
        "verdicts": [],
        "active_verdicts": 0,
        "documents": [],
        "events": [],
    }


def test_as_of_에_타임존이_없으면_거부한다(client) -> None:
    assert client.get("/api/headlines?as_of=2026-08-14T07:00:00").status_code == 400
