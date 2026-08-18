"""미장 일봉 증분 수집 — 창이 움직여도 같은 세션을 두 번 쓰지 않는다.

이 파일이 지키는 것은 세 가지다.

1. **멱등.** 겹치는 창을 매일 돌려도 이미 받은 (세션, 배치) 는 API 를 치지도
   않는다. 이게 깨지면 증분이 매일 111분을 그대로 쓴다.
2. **부분 수집이 세션을 잠그지 않는다.** 몇 종목만 받은 실행이 전체 실행과
   같은 run_id 를 쓰면 나머지 6,600종목이 영영 안 들어온다.
3. **미공표 세션을 저장하지 않는다.** 오늘 장중 봉을 넣으면 백테스트가 미래를
   본다 — 그리고 그 세션은 매니페스트에 안 남아야 내일 다시 받는다.
"""

from __future__ import annotations

import json
from datetime import date

import httpx
import pytest

from quant_rl_trading.collectors.ls_client import LSClient, LSCredentials
from quant_rl_trading.collectors.ls_us_source import (
    NASDAQ,
    NYSE,
    LsUsSource,
    UsIncrementalCollector,
    incremental_run_id,
)
from quant_rl_trading.collectors.market_hours import Market
from quant_rl_trading.collectors.publication import PublicationPolicy
from quant_rl_trading.collectors.raw import RawArchive
from quant_rl_trading.replay.clock import ReplayClock

#: 2026-08-13(목) · 08-14(금) · 08-17(월) 은 미장 거래일이다. 08-18 은 아직
#: 공표 전이라 응답에 섞여 오지만 저장되면 안 된다.
#: 어느 심볼이 어느 거래소에 있는지. 가짜 LS 가 이걸로 0행/정상을 가른다.
EXCHANGE_OF = {"AAPL": NASDAQ, "MSFT": NASDAQ, "JPM": NYSE}

BARS = {
    "20260813": {"open": "10", "high": "11", "low": "9", "close": "10.5",
                 "volume": 100, "amount": 1000},
    "20260814": {"open": "10.5", "high": "12", "low": "10", "close": "11.5",
                 "volume": 200, "amount": 2000},
    "20260817": {"open": "11.5", "high": "12", "low": "11", "close": "11.8",
                 "volume": 300, "amount": 3000},
    "20260818": {"open": "11.8", "high": "12", "low": "11", "close": "11.9",
                 "volume": 5, "amount": 50},
}


@pytest.fixture
def source(ts):  # type: ignore[no-untyped-def]
    """g3204 만 답하는 가짜 LS. 호출 수와 경로를 세어 둔다."""
    calls: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(
                200, json={"access_token": "t", "token_type": "Bearer", "expires_in": 86400}
            )
        body = json.loads(request.content)["g3204InBlock"]
        calls.append({"path": request.url.path, "symbol": body["symbol"],
                      "exchcd": body["exchcd"],
                      "sdate": body["sdate"], "edate": body["edate"]})
        # **거래소가 틀리면 오류가 아니라 0행이다** (실측 2026-08-18).
        # 그 규약 위에서 거래소를 알아내므로 가짜도 같게 굴어야 한다.
        rows = [
            {"date": day, **values}
            for day, values in BARS.items()
            if body["sdate"] <= day <= body["edate"]
            and body["exchcd"] == EXCHANGE_OF.get(body["symbol"], NASDAQ)
        ]
        return httpx.Response(200, json={"rsp_cd": "00000", "g3204OutBlock1": rows})

    clock = ReplayClock(ts(2026, 8, 18, 4))
    client = LSClient(
        credentials=LSCredentials("key", "secret", "https://api.test"),
        clock=clock,
        transport=httpx.MockTransport(handler),
        live_trading=True,
        sleep=lambda _: None,
    )
    built = LsUsSource(client=client)
    built.calls = calls  # type: ignore[attr-defined]
    return built


@pytest.fixture
def collector(store, source, tmp_path, ts):  # type: ignore[no-untyped-def]
    clock = ReplayClock(ts(2026, 8, 18, 4))
    return UsIncrementalCollector(
        store=store,
        source=source,
        clock=clock,
        archive=RawArchive(root=tmp_path / "data"),
        # 미장 마감(16:00 ET) + 20분. 08-18 세션은 이 시각에 아직 공표 전이다.
        policy=PublicationPolicy(market=Market.US, lag_seconds=1200, clock=clock),
        exchanges={"AAPL": "82", "MSFT": "82"},
        batch_size=1,
    )


