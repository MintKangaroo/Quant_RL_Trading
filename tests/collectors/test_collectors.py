"""수집자 — 정규화, 원본 보존, 지연 실측.

가장 중요한 것은 ``observed_at`` 이 **수집 시각**이라는 것이다. 봉의 날짜를
관측시각으로 쓰면 그날 자정부터 그날 종가를 알고 있던 것이 되고, 백테스트
전체가 하루씩 미래를 본다.
"""

from __future__ import annotations

import json

import httpx
import pytest

from quant_rl_trading.collectors.errors import CollectorError
from quant_rl_trading.collectors.ls_client import (
    PATH_CHART,
    PATH_MARKET,
    LSClient,
    LSCredentials,
)
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
    """수정주가에는 미래의 분할·증자가 반영돼 있다. 원주가만 받는다.

    짝이 되는 조정계수는 ``collectors/corporate_actions.py`` 가 발효일과 함께
    별도로 받아 ``prices.adj_factor`` 에 넣는다.
    """
    assert SUJUNG_RAW == "N"


# -- t8410 경로 ---------------------------------------------------------------
#
# 이 TR 은 **한 번도 성공한 적이 없었다.** ``PATH_MARKET``(``/stock/market-data``)
# 으로 불러서 HTTP 500 ``IGW00215``(유효하지 않은 TR CD)를 받고 있었다 —
# 국장 일봉이 KRX Open API 로 들어오는 덕에 아무도 몰랐다 (실측 2026-08-15).
#
# 경로는 고쳤다. 그런데 고치면 이 메서드가 **살아난다**. 국장 일봉의 정본은
# KRX 이고, 자연키가 ``(entity_id, valid_from)`` 이라 두 소스가 같은 표에 쓰면
# 어느 값이 남는지가 수집 순서에 달린다. 그래서 경로를 고치고 호출을 막았다.
#
# 아래 둘을 같이 못 박는다 — **경로가 맞다**는 것과 **지금은 안 나간다**는 것.


def test_daily_bars_use_the_chart_path() -> None:
    """차트 TR 의 경로는 ``PATH_MARKET`` 이 아니다."""
    assert PATH_CHART == "/stock/chart"
    assert PATH_CHART != PATH_MARKET


def test_the_call_actually_goes_to_the_chart_path(collector) -> None:  # type: ignore[no-untyped-def]
    """t8410 이 실제로 그 경로로 나간다. 상수만 맞고 호출부가 옛 경로면 소용없다."""
    collector.collect_ohlcv(
        "005930", ingest_run_id="run-path", allow_duplicate_source=True
    )

    assert PATH_CHART in collector.client.seen_paths
    assert PATH_MARKET not in collector.client.seen_paths


def test_ohlcv_collection_is_blocked_by_default(collector) -> None:  # type: ignore[no-untyped-def]
    """명시하지 않으면 안 나간다. **주석만으로는 다음 사람이 배선하는 것을 못 막는다.**"""
    with pytest.raises(CollectorError, match="정본"):
        collector.collect_ohlcv("005930", ingest_run_id="run-blocked")

    assert not collector.client.seen_paths, "막혔는데 호출이 나갔다"


def test_master_marks_etf_as_not_tradable(ts) -> None:  # type: ignore[no-untyped-def]
    rows = normalize_master(
        MASTER, market=Market.KR, trading_day=ts(2024, 3, 5), observed_at=ts(2024, 3, 5, 8)
    )

    tradable = {row["entity_id"]: row["is_tradable"] for row in rows}

    assert tradable == {"KR:005930": True, "KR:069500": False}


# -- 수집 파이프라인 -----------------------------------------------------------


@pytest.fixture
def collector(store, tmp_path, ts):  # type: ignore[no-untyped-def]
    # 어느 경로로 나갔는지 기록한다. t8410 이 **한 번도 성공한 적이 없던**
    # 원인이 경로였으므로(IGW00215), 상수만 보는 테스트로는 못 지킨다.
    seen_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(
                200, json={"access_token": "t", "token_type": "Bearer", "expires_in": 86400}
            )
        seen_paths.append(request.url.path)
        body = json.loads(request.content)
        if "t8410InBlock" in body:
            return httpx.Response(200, json={"rsp_cd": "00000", "t8410OutBlock1": BARS})
        return httpx.Response(200, json={"rsp_cd": "00000", "t8436OutBlock": MASTER})

    clock = ReplayClock(ts(2024, 3, 5, 16))
    client = LSClient(
        credentials=LSCredentials("key", "secret", "https://api.test"),
        clock=clock,
        transport=httpx.MockTransport(handler),
        live_trading=True,
        sleep=lambda _: None,
    )
    client.seen_paths = seen_paths  # type: ignore[attr-defined]
    return MarketCollector(
        store=store,
        client=client,
        clock=clock,
        archive=RawArchive(root=tmp_path / "data"),
        market=Market.KR,
    )


def test_collected_bars_are_readable_through_the_gate(collector, store, ts) -> None:  # type: ignore[no-untyped-def]
    collector.collect_ohlcv(
        "005930", ingest_run_id="run-1", allow_duplicate_source=True
    )

    seen = store.get("prices", as_of=ts(2024, 3, 5, 18))

    assert list(seen["close"]) == [70_500.0, 71_800.0]


def test_collected_bars_are_invisible_before_collection(collector, store, ts) -> None:  # type: ignore[no-untyped-def]
    """3월 4일 봉을 3월 5일 16시에 수집했다면, 3월 5일 09시의 나는 몰랐다."""
    collector.collect_ohlcv(
        "005930", ingest_run_id="run-1", allow_duplicate_source=True
    )

    assert store.get("prices", as_of=ts(2024, 3, 5, 9)).empty


def test_raw_response_is_preserved(collector, tmp_path, ts) -> None:  # type: ignore[no-untyped-def]
    """정규화에 버그를 발견해도 원본이 있으면 다시 만들 수 있다."""
    collector.collect_ohlcv(
        "005930", ingest_run_id="run-1", allow_duplicate_source=True
    )

    files = list((tmp_path / "data" / "raw" / "ls_api" / "date=2024-03-05").glob("*.json"))

    assert len(files) == 1
    saved = json.loads(files[0].read_text(encoding="utf-8"))
    assert saved["payload"]["t8410OutBlock1"] == BARS


def test_latency_is_measured_per_stage(collector, store, ts) -> None:  # type: ignore[no-untyped-def]
    collector.collect_ohlcv(
        "005930", ingest_run_id="run-1", allow_duplicate_source=True
    )

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
