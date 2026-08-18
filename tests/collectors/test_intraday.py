"""분봉 수집 — 정규화, 배치 적재, "조회 실패" 와 "정말 없다" 의 구분.

이 저장소에서 제일 자주 나는 결함은 "호출부가 조용히 0건으로 넘어간다" 는
것이다(``wiring-missing-defect-class``). 그래서 아래 대부분은 **실패가
0행으로 둔갑하지 않는지**를 본다 — API 가 진짜로 실패했을 때 예외가 위로
올라오는지, 반대로 API 가 정상 응답하면서 그냥 빈 배열을 준 경우엔 예외
없이 0행으로 끝나는지, 이 둘을 갈라 본다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from quant_rl_trading.collectors.errors import CollectorError, LSAPIError
from quant_rl_trading.collectors.intraday_collector import (
    INTERVAL_NCNT,
    MAX_ROWS_PER_CALL,
    PATH_CHART_KR,
    PATH_CHART_US,
    TR_KR,
    TR_US,
    IntradayCollector,
    ingest_run_id,
    normalize_kr,
    normalize_us,
)
from quant_rl_trading.collectors.ls_client import LSClient, LSCredentials
from quant_rl_trading.collectors.ls_us_source import LsUsSource
from quant_rl_trading.collectors.raw import RawArchive
from quant_rl_trading.replay.clock import ReplayClock

KR_BARS = [
    {"date": "20260818", "time": "151700", "open": "268000", "high": "268500",
     "low": "268000", "close": "268000", "jdiff_vol": "49483", "value": "13273"},
    {"date": "20260818", "time": "151800", "open": "268250", "high": "268500",
     "low": "268000", "close": "268000", "jdiff_vol": "31043", "value": "8330"},
    {"date": "", "time": "", "close": "-"},  # 쓰레기 행 — 버려야 한다
]

US_BARS = [
    {"date": "20260818", "loctime": "031500", "open": "305.29", "high": "305.29",
     "low": "305.25", "close": "305.25", "exevol": 131, "amount": 0},
    {"date": "20260818", "loctime": "032000", "open": "305.24", "high": "305.29",
     "low": "305.24", "close": "305.25", "exevol": 77, "amount": 0},
]


# -- 정규화 -------------------------------------------------------------------


def test_normalize_kr_converts_kst_wall_clock_to_utc(ts) -> None:  # type: ignore[no-untyped-def]
    rows = normalize_kr(KR_BARS, entity_id="KR:005930", interval="1m", observed_at=ts(2026, 8, 18, 7))

    # 15:17 KST = 06:17 UTC.
    assert rows[0]["valid_from"] == datetime(2026, 8, 18, 6, 17, tzinfo=UTC)
    assert rows[0]["market"] == "KR"
    assert rows[0]["interval"] == "1m"
    assert rows[0]["close"] == 268_000.0


def test_normalize_kr_drops_malformed_rows(ts) -> None:  # type: ignore[no-untyped-def]
    rows = normalize_kr(KR_BARS, entity_id="KR:005930", interval="1m", observed_at=ts(2026, 8, 18, 7))

    assert len(rows) == 2


def test_normalize_us_converts_eastern_wall_clock_to_utc(ts) -> None:  # type: ignore[no-untyped-def]
    rows = normalize_us(US_BARS, entity_id="US:AAPL", interval="5m", observed_at=ts(2026, 8, 18, 7))

    # 2026-08-18 은 여름(EDT, UTC-4). 03:15 ET = 07:15 UTC.
    assert rows[0]["valid_from"] == datetime(2026, 8, 18, 7, 15, tzinfo=UTC)
    assert rows[0]["market"] == "US"
    assert rows[0]["close"] == 305.25


def test_normalize_us_handles_dst_without_manual_offset(ts) -> None:  # type: ignore[no-untyped-def]
    """서머타임을 손으로 계산하지 않는다 — market_hours.py 와 같은 원칙.

    같은 시각 문자열(09:00)이 겨울(EST, UTC-5)과 여름(EDT, UTC-4)에 서로
    다른 UTC 로 바뀌어야 한다. zoneinfo 가 그 차이를 대신 처리하는지 본다.
    """
    winter = normalize_us(
        [{"date": "20260115", "loctime": "090000", "open": "1", "high": "1", "low": "1",
          "close": "1", "exevol": 0, "amount": 0}],
        entity_id="US:AAPL", interval="1m", observed_at=ts(2026, 1, 15, 20),
    )
    summer = normalize_us(
        [{"date": "20260715", "loctime": "090000", "open": "1", "high": "1", "low": "1",
          "close": "1", "exevol": 0, "amount": 0}],
        entity_id="US:AAPL", interval="1m", observed_at=ts(2026, 7, 15, 20),
    )

    assert winter[0]["valid_from"].hour == 14  # 09:00 EST = 14:00 UTC
    assert summer[0]["valid_from"].hour == 13  # 09:00 EDT = 13:00 UTC


def test_ingest_run_id_carries_the_date_axis(ts) -> None:  # type: ignore[no-untyped-def]
    """날짜 없는 고정 run_id 때문에 재수집이 영원히 막힌 사고가 있었다(adjfactor).

    그 사고의 교훈은 "날짜를 넣어라" 가 아니라 **"다시 받아야 하는 주기보다
    잘게 쪼개라"** 다. 일봉은 하루 한 번 확정되니 날짜면 충분하지만 **분봉은
    장중 내내 새 봉이 생긴다** — 날짜까지만 넣으면 그날 두 번째 수집이
    ``DuplicateIngestRun`` 으로 막히고, 분봉이 장 시작 시점에 얼어붙는다.
    화면은 멀쩡한데 값만 하루 종일 안 변하는, 제일 늦게 발견되는 종류다.
    """
    morning = ingest_run_id("KR", "5m", observed_at=ts(2026, 8, 18, 1))
    midday = ingest_run_id("KR", "5m", observed_at=ts(2026, 8, 18, 4))
    tomorrow = ingest_run_id("KR", "5m", observed_at=ts(2026, 8, 19, 1))

    assert morning == "intraday-KR-5m-20260818T0100"
    assert morning != midday, "장중 재수집이 막힌다 — 분봉이 하루 종일 멈춘다"
    assert morning != tomorrow, "다음날이 막힌다 — adjfactor 사고의 재발이다"


def test_같은_봉을_다시_받는_것은_중복이_아니라_재관측이다(ts) -> None:  # type: ignore[no-untyped-def]
    """구간(interval)과 시장이 같아도 **실행 시각이 다르면 다른 run_id** 다.

    창고가 bitemporal 이라 같은 봉을 두 번 받아도 나중 ``observed_at`` 이
    이긴다. 늦게 정정된 봉이 자연스럽게 반영되는 것이 옳고, 그걸 멱등성으로
    막으면 정정을 영영 못 받는다.
    """
    seen = {
        ingest_run_id("KR", "5m", observed_at=ts(2026, 8, 18, hour))
        for hour in (0, 1, 2, 3, 4, 5, 6)
    }
    assert len(seen) == 7, "장중 시각이 달라도 run_id 가 겹친다"


# -- 상수 ---------------------------------------------------------------------


def test_all_five_intervals_map_to_verified_ncnt() -> None:
    """다섯 구간(1m/5m/15m/1H/4H) 은 실호출로 검증된 ncnt 값이다(모듈독스트링)."""
    assert INTERVAL_NCNT == {"1m": 1, "5m": 5, "15m": 15, "1H": 60, "4H": 240}


def test_kr_path_is_the_verified_chart_path() -> None:
    """t8410 이 겪은 실수(``/stock/market-data`` 로 불러 IGW00215)를 반복하지 않는다."""
    assert PATH_CHART_KR == "/stock/chart"
    assert TR_KR == "t8412"


def test_us_path_uses_the_corrected_tr() -> None:
    """g3202 가 아니라 g3203 이다 — ls_us_source.py:510 주석이 틀렸다(모듈독스트링)."""
    assert PATH_CHART_US == "/overseas-stock/chart"
    assert TR_US == "g3203"
    assert TR_US != "g3202"


# -- KR 수집 파이프라인 --------------------------------------------------------


@pytest.fixture
def kr_collector(store, tmp_path: Path, ts):  # type: ignore[no-untyped-def]
    seen_paths: list[str] = []
    requested_shcodes: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(
                200, json={"access_token": "t", "token_type": "Bearer", "expires_in": 86400}
            )
        seen_paths.append(request.url.path)
        body = json.loads(request.content)
        block = body.get(f"{TR_KR}InBlock", {})
        shcode = block.get("shcode")
        requested_shcodes.append(shcode)
        if shcode == "FAIL":
            return httpx.Response(200, json={"rsp_cd": "99999", "rsp_msg": "조회 실패"})
        return httpx.Response(200, json={"rsp_cd": "00000", f"{TR_KR}OutBlock1": KR_BARS})

    clock = ReplayClock(ts(2026, 8, 18, 7))
    client = LSClient(
        credentials=LSCredentials("key", "secret", "https://api.test"),
        clock=clock,
        transport=httpx.MockTransport(handler),
        live_trading=True,
        sleep=lambda _: None,
    )
    client.seen_paths = seen_paths  # type: ignore[attr-defined]
    client.requested_shcodes = requested_shcodes  # type: ignore[attr-defined]
    return IntradayCollector(
        store=store,
        clock=clock,
        archive=RawArchive(root=tmp_path / "data"),
        kr_client=client,
    )


def test_collect_kr_calls_the_verified_path(kr_collector) -> None:  # type: ignore[no-untyped-def]
    kr_collector.collect_kr(["005930"], interval="1m", ingest_run_id="run-1")

    assert PATH_CHART_KR in kr_collector.kr_client.seen_paths


def test_collect_kr_batches_many_symbols_into_one_append(kr_collector, store, tmp_path, ts) -> None:  # type: ignore[no-untyped-def]
    """여러 종목을 한 번의 실행에 모아 파일 하나로 적재한다.

    종목 축으로 ``append()`` 를 따로 부르면 파일이 종목 수만큼 늘어난다
    (미장 백필이 파일 247만 개를 만든 전례). 종목이 셋이어도 파티션 파일은
    하나여야 한다.
    """
    written = kr_collector.collect_kr(
        ["005930", "000660", "035420"], interval="1m", ingest_run_id="run-batch"
    )

    assert written == 6  # 종목 3 × 유효 봉 2

    files = list((tmp_path / "data" / "curated" / "prices_intraday").glob("*/*.parquet"))
    assert len(files) == 1, f"파일이 하나가 아니다: {files}"

    seen = store.get("prices_intraday", as_of=ts(2026, 8, 18, 8))
    assert set(seen["entity_id"]) == {"KR:005930", "KR:000660", "KR:035420"}


def test_collect_kr_accepts_bare_and_prefixed_symbols(kr_collector) -> None:  # type: ignore[no-untyped-def]
    kr_collector.collect_kr(["005930", "KR:000660"], interval="1m", ingest_run_id="run-mixed")

    assert kr_collector.kr_client.requested_shcodes == ["005930", "000660"]


def test_collect_kr_rejects_unknown_interval(kr_collector) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(CollectorError, match="interval"):
        kr_collector.collect_kr(["005930"], interval="7m", ingest_run_id="run-bad")

    assert not kr_collector.kr_client.seen_paths, "잘못된 interval 인데 호출이 나갔다"


def test_collect_kr_api_failure_raises_instead_of_returning_zero(kr_collector) -> None:  # type: ignore[no-untyped-def]
    """**조회 실패와 '정말 없다' 를 구분한다.**

    TR 이 실패(``rsp_cd`` 오류)했는데 예외 없이 0행을 돌려주면, 그 0행이
    "그 종목엔 분봉이 없다" 인지 "API 가 잠깐 죽었다" 인지 화면에서 구분할
    수 없다. 실패는 그대로 위로 올라와야 한다.
    """
    with pytest.raises(LSAPIError):
        kr_collector.collect_kr(["FAIL"], interval="1m", ingest_run_id="run-fail")


def test_collect_kr_genuinely_empty_response_is_not_an_error(kr_collector, ts) -> None:  # type: ignore[no-untyped-def]
    """반대 경우 — TR 이 정상 응답(rsp_cd=00000)하면서 빈 배열을 주면 0행이 맞다."""

    def empty_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(
                200, json={"access_token": "t", "token_type": "Bearer", "expires_in": 86400}
            )
        return httpx.Response(200, json={"rsp_cd": "00000", f"{TR_KR}OutBlock1": []})

    kr_collector.kr_client._client = None  # 새 transport 로 갈아끼운다
    kr_collector.kr_client.transport = httpx.MockTransport(empty_handler)

    written = kr_collector.collect_kr(["999999"], interval="1m", ingest_run_id="run-empty")

    assert written == 0


def test_collect_kr_duplicate_run_id_is_rejected(kr_collector) -> None:  # type: ignore[no-untyped-def]
    """같은 날 두 번째 실행은 의도적으로 막힌다 (append-only 멱등성)."""
    from quant_rl_trading.store.errors import DuplicateIngestRun

    kr_collector.collect_kr(["005930"], interval="1m", ingest_run_id="run-once")
    with pytest.raises(DuplicateIngestRun):
        kr_collector.collect_kr(["005930"], interval="1m", ingest_run_id="run-once")


def test_collect_kr_different_intervals_do_not_collide_on_natural_key(kr_collector, store, ts) -> None:  # type: ignore[no-untyped-def]
    """같은 종목의 1분봉과 5분봉을 같은 날 둘 다 받아도 서로를 안 지운다.

    자연키에 ``interval`` 이 없으면 (entity_id, valid_from) 이 같은 두 해상도
    봉이 같은 자리를 다투다 한쪽이 정정본으로 밀려난다.
    """
    kr_collector.collect_kr(["005930"], interval="1m", ingest_run_id="run-1m")
    kr_collector.collect_kr(["005930"], interval="5m", ingest_run_id="run-5m")

    seen = store.get("prices_intraday", as_of=ts(2026, 8, 18, 8))
    assert set(seen["interval"]) == {"1m", "5m"}
    assert len(seen) == 4  # 2봉 × 2 interval, 어느 쪽도 안 지워졌다


def test_collect_kr_bars_are_invisible_before_collection(kr_collector, store, ts) -> None:  # type: ignore[no-untyped-def]
    """이중시간 — 수집 시각 이전 시점에서 조회하면 안 보인다."""
    kr_collector.collect_kr(["005930"], interval="1m", ingest_run_id="run-1")

    assert store.get("prices_intraday", as_of=ts(2026, 8, 18, 6)).empty


def test_raw_response_is_archived_per_symbol(kr_collector, tmp_path, ts) -> None:  # type: ignore[no-untyped-def]
    """정규화 버그를 나중에 발견해도 원본이 남아 있어야 복구할 수 있다."""
    kr_collector.collect_kr(["005930", "000660"], interval="1m", ingest_run_id="run-archive")

    files = list((tmp_path / "data" / "raw" / "ls_intraday" / "date=2026-08-18").glob("*.json"))
    assert len(files) == 2


def test_latency_is_measured_per_stage(kr_collector, store, ts) -> None:  # type: ignore[no-untyped-def]
    kr_collector.collect_kr(["005930"], interval="1m", ingest_run_id="run-latency")

    latency = store.get("ingest_latency", as_of=ts(2026, 8, 18, 8))
    assert {"fetch", "archive", "normalize", "append"} <= set(latency["stage"])
    assert all(latency["ok"])


def test_collect_kr_without_client_raises(store, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from quant_rl_trading.replay.clock import LiveClock

    collector = IntradayCollector(
        store=store, clock=LiveClock(), archive=RawArchive(root=tmp_path / "data")
    )
    with pytest.raises(CollectorError, match="kr_client"):
        collector.collect_kr(["005930"], interval="1m", ingest_run_id="run-none")


# -- US 수집 파이프라인 --------------------------------------------------------


@pytest.fixture
def us_collector(store, tmp_path: Path, ts):  # type: ignore[no-untyped-def]
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(
                200, json={"access_token": "t", "token_type": "Bearer", "expires_in": 86400}
            )
        body = json.loads(request.content)
        if "g3101InBlock" in body:
            symbol = body["g3101InBlock"]["symbol"]
            exch = body["g3101InBlock"]["exchcd"]
            # NASDAQ("82")에만 있는 걸로 꾸민다 — resolve_exchange 가 82 를 먼저 본다.
            if symbol == "AAPL" and exch == "82":
                return httpx.Response(200, json={"rsp_cd": "00000", "g3101OutBlock": {"symbol": symbol}})
            if symbol == "GHOST":
                return httpx.Response(200, json={"rsp_cd": "02679", "rsp_msg": "없음"})
            return httpx.Response(200, json={"rsp_cd": "02679", "rsp_msg": "없음"})
        if "g3203InBlock" in body:
            return httpx.Response(200, json={"rsp_cd": "00000", "g3203OutBlock1": US_BARS})
        raise AssertionError(f"예상 못한 요청: {body}")

    clock = ReplayClock(ts(2026, 8, 18, 7))
    client = LSClient(
        credentials=LSCredentials("key", "secret", "https://api.test"),
        clock=clock,
        transport=httpx.MockTransport(handler),
        live_trading=True,
        sleep=lambda _: None,
    )
    return IntradayCollector(
        store=store,
        clock=clock,
        archive=RawArchive(root=tmp_path / "data"),
        us_source=LsUsSource(client=client),
    )


def test_collect_us_resolves_exchange_and_writes_rows(us_collector, store, ts) -> None:  # type: ignore[no-untyped-def]
    written = us_collector.collect_us(["AAPL"], interval="5m", ingest_run_id="run-us-1")

    assert written == 2
    seen = store.get("prices_intraday", as_of=ts(2026, 8, 18, 8), market="US")
    assert list(seen["entity_id"]) == ["US:AAPL", "US:AAPL"]


def test_collect_us_skips_unresolvable_symbols_without_raising(us_collector) -> None:  # type: ignore[no-untyped-def]
    """상장폐지 등으로 거래소를 못 찾는 종목은 건너뛰고 개수만 센다.

    여기서 예외를 던지면 나머지 종목의 수집까지 통째로 멈춘다 — 화면은
    "이 종목은 시세가 없다" 를 알면 되지, 수집 전체가 죽을 일이 아니다.
    """
    written = us_collector.collect_us(["AAPL", "GHOST"], interval="5m", ingest_run_id="run-us-2")

    assert written == 2  # AAPL 만 반영됐다
    assert us_collector.last_skipped == 1


def test_collect_us_without_source_raises(store, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from quant_rl_trading.replay.clock import LiveClock

    collector = IntradayCollector(
        store=store, clock=LiveClock(), archive=RawArchive(root=tmp_path / "data")
    )
    with pytest.raises(CollectorError, match="us_source"):
        collector.collect_us(["AAPL"], interval="5m", ingest_run_id="run-none")
