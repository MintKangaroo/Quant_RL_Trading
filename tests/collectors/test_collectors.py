"""수집자 — 정규화, 원본 보존, 지연 실측.

가장 중요한 것은 ``observed_at`` 이 **수집 시각**이라는 것이다. 봉의 날짜를
관측시각으로 쓰면 그날 자정부터 그날 종가를 알고 있던 것이 되고, 백테스트
전체가 하루씩 미래를 본다.
"""

from __future__ import annotations

import json

import httpx
import pytest

from quant_rl_trading.collectors.ls_client import LSClient, LSCredentials
from quant_rl_trading.collectors.market_collector import (
    SUJUNG_RAW,
    MarketCollector,
    normalize_master,
    normalize_ohlcv,
)
from quant_rl_trading.collectors.market_hours import Market, is_regular_session, is_trading_day
from quant_rl_trading.collectors.raw import RawArchive
from quant_rl_trading.replay.clock import ReplayClock

BARS = [
    {"date": "20240304", "open": "70000", "high": "71000", "low": "69500", "close": "70500",
     "jdiff_vol": "12,345,678"},
    {"date": "20240305", "open": "70500", "high": "72000", "low": "70000", "close": "71800",
     "jdiff_vol": "9,000,000"},
    {"date": "", "close": "-"},  # 쓰레기 행은 버린다
]

MASTER = [
    {"shcode": "005930", "hname": "삼성전자", "etfgubun": "0", "spac_gubun": "N"},
    {"shcode": "069500", "hname": "KODEX 200", "etfgubun": "1", "spac_gubun": "N"},
    {"shcode": "", "hname": "빈 코드"},
]

# -- 정규화 -------------------------------------------------------------------


def test_observed_at_is_collection_time_not_bar_date(ts) -> None:  # type: ignore[no-untyped-def]
    collected = ts(2024, 3, 5, 16)
    rows = normalize_ohlcv(BARS, entity_id="KR:005930", market=Market.KR, observed_at=collected)

    assert [row["observed_at"] for row in rows] == [collected, collected]
    assert rows[0]["valid_from"] == ts(2024, 3, 4)


def test_normalize_parses_comma_separated_volume(ts) -> None:  # type: ignore[no-untyped-def]
    rows = normalize_ohlcv(
        BARS, entity_id="KR:005930", market=Market.KR, observed_at=ts(2024, 3, 5)
    )

    assert rows[0]["volume"] == 12_345_678.0
    assert rows[0]["close"] == 70_500.0


def test_normalize_drops_malformed_rows(ts) -> None:  # type: ignore[no-untyped-def]
    rows = normalize_ohlcv(
        BARS, entity_id="KR:005930", market=Market.KR, observed_at=ts(2024, 3, 5)
    )

    assert len(rows) == 2


def test_adjusted_prices_are_never_requested() -> None:
    """수정주가에는 미래의 분할·증자가 반영돼 있다. 원주가만 받는다."""
    assert SUJUNG_RAW == "N"


def test_master_marks_etf_as_not_tradable(ts) -> None:  # type: ignore[no-untyped-def]
    rows = normalize_master(
        MASTER, market=Market.KR, trading_day=ts(2024, 3, 5), observed_at=ts(2024, 3, 5, 8)
    )

    tradable = {row["entity_id"]: row["is_tradable"] for row in rows}

    assert tradable == {"KR:005930": True, "KR:069500": False}


# -- 수집 파이프라인 -----------------------------------------------------------


@pytest.fixture
def collector(store, tmp_path, ts):  # type: ignore[no-untyped-def]
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(
                200, json={"access_token": "t", "token_type": "Bearer", "expires_in": 86400}
            )
        body = json.loads(request.content)
        if "t8410InBlock" in body:
            return httpx.Response(200, json={"rsp_cd": "00000", "t8410OutBlock1": BARS})
        return httpx.Response(200, json={"rsp_cd": "00000", "t8436OutBlock": MASTER})

    clock = ReplayClock(ts(2024, 3, 5, 16))
    return MarketCollector(
        store=store,
        client=LSClient(
            credentials=LSCredentials("key", "secret", "https://api.test"),
            clock=clock,
            transport=httpx.MockTransport(handler),
            live_trading=True,
            sleep=lambda _: None,
        ),
        clock=clock,
        archive=RawArchive(root=tmp_path / "data"),
        market=Market.KR,
    )