WINDOW = {"start": date(2026, 8, 13), "end": date(2026, 8, 18)}


def test_one_call_per_symbol(collector, source) -> None:  # type: ignore[no-untyped-def]
    """페이징이 붙으면 6,648종목이 111분에서 222분이 된다."""
    collector.run_batch(0, ["AAPL"], **WINDOW)

    assert len(source.calls) == 1
    assert source.calls[0]["path"] == "/overseas-stock/chart"


def test_unpublished_session_is_not_stored(collector, store, ts) -> None:  # type: ignore[no-untyped-def]
    """08-18 봉은 응답에 있지만 아직 마감 전이다."""
    result = collector.run_batch(0, ["AAPL"], **WINDOW)

    stored = store.get("prices", as_of=ts(2026, 8, 20))
    assert sorted(stored["valid_from"].dt.date.unique()) == [
        date(2026, 8, 13), date(2026, 8, 14), date(2026, 8, 17)
    ]
    assert result.deferred_sessions == 1
    # 매니페스트에 안 남아야 내일 실행이 다시 받는다.
    assert not store.ingest_run_recorded(
        "prices", incremental_run_id(Market.US, date(2026, 8, 18), 0)
    )


def test_observed_at_is_per_session(collector, store, ts) -> None:  # type: ignore[no-untyped-def]
    """한 번에 받아왔다고 오늘 시각을 전부에 찍으면 그 구간이 통째로 안 보인다."""
    collector.run_batch(0, ["AAPL"], **WINDOW)

    stored = store.get("prices", as_of=ts(2026, 8, 15)).sort_values("valid_from")

    # 08-15 시점에서는 08-13·08-14 만 알 수 있었다.
    assert list(stored["valid_from"].dt.date) == [date(2026, 8, 13), date(2026, 8, 14)]


def test_rerun_does_not_call_the_api_again(collector, source) -> None:  # type: ignore[no-untyped-def]
    """멱등은 '받아서 버린다' 가 아니라 '안 부른다' 여야 뜻이 있다."""
    collector.run_batch(0, ["AAPL"], **WINDOW)
    source.calls.clear()

    # 창을 이미 받은 세션들로만 좁혀 부른다 (08-18 은 여전히 미공표라 제외).
    result = collector.run_batch(0, ["AAPL"], start=date(2026, 8, 13), end=date(2026, 8, 17))

    assert source.calls == []
    assert result.skipped
    assert result.rows == 0
    assert len(result.already) == 3


def test_partial_run_does_not_lock_the_session(collector, store, source) -> None:  # type: ignore[no-untyped-def]
    """상위 N종목만 받은 실행이 나머지 종목을 영영 막으면 안 된다."""
    collector.scope = "top1"
    collector.run_batch(0, ["AAPL"], **WINDOW)

    assert not store.ingest_run_recorded(
        "prices", incremental_run_id(Market.US, date(2026, 8, 17), 0)
    )
    assert store.ingest_run_recorded(
        "prices", incremental_run_id(Market.US, date(2026, 8, 17), 0, scope="top1")
    )

    # 전체 실행이 같은 세션을 다시 채운다.
    collector.scope = ""
    source.calls.clear()
    result = collector.run_batch(0, ["AAPL"], **WINDOW)

    assert result.rows == 3
    assert len(source.calls) == 1


def test_batch_number_follows_list_order(collector) -> None:  # type: ignore[no-untyped-def]
    """번호가 run_id 에 박히므로 명단 순서가 바뀌면 재개가 성립하지 않는다."""
    assert collector.batches(["AAPL", "MSFT"]) == [(0, ["AAPL"]), (1, ["MSFT"])]


def test_batches_are_recorded_separately(collector, store) -> None:  # type: ignore[no-untyped-def]
    """배치 하나가 실패해도 그 세션이 '적재됨' 으로 잠기면 안 된다."""
    collector.run_batch(0, ["AAPL"], **WINDOW)

    day = date(2026, 8, 17)
    assert store.ingest_run_recorded("prices", incremental_run_id(Market.US, day, 0))
    assert not store.ingest_run_recorded("prices", incremental_run_id(Market.US, day, 1))


