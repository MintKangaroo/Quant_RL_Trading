"""마켓 API — 지금 시장이 어떤 상태인가.

화면은 **왼쪽 국장 · 오른쪽 미장**이고, 응답도 시장별로 같은 모양의 판 둘이다.
여기서 고정하는 사실은 이렇다.

1. **as_of 를 지킨다** — 되감으면 그 시점 이후 값이 안 보인다 (불변식 9)
2. **없는 것은 null 이다** — 환율·지수가 없으면 0 이 아니라 null
3. **대표 지수는 config 가 정한다** — 화면이 고르지 않는다 (불변식 10).
   그 이름이 창고에 없으면 대용치로 바꿔치기하지 않고 ``index_panels.missing``
   으로 나간다. 지수마다 패널 하나이고 값은 **원 종가**다 — 정규화하지 않는다
4. **KR·US 판은 같은 모양이다** — 한쪽에만 있는 칸을 만들지 않는다
5. **예정된 거시지표는 여기 없다** — 발표 완료만. 예정은 뉴스·일정 탭의 몫이다
6. **등락을 못 잰 종목은 null 이다** — 0% 로 채우면 "보합" 이라는 다른 사실이
   된다. 시장 폭의 보합 칸에도 넣지 않는다
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
US_A, US_B = "US:AAPL", "US:MSFT"


def _row(entity: str, moment: datetime, **extra: Any) -> dict[str, Any]:
    return {
        "entity_id": entity,
        "valid_from": moment,
        "observed_at": moment,
        "source": "test",
        **extra,
    }


def _price(
    entity: str, moment: datetime, market: str, close: float, value: float
) -> dict[str, Any]:
    return _row(
        entity, moment, market=market, open=close, high=close, low=close,
        close=close, volume=1.0, value=value,
    )


def _build_app(store: Store, clock: Clock) -> Flask:
    """``create_app`` 을 안 쓴다 — 이 블루프린트 하나의 계약만 본다.
    앱 전체를 세우면 다른 탭의 배선 상태가 이 테스트를 흔든다."""
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
    """국장·미장 각각 지수·시세·유니버스 + 국장만 시총 + 시장별 거시지표."""
    store.seed_config_defaults()

    store.append(
        "fx",
        [_row("FX:USDKRW", day, rate=rate) for day, rate in ((YESTERDAY, 1_400.0), (NOW, 1_420.0))],
        ingest_run_id="fx",
    )
    store.append(
        "indices",
        [
            # config.benchmark.kr_index 가 가리키는 이름이다 — 대표 카드가 된다.
            _row("KR:IDX:KOSPI", day, market="KR", board="KOSPI", close=close)
            for day, close in ((YESTERDAY, 1000.0), (NOW, 1010.0))
        ]
        + [
            _row("KR:IDX:KRX 반도체", day, market="KR", board="KRX", close=close)
            for day, close in ((YESTERDAY, 500.0), (NOW, 520.0))
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
            _row(US_A, NOW, market="US", name="AAPL", is_listed=True,
                 is_tradable=True, delisted_on=None),
            _row(US_B, NOW, market="US", name="MSFT", is_listed=True,
                 is_tradable=True, delisted_on=None),
        ],
        ingest_run_id="universe",
    )
    # 시총은 국장만 있다 — 미장 상장주식수를 받는 수집기가 아직 없다.
    store.append(
        "market_stats",
        [
            _row(KR_A, NOW, market="KR", metric="market_cap", value=5_000_000_000_000.0),
            _row(KR_B, NOW, market="KR", metric="market_cap", value=1_000_000_000_000.0),
        ],
        ingest_run_id="stats",
    )
    store.append(
        "prices",
        [
            # 국장: KR_A 는 올랐고, KR_B 는 오늘 종가가 아예 없다(등락 미측정).
            _price(KR_A, YESTERDAY, "KR", 70_000.0, 1e10),
            _price(KR_A, NOW, "KR", 71_400.0, 1.2e10),
            _price(KR_B, YESTERDAY, "KR", 10_000.0, 1e9),
            # 미장: 하나는 오르고 하나는 내린다 — 시장 폭이 1승 1패다.
            _price(US_A, YESTERDAY, "US", 200.0, 1e10),
            _price(US_A, NOW, "US", 210.0, 1.1e10),
            _price(US_B, YESTERDAY, "US", 400.0, 9e9),
            _price(US_B, NOW, "US", 380.0, 8e9),
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
                "US:PPI", NOW, market="US", indicator="PPI", release_name="Producer Price Index",
                scheduled_at=YESTERDAY, actual=145.2, previous=144.9, unit="index",
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


def test_두_시장이_같은_모양의_판을_받는다(client) -> None:
    markets = client.get("/api/market").get_json()["data"]["markets"]
    assert set(markets) == {"KR", "US"}
    assert set(markets["KR"]) == set(markets["US"])


def _headline(panel: dict) -> dict:
    """그 시장의 대표 지수 패널. 대표는 **하나뿐이어야** 한다."""
    heads = [
        row
        for row in panel["index_panels"]["panels"] + panel["index_panels"]["missing"]
        if row["role"] == "headline"
    ]
    assert len(heads) == 1, heads
    return heads[0]


def test_대표_지수는_config_가_정한다(client) -> None:
    markets = client.get("/api/market").get_json()["data"]["markets"]
    # config.benchmark 의 kr_index / us_index 그대로다.
    assert _headline(markets["KR"])["entity_id"] == "KR:IDX:KOSPI"
    assert _headline(markets["US"])["entity_id"] == "US:IDX:SP500"
    assert _headline(markets["KR"])["change"] == pytest.approx(0.01)
    assert _headline(markets["US"])["change"] == pytest.approx(-0.01)


def test_지수_패널은_원_종가를_싣는다(client) -> None:
    """정규화는 축을 공유해야 할 때만 필요했던 트릭이었다. 패널이 갈리면서
    사라졌다 — 100 에서 출발하는 값이 있으면 옛 동작이 남은 것이다."""
    head = _headline(client.get("/api/market").get_json()["data"]["markets"]["KR"])
    assert head["closes"][-1] == head["close"]
    assert head["close"] > 100.0  # 지수 포인트이지 정규화값이 아니다


def test_대표_지수가_없으면_대용치로_바꿔치기하지_않는다(store) -> None:
    """대표 지수가 창고에 없으면 다른 지수를 그 이름으로 부르지 않는다.

    빈 창고라 코스피가 없다. 여기서 KRX 300 을 코스피라 부르면 화면이
    거짓말을 한다 — 없으면 없다고 하고 **무엇이 없는지**를 말한다.
    """
    store.seed_config_defaults()
    client = _build_app(store=store, clock=ReplayClock(NOW)).test_client()
    kr = client.get("/api/market").get_json()["data"]["markets"]["KR"]
    head = _headline(kr)
    assert head in kr["index_panels"]["missing"]  # 값이 아니라 "없음" 으로 나간다
    assert head["entity_id"] == "KR:IDX:KOSPI"  # 무엇이 없는지는 말한다


def test_패널로_세운_지수는_나머지_목록에_다시_안_나온다(client) -> None:
    """같은 지수를 한 칸에 두 번 적으면 목록의 "N종" 이 무엇을 세는지 흐려진다."""
    kr = client.get("/api/market").get_json()["data"]["markets"]["KR"]["indices"]
    assert [row["entity_id"] for row in kr["others"]] == ["KR:IDX:KRX 반도체"]
    assert kr["total"] == 2  # 접은 것이지 지운 것이 아니다
    assert "KR:IDX:KOSPI" in kr["excluded"]  # 어디로 갔는지 화면이 말할 수 있다


def test_지수는_시장별로_갈린다(client) -> None:
    markets = client.get("/api/market").get_json()["data"]["markets"]
    assert markets["US"]["indices"]["total"] == 1
    assert markets["US"]["indices"]["others"] == []


def test_환율에는_시계열과_등락이_같이_온다(client) -> None:
    fx = client.get("/api/market").get_json()["data"]["fx"]
    assert fx["rate"] == 1_420.0
    assert fx["change"] == pytest.approx(1_420.0 / 1_400.0 - 1.0)
    assert len(fx["sessions"]) == len(fx["rates"]) == 2


def test_시장_폭은_시세만으로_만들어진다(client) -> None:
    """미장에 시총이 없어도 이 패널은 국장과 같은 밀도로 찬다."""
    us = client.get("/api/market").get_json()["data"]["markets"]["US"]["breadth"]
    assert (us["advancers"], us["decliners"], us["unchanged"]) == (1, 1, 0)
    assert us["traded"] == 2
    assert us["currency"] == "USD"
    assert us["value"] == pytest.approx(1.1e10 + 8e9)


def test_등락을_못_잰_종목은_보합이_아니다(client) -> None:
    kr = client.get("/api/market").get_json()["data"]["markets"]["KR"]["breadth"]
    # KR_B 는 오늘 종가가 없다 — 어제 종가만 잡히므로 등락을 못 잰다.
    assert kr["advancers"] == 1
    assert kr["unchanged"] == 0
    assert kr["unmeasured"] == 1


def test_많이_움직인_종목이_시장별로_온다(client) -> None:
    markets = client.get("/api/market").get_json()["data"]["markets"]
    us = markets["US"]["movers"]
    assert next(row["entity_id"] for row in us["gainers"]) == US_A
    assert next(row["entity_id"] for row in us["losers"]) == US_B
    assert us["actives"][0]["entity_id"] == US_A  # 거래대금 1위
    assert us["gainers"][0]["change"] == pytest.approx(210.0 / 200.0 - 1.0)


def test_시가총액은_국장만_찬다(client) -> None:
    """미장 시총이 빈 것은 조인 버그가 아니라 상장주식수 수집기가 없어서다."""
    markets = client.get("/api/market").get_json()["data"]["markets"]
    assert [row["entity_id"] for row in markets["KR"]["leaders"]] == [KR_A, KR_B]
    assert markets["KR"]["leaders"][0]["name"] == "가나전자"
    assert markets["KR"]["leaders"][0]["change"] == pytest.approx(71_400.0 / 70_000.0 - 1.0)
    assert markets["US"]["leaders"] == []
    assert markets["US"]["treemap"]["rows"] == []


def test_트리맵도_등락을_못_잰_종목은_null이다(client) -> None:
    rows = client.get("/api/market").get_json()["data"]["markets"]["KR"]["treemap"]["rows"]
    by_entity = {row["entity_id"]: row for row in rows}
    assert by_entity[KR_A]["change"] == pytest.approx(71_400.0 / 70_000.0 - 1.0)
    assert by_entity[KR_B]["change"] is None  # 오늘 종가가 아예 없다


def test_트리맵_상위_n이_응답에_같이_실린다(client) -> None:
    markets = client.get("/api/market").get_json()["data"]["markets"]
    assert markets["KR"]["treemap"]["top_n"] == 60
    assert markets["US"]["treemap"]["top_n"] == 60


def test_거시지표가_시장별로_갈리고_예정은_빠진다(client) -> None:
    markets = client.get("/api/market").get_json()["data"]["markets"]
    assert [row["indicator"] for row in markets["KR"]["macro"]] == ["CPI"]
    # US:CPI 는 예정이라 빠진다 — 발표 완료만 본다.
    assert [row["indicator"] for row in markets["US"]["macro"]] == ["PPI"]


def test_가격지수_배지를_위한_플래그가_실린다(client) -> None:
    """총수익지수를 못 구해 배당만큼 우리가 유리하게 보인다 — 화면이 그걸 말한다."""
    assert client.get("/api/market").get_json()["data"]["total_return"] is False


def test_없는_데이터는_0이_아니라_null이다(store) -> None:
    store.seed_config_defaults()
    client = _build_app(store=store, clock=ReplayClock(NOW)).test_client()
    data = client.get("/api/market").get_json()["data"]
    assert data["fx"]["rate"] is None
    for code in ("KR", "US"):
        panel = data["markets"][code]
        assert panel["index_panels"]["panels"] == []
        assert panel["index_panels"]["missing"]  # 무엇이 없는지는 말한다
        assert panel["leaders"] == []
        assert panel["macro"] == []
        assert panel["breadth"]["value"] is None
        assert panel["breadth"]["traded"] == 0


def test_되감으면_그_시점_이후_값이_안_보인다(desk) -> None:
    client = _build_app(store=desk, clock=ReplayClock(NOW)).test_client()
    body = client.get(f"/api/market?as_of={YESTERDAY.isoformat()}").get_json()
    assert body["data"]["fx"]["rate"] == 1_400.0  # NOW 시점 값은 아직 안 보인다


def test_as_of_에_타임존이_없으면_거부한다(client) -> None:
    response = client.get("/api/market?as_of=2026-08-12T06:40:00")
    assert response.status_code == 400