def test_collected_bars_are_readable_through_the_gate(collector, store, ts) -> None:  # type: ignore[no-untyped-def]
    collector.collect_ohlcv("005930", ingest_run_id="run-1")

    seen = store.get("prices", as_of=ts(2024, 3, 5, 18))

    assert list(seen["close"]) == [70_500.0, 71_800.0]


def test_collected_bars_are_invisible_before_collection(collector, store, ts) -> None:  # type: ignore[no-untyped-def]
    """3월 4일 봉을 3월 5일 16시에 수집했다면, 3월 5일 09시의 나는 몰랐다."""
    collector.collect_ohlcv("005930", ingest_run_id="run-1")

    assert store.get("prices", as_of=ts(2024, 3, 5, 9)).empty


def test_raw_response_is_preserved(collector, tmp_path, ts) -> None:  # type: ignore[no-untyped-def]
    """정규화에 버그를 발견해도 원본이 있으면 다시 만들 수 있다."""
    collector.collect_ohlcv("005930", ingest_run_id="run-1")

    files = list((tmp_path / "data" / "raw" / "ls_api" / "date=2024-03-05").glob("*.json"))

    assert len(files) == 1
    saved = json.loads(files[0].read_text(encoding="utf-8"))
    assert saved["payload"]["t8410OutBlock1"] == BARS


def test_latency_is_measured_per_stage(collector, store, ts) -> None:  # type: ignore[no-untyped-def]
    collector.collect_ohlcv("005930", ingest_run_id="run-1")

    latency = store.get("ingest_latency", as_of=ts(2024, 3, 5, 18))

    assert set(latency["stage"]) == {"fetch", "archive", "normalize", "append"}
    assert all(latency["ok"])


def test_universe_snapshot_is_written(collector, store, ts) -> None:  # type: ignore[no-untyped-def]
    collector.collect_universe(ingest_run_id="universe-1")

    seen = store.get("universe", as_of=ts(2024, 3, 5, 18))

    assert set(seen["entity_id"]) == {"KR:005930", "KR:069500"}


# -- 장 운영시간 ---------------------------------------------------------------


def test_korean_holiday_is_not_a_trading_day() -> None:
    """수작업 휴장일 파일은 갱신을 잊으면 조용히 틀린다. 라이브러리로 조회한다."""
    from datetime import date

    assert not is_trading_day(Market.KR, date(2024, 3, 1))  # 삼일절
    assert is_trading_day(Market.KR, date(2024, 3, 4))


def test_us_holiday_is_not_a_trading_day() -> None:
    from datetime import date

    assert not is_trading_day(Market.US, date(2024, 7, 4))
    assert is_trading_day(Market.US, date(2024, 7, 3))


def test_regular_session_is_local_time_aware(ts) -> None:  # type: ignore[no-untyped-def]
    # 2024-03-04 05:00 UTC = 14:00 KST → 정규장
    assert is_regular_session(Market.KR, ts(2024, 3, 4, 5))
    # 2024-03-04 08:00 UTC = 17:00 KST → 마감 후
    assert not is_regular_session(Market.KR, ts(2024, 3, 4, 8))


def test_dst_is_handled_by_the_library(ts) -> None:  # type: ignore[no-untyped-def]
    """서머타임을 손으로 계산하지 않는다.

    14:30 UTC 는 겨울(EST)엔 09:30 ET 로 개장이지만, 여름(EDT)엔 10:30 ET 다.
    """
    assert is_regular_session(Market.US, ts(2024, 1, 3, 14, 30))
    assert is_regular_session(Market.US, ts(2024, 7, 3, 14, 30))
    # 13:30 UTC 는 겨울엔 08:30 ET 로 개장 전이다.
    assert not is_regular_session(Market.US, ts(2024, 1, 3, 13, 30))