def test_missing_symbol_does_not_sink_the_batch(collector, store, ts) -> None:  # type: ignore[no-untyped-def]
    """상폐·미취급 종목은 사실이지 실패가 아니다 — 나머지는 들어가야 한다."""
    collector.exchanges["DEAD"] = "82"
    collector.batch_size = 2
    result = collector.run_batch(0, ["AAPL", "DEAD"], **WINDOW)

    # 가짜 소스는 심볼을 가리지 않으므로 DEAD 도 봉을 받는다. 대신 명단에
    # 없는 종목으로 거래소 조회가 도는 경로를 따로 본다.
    assert result.rows == 6
    assert store.get("prices", as_of=ts(2026, 8, 20))["entity_id"].nunique() == 2


def test_run_id_marks_scope(ts) -> None:  # type: ignore[no-untyped-def]
    assert (
        incremental_run_id(Market.US, date(2026, 8, 17), 3)
        == "inc-prices-US-2026-08-17-b003"
    )
    assert (
        incremental_run_id(Market.US, date(2026, 8, 17), 3, scope="top500")
        == "inc-prices-US-2026-08-17-top500-b003"
    )


# -- 신선도 판정 ---------------------------------------------------------------
#
# **행 수로 성공을 판정하지 않는다.** 이 저장소의 반복 결함이다 — 환율
# 수집기가 매일 ``0행 rc=0`` 을 찍으며 11일 결손을 숨기고 있었다. 미장 시세는
# 여기에 함정이 하나 더 있다: 소량 검증으로 5종목만 넣어도 그 세션이 창고의
# "최신" 이 되어 6,600종목의 결손을 가린다.


def _price_rows(day: date, symbols: list[str], ts):  # type: ignore[no-untyped-def]
    return [
        {
            "entity_id": f"US:{symbol}",
            "valid_from": ts(day.year, day.month, day.day),
            "observed_at": ts(day.year, day.month, day.day + 1, 5),
            "source": "ls_us",
            "market": "US",
            "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5,
            "volume": 100.0, "value": 1000.0, "adj_factor": None,
        }
        for symbol in symbols
    ]


def test_latest_session_ignores_thin_sessions(store, ts) -> None:  # type: ignore[no-untyped-def]
    """5종목짜리 검증 실행이 창고를 '최신' 으로 만들면 안 된다."""
    from tools.collect_us_prices import warehouse_latest

    full = [f"S{index:03d}" for index in range(100)]
    store.append("prices", _price_rows(date(2026, 8, 13), full, ts), ingest_run_id="r1")
    store.append("prices", _price_rows(date(2026, 8, 14), full, ts), ingest_run_id="r2")
    store.append("prices", _price_rows(date(2026, 8, 17), full[:3], ts), ingest_run_id="r3")

    newest, counts = warehouse_latest(store, as_of=ts(2026, 8, 20))

    assert newest == date(2026, 8, 14)
    assert counts[date(2026, 8, 17)] == 3


def test_exchange_is_found_without_a_master_call(collector, source) -> None:  # type: ignore[no-untyped-def]
    """거래소를 모르면 차트로 찾는다 — 마스터(g3101)를 부르면 종목당 3호출이다."""
    del collector.exchanges["AAPL"]
    result = collector.run_batch(0, ["AAPL"], **WINDOW)

    assert [call["path"] for call in source.calls] == ["/overseas-stock/chart"]
    assert result.calls == 1
    assert result.rows == 3
    # 다음 실행이 1호출로 끝나도록 알아낸 값을 남긴다.
    assert collector.exchanges["AAPL"] == NASDAQ


def test_wrong_exchange_falls_back_once(collector, source) -> None:  # type: ignore[no-untyped-def]
    """나스닥을 먼저 친다. 뉴욕 종목은 0행을 받고 한 번 더 친다."""
    result = collector.run_batch(0, ["JPM"], **WINDOW)

    assert [call["exchcd"] for call in source.calls] == [NASDAQ, NYSE]
    assert result.calls == 2
    assert collector.exchanges["JPM"] == NYSE
