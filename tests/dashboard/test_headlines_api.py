"""중요 시황 API — 지금 시장에서 우리와 관련해 무슨 일이 벌어졌나.

여기서 고정하는 사실은 다섯이다.

1. **매수 차단만 있다** — News·SNS Analyst 는 매도 권한이 없다 (금지 사항)
2. **부피가 큰 통상 신고는 빠진다** — ownership·other·earnings 는 중요 공시 표에 안 든다
3. **events 가 0행이면 빈 리스트다** — 지어내지 않는다
4. **트리맵은 시장(KR/US)으로 묶는다** — sectors 는 업종이 아니라 KOSDAQ 소속부라
   KOSPI 가 전부 미상이다. 업종으로 묶지 않는다
5. **등락을 못 잰 종목은 null 이다** — 0% 로 채우면 "보합" 이라는 다른 사실이 된다
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from quant_rl_trading.dashboard import create_app
from quant_rl_trading.replay.clock import ReplayClock

NOW = datetime(2026, 8, 14, 7, 0, tzinfo=UTC)
YESTERDAY = NOW - timedelta(days=1)
ENTITY = "KR:001000"
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


@pytest.fixture
def desk(store):  # type: ignore[no-untyped-def]
    """매수 차단 1건(활성) · 만료 1건 · 중요 공시 1건 · 통상 신고 1건 ·
    시가총액 KR 2종목(등락 있음/없음) · US 1종목."""
    store.seed_config_defaults()

    store.append(
        "universe",
        [
            _row(ENTITY, NOW, market="KR", name="관리종목전자", is_listed=True,
                 is_tradable=True, delisted_on=None),
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
        # KR_A 는 어제·오늘 종가가 있어 등락이 잡힌다. KR_B 는 오늘 종가
        # 하나뿐이다 — 거래정지 등으로 등락을 못 재는 경우를 흉내낸다.
        [
            _row(KR_A, day, market="KR", open=c, high=c, low=c, close=c, volume=1.0, value=c)
            for day, c in ((YESTERDAY, 70_000.0), (NOW, 71_400.0))
        ]
        + [_row(KR_B, NOW, market="KR", open=5_000.0, high=5_000.0, low=5_000.0,
                close=5_000.0, volume=1.0, value=5_000.0)],
        ingest_run_id="prices",
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
    return create_app(store=desk, clock=ReplayClock(NOW)).test_client()


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
    client = create_app(store=store, clock=ReplayClock(NOW)).test_client()
    body = client.get("/api/headlines").get_json()
    assert body["data"] == {
        "verdicts": [],
        "active_verdicts": 0,
        "documents": [],
        "events": [],
        "treemap": {"groups": [], "top_n": 60},
    }


def test_as_of_에_타임존이_없으면_거부한다(client) -> None:
    assert client.get("/api/headlines?as_of=2026-08-14T07:00:00").status_code == 400


def test_트리맵은_시장별로_묶인다(client) -> None:
    body = client.get("/api/headlines").get_json()
    groups = {g["market"]: g["children"] for g in body["data"]["treemap"]["groups"]}
    assert set(groups) == {"KR", "US"}
    kr_ids = {row["entity_id"] for row in groups["KR"]}
    # ENTITY(관리종목전자) 는 market_stats·prices 가 없으므로 트리맵에 안 온다.
    assert kr_ids == {KR_A, KR_B}
    assert [row["entity_id"] for row in groups["US"]] == [US_A]


def test_등락을_못_잰_종목은_0이_아니라_null이다(client) -> None:
    body = client.get("/api/headlines").get_json()
    kr = {row["entity_id"]: row for row in body["data"]["treemap"]["groups"][0]["children"]}
    assert kr[KR_A]["change"] == pytest.approx(71_400.0 / 70_000.0 - 1.0)
    assert kr[KR_B]["change"] is None  # 오늘 종가 하나뿐 — 등락을 잴 수 없다


def test_상위_n이_응답에_같이_실린다(client) -> None:
    body = client.get("/api/headlines").get_json()
    assert body["data"]["treemap"]["top_n"] == 60
