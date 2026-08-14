"""미장 상장주식수 계약 테스트.

**응답은 지어내지 않는다.** ``fixtures/companyfacts_*.json`` 은 2026-08-15 에
SEC 에서 실제로 받은 응답을 태그만 남기고 자른 것이다. 합성 데이터로 통과하는
테스트는 현실을 말해주지 않는다 — 이 저장소에서 반복된 사고 유형이고, 실제로
아래 네 종목이 각각 다른 함정을 하나씩 갖고 있다.

| 종목 | 이 표본이 잡는 것 |
|---|---|
| AAPL | 표준 경로(``dei``) + 같은 기준일을 10-K/A 가 **같은 값으로** 재공시 |
| HCA | ``companyconcept`` API 가 0행을 주는 종목. 벌크에는 61행이 있다 |
| GOOGL | ``dei`` 태그가 아예 없다. ``us-gaap`` 사슬로만 잡힌다 |
| TKO | 발행주식수 태그가 하나도 없다. 행을 만들면 안 되는 종목 |

지키는 것은 셋이다.

1. ``observed_at`` 은 **공시일**(``filed``)이다. 기준일(``end``)로 찍으면
   분기 말에 아직 공시되지 않은 주식수를 아는 것이 된다
2. 시가총액은 **그날까지 알려진** 주식수로만 만든다. 다음 분기 공시가 과거
   세션에 새어 들어가면 미래 훔쳐보기다
3. 같은 값의 재공시는 정정본이 아니다. ``revision`` 을 올리지 않는다
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest

from quant_rl_trading.collectors.market_hours import Market
from quant_rl_trading.collectors.us_shares import (
    MARKET_CAP,
    SHARES,
    SOURCE,
    build_timelines,
    existing_shares,
    filing_moment,
    market_cap_rows,
    market_cap_run_id,
    refresh_stamp,
    share_facts,
    shares_rows,
    year_windows,
)

FIXTURES = Path(__file__).parent / "fixtures"


def facts(ticker: str) -> dict[str, Any]:
    """SEC 실제 응답 (태그만 남기고 자른 것)."""
    return json.loads((FIXTURES / f"companyfacts_{ticker}.json").read_text(encoding="utf-8"))


# -- 태그 사슬 ------------------------------------------------------------------


def test_표준_태그를_먼저_쓴다() -> None:
    found = share_facts(facts("AAPL"))

    assert found, "AAPL 은 dei 태그를 낸다"
    assert {fact.tag for fact in found} == {"dei:EntityCommonStockSharesOutstanding"}


def test_dei가_없으면_us_gaap으로_내려간다() -> None:
    """GOOGL 은 다중 클래스라 ``dei`` 태그를 아예 내지 않는다.

    사슬이 없으면 이런 기업이 통째로 빠진다 — 그리고 빠지는 쪽이 대형주다.
    """
    assert "dei" not in facts("GOOGL")["facts"]

    found = share_facts(facts("GOOGL"))

    assert found
    assert {fact.tag for fact in found} == {"us-gaap:CommonStockSharesOutstanding"}


def test_한_종목은_한_태그만_쓴다() -> None:
    """AAPL 은 dei 와 us-gaap 을 **둘 다** 갖고 있다.

    섞으면 정의가 다른 값이 한 시계열에 들어가 없던 발행주식수 급변이 생긴다.
    """
    payload = facts("AAPL")
    assert "CommonStockSharesOutstanding" in payload["facts"]["us-gaap"]

    assert len({fact.tag for fact in share_facts(payload)}) == 1


def test_태그가_없으면_행이_없다() -> None:
    """TKO 는 발행주식수 태그가 하나도 없다 (ETF·워런트도 같다).

    모르는 것을 채우지 않는다. 0 을 넣으면 "시가총액 0" 이라는 다른 사실이 된다.
    """
    assert share_facts(facts("TKO")) == []
    assert shares_rows(share_facts(facts("TKO")), ticker="TKO") == []


def test_companyconcept가_0행을_주는_종목도_벌크에는_있다() -> None:
    """HCA 는 ``companyconcept`` API 가 200 + 빈 units 를 돌려주는 종목이다.

    그 API 를 믿고 골랐으면 이런 종목이 통째로 빠졌고, 그건 "커버리지가 원래
    77%" 라는 잘못된 결론으로 이어졌을 것이다 (모듈 docstring 의 소스 표).
    """
    assert len(share_facts(facts("HCA"))) > 0


def test_ADR은_원주수를_쓰지_않는다() -> None:
    """TSM 은 20-F 를 내며 **원주** 259억 주를 신고한다. 미국 시세는 ADR
    한 장 값이고 1 ADR = 5 원주라, 그대로 곱하면 시가총액이 다섯 배가 된다.

    실측(필터 넣기 전 리더 상위): LTM 30,112 B$ · TSM 11,129 B$ ·
    BSAC 6,409 B$ — 전부 20-F 발행인이었고 전부 진짜 대형주를 밀어냈다.
    ADR 비율은 무료로 못 구한다. **채우지 않고 버린다.**
    """
    payload = facts("TSM")
    raw = payload["facts"]["dei"]["EntityCommonStockSharesOutstanding"]["units"]["shares"]
    assert {row["form"] for row in raw} == {"20-F"}, "표본이 외국 발행인 서식이어야 한다"

    assert share_facts(payload) == []


def test_정정본_서식도_외국_발행인이면_버린다() -> None:
    payload = {
        "facts": {
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "units": {
                        "shares": [
                            {"end": "2026-03-31", "val": 1, "filed": "2026-04-30",
                             "form": "20-F/A"},
                            {"end": "2026-06-30", "val": 2, "filed": "2026-07-30",
                             "form": "6-K"},
                        ]
                    }
                }
            }
        }
    }
    assert share_facts(payload) == []


def test_국내_서식은_그대로_쓴다() -> None:
    """AAPL·HCA·GOOGL 은 10-K/10-Q/8-K 라 단위가 시세와 같다."""
    for ticker in ("AAPL", "HCA", "GOOGL"):
        assert share_facts(facts(ticker)), ticker


# -- 이중시간 -------------------------------------------------------------------


def test_관측시각은_기준일이_아니라_공시일이다() -> None:
    """AAPL 2026-07-17 기준 주식수는 2026-07-31 에야 공시됐다.

    ``end`` 로 찍으면 2주 동안 아무도 모르는 숫자를 알고 있는 것이 된다.
    """
    rows = shares_rows(share_facts(facts("AAPL")), ticker="AAPL")
    row = next(r for r in rows if r["valid_from"] == datetime(2026, 7, 17, tzinfo=UTC))

    assert row["value"] == 14594180000.0
    assert row["metric"] == SHARES
    assert row["source"] == SOURCE
    assert row["market"] == str(Market.US)
    assert row["entity_id"] == "US:AAPL"
    # 공시일 + EDGAR 접수 마감(18시 ET) → UTC. 여름이라 UTC-4.
    assert row["observed_at"] == datetime(2026, 7, 31, 22, tzinfo=UTC)
    assert row["observed_at"] > row["valid_from"]


def test_모든_행이_공시일_이후에_관측된다() -> None:
    for ticker in ("AAPL", "HCA", "GOOGL"):
        for row in shares_rows(share_facts(facts(ticker)), ticker=ticker):
            assert row["observed_at"] >= row["valid_from"], f"{ticker} {row}"


def test_공시일은_서머타임을_손으로_계산하지_않는다() -> None:
    assert filing_moment(date(2026, 7, 31)) == datetime(2026, 7, 31, 22, tzinfo=UTC)
    # 겨울은 UTC-5 다. 손으로 4시간을 더하면 여기서 한 시간이 어긋난다.
    assert filing_moment(date(2026, 1, 30)) == datetime(2026, 1, 30, 23, tzinfo=UTC)


def test_같은_값의_재공시는_정정이_아니다() -> None:
    """같은 기준일이 여러 공시에 반복해 실린다 (10-K → 10-K/A, 8-K).

    값이 그대로면 새 사실이 아니다. ``revision`` 을 올려 쌓으면 창고가
    "정정이 있었다" 는 거짓을 기록한다.
    """
    found = share_facts(facts("AAPL"))
    by_end: dict[date, list[float]] = {}
    for fact in found:
        by_end.setdefault(fact.end, []).append(fact.value)

    for end, values in by_end.items():
        assert len(values) == len(set(values)), f"{end} 에 같은 값이 두 번 들어갔다"

    # 값이 하나뿐인 기준일은 전부 revision 0 이어야 한다.
    for fact in found:
        if len(by_end[fact.end]) == 1:
            assert fact.revision == 0


def test_값이_바뀌면_정정본이_된다() -> None:
    """같은 기준일에 다른 값이 나중에 공시되면 그건 진짜 정정이다."""
    payload = {
        "facts": {
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "units": {
                        "shares": [
                            {"end": "2026-03-31", "val": 100, "filed": "2026-04-30"},
                            # 같은 값 재공시 — 무시된다
                            {"end": "2026-03-31", "val": 100, "filed": "2026-06-01"},
                            # 값이 바뀌었다 — 정정본
                            {"end": "2026-03-31", "val": 105, "filed": "2026-08-01"},
                        ]
                    }
                }
            }
        }
    }
    found = share_facts(payload)

    assert [(fact.value, fact.revision) for fact in found] == [(100.0, 0), (105.0, 1)]
    assert found[0].filed == date(2026, 4, 30), "가장 이른 공시가 진실이다"


def test_음수와_비수치는_버린다() -> None:
    payload = {
        "facts": {
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "units": {
                        "shares": [
                            {"end": "2026-03-31", "val": 0, "filed": "2026-04-30"},
                            {"end": "2026-06-30", "val": "많음", "filed": "2026-07-30"},
                            {"end": "2026-09-30", "val": 10, "filed": None},
                            {"end": "2026-12-31", "val": 10, "filed": "2027-01-30"},
                        ]
                    }
                }
            }
        }
    }
    assert [fact.end for fact in share_facts(payload)] == [date(2026, 12, 31)]


def test_창_밖의_옛_공시는_넣지_않는다() -> None:
    found = share_facts(facts("AAPL"))
    rows = shares_rows(found, ticker="AAPL", since=date(2026, 1, 1))

    assert rows
    assert all(row["valid_from"] >= datetime(2026, 1, 1, tzinfo=UTC) for row in rows)


# -- 시가총액 -------------------------------------------------------------------


def bar(ticker: str, day: date, close: float, hour: int = 21) -> dict[str, Any]:
    """미장 일봉 한 줄. 관측은 마감(16:00 ET) + 20분이다."""
    return {
        "entity_id": f"US:{ticker}",
        "valid_from": datetime(day.year, day.month, day.day, tzinfo=UTC),
        "observed_at": datetime(day.year, day.month, day.day, hour, tzinfo=UTC),
        "close": close,
    }


@pytest.fixture
def timeline() -> dict[str, Any]:
    """두 분기 공시. Q1 은 4월 30일, Q2 는 7월 30일에 알려졌다."""
    return build_timelines(
        [
            {
                "entity_id": "US:AAPL",
                "observed_at": filing_moment(date(2026, 4, 30)),
                "value": 100.0,
                "revision": 0,
            },
            {
                "entity_id": "US:AAPL",
                "observed_at": filing_moment(date(2026, 7, 30)),
                "value": 90.0,
                "revision": 0,
            },
        ]
    )


def test_시가총액은_마지막으로_알려진_주식수를_쓴다(timeline: dict[str, Any]) -> None:
    rows = market_cap_rows([bar("AAPL", date(2026, 6, 15), 10.0)], timeline)

    assert len(rows) == 1
    assert rows[0]["metric"] == MARKET_CAP
    # 6월에는 Q2 공시(7월 30일)를 알 수 없다. Q1 주식수를 쓴다.
    assert rows[0]["value"] == pytest.approx(1000.0)


def test_다음_분기_공시는_과거_세션에_새지_않는다(timeline: dict[str, Any]) -> None:
    """이게 이 파일에서 가장 중요한 테스트다.

    주식수를 종목 하나의 최신값 하나로 들고 전 세션에 곱하면 여기가 깨진다 —
    그리고 그 백테스트는 조용히 미래를 본 채로 돈다.
    """
    before = market_cap_rows([bar("AAPL", date(2026, 7, 29), 10.0)], timeline)
    after = market_cap_rows([bar("AAPL", date(2026, 7, 31), 10.0)], timeline)

    assert before[0]["value"] == pytest.approx(1000.0), "공시 전에는 옛 주식수"
    assert after[0]["value"] == pytest.approx(900.0), "공시 후에는 새 주식수"


def test_첫_공시_이전_세션은_행이_없다(timeline: dict[str, Any]) -> None:
    """아직 아무 주식수도 모르던 때. 추정하지 않는다 — backward-fill 금지."""
    assert market_cap_rows([bar("AAPL", date(2026, 1, 5), 10.0)], timeline) == []


def test_주식수를_모르는_종목은_건너뛴다(timeline: dict[str, Any]) -> None:
    assert market_cap_rows([bar("TKO", date(2026, 8, 3), 10.0)], timeline) == []


def test_종가_0_세션은_시가총액을_만들지_않는다(timeline: dict[str, Any]) -> None:
    """종가 0 짜리 세션이 실제로 창고에 있었다. 그대로 곱하면 시총 0 이 남고,
    리더·트리맵이 그 거짓을 정렬에 쓴다."""
    assert market_cap_rows([bar("AAPL", date(2026, 8, 3), 0.0)], timeline) == []
    assert market_cap_rows([bar("AAPL", date(2026, 8, 3), None)], timeline) == []  # type: ignore[arg-type]


def test_관측시각은_봉에서_그대로_온다(timeline: dict[str, Any]) -> None:
    """주식수는 봉보다 먼저 공시된 것이므로 늦은 쪽은 언제나 봉이다."""
    one = bar("AAPL", date(2026, 8, 3), 10.0)
    row = market_cap_rows([one], timeline)[0]

    assert row["observed_at"] == one["observed_at"]
    assert row["valid_from"] == one["valid_from"]


def test_같은_순간의_공시는_포함된다(timeline: dict[str, Any]) -> None:
    """경계는 ``<=`` 다. 공시 시각과 봉 관측시각이 같으면 이미 공개된 것이다."""
    moment = filing_moment(date(2026, 7, 30))
    one = {
        "entity_id": "US:AAPL",
        "valid_from": datetime(2026, 7, 30, tzinfo=UTC),
        "observed_at": moment,
        "close": 10.0,
    }
    assert market_cap_rows([one], timeline)[0]["value"] == pytest.approx(900.0)


def test_정정본이_같은_순간에_있으면_최신_revision이_이긴다() -> None:
    moment = filing_moment(date(2026, 4, 30))
    lines = build_timelines(
        [
            {"entity_id": "US:X", "observed_at": moment, "value": 100.0, "revision": 0},
            {"entity_id": "US:X", "observed_at": moment, "value": 111.0, "revision": 1},
        ]
    )
    assert lines["US:X"].known_at(moment) == 111.0


# -- 적재 단위 ------------------------------------------------------------------


def test_시가총액_실행id는_세션당_하나다() -> None:
    """파티션 폭발을 막는 것이 이 단위다 — 종목 축으로 넣으면 파티션마다
    6,648개 파일이 생긴다 (과거 실측 247만 개)."""
    assert market_cap_run_id(Market.US, date(2026, 8, 3)) == "bf-market_stats-US-mcap-20260803"
    assert market_cap_run_id(Market.US, date(2026, 8, 3)) == market_cap_run_id(
        Market.US, date(2026, 8, 3)
    ), "결정론적이어야 재개가 성립한다"


def test_갱신_스탬프는_주_단위다() -> None:
    """분기로 잡으면 그 사이 석 달치 공시가 빠진 채 시가총액이 계산된다 —
    회사마다 결산월이 달라 새 공시는 매주 들어온다."""
    assert refresh_stamp(datetime(2026, 8, 15, tzinfo=UTC)) == "2026W33"
    # 같은 주 안에서는 스탬프가 그대로다 — 매일 불러도 한 번만 돈다.
    assert refresh_stamp(datetime(2026, 8, 12, tzinfo=UTC)) == refresh_stamp(
        datetime(2026, 8, 15, tzinfo=UTC)
    )
    # 연말은 ISO 주차라 해가 넘어간다. 손으로 세면 여기서 틀린다.
    assert refresh_stamp(datetime(2025, 12, 31, tzinfo=UTC)) == "2026W01"


def test_벌크파일이_묵으면_다시_받는다(tmp_path: Path) -> None:
    """"있으면 안 받는다" 로 두면 주식수가 그 날짜에 얼어붙는다 —
    화면은 멀쩡하고 낡은 것은 입력뿐인 실패 모양이다."""
    from quant_rl_trading.collectors.us_shares import SecBulkFacts

    blob = tmp_path / "companyfacts.zip"
    blob.write_bytes(b"not really a zip")
    facts = SecBulkFacts(path=blob, max_age_days=7)

    modified = datetime.fromtimestamp(blob.stat().st_mtime, tz=UTC)
    assert facts.age(now=modified + timedelta(days=3)) < timedelta(days=7)
    assert facts.age(now=modified + timedelta(days=9)) > timedelta(days=7)
    assert SecBulkFacts(path=tmp_path / "없다.zip").age(now=modified) == timedelta.max


def test_이미_적재된_값은_다시_넣지_않는다() -> None:
    known = existing_shares(
        [
            {
                "entity_id": "US:AAPL",
                "valid_from": datetime(2026, 7, 17, tzinfo=UTC),
                "value": 14594180000.0,
            }
        ]
    )
    assert known[("US:AAPL", date(2026, 7, 17))] == {14594180000.0}


def test_창고를_거쳐도_공시_전에는_안_보인다(store: Any) -> None:
    """게이트까지 통과하는 계약 테스트. 진짜 Parquet 을 깔고 진짜 store.get 을 부른다.

    AAPL 실제 공시(end=2026-07-17, filed=2026-07-31)를 넣고, 그 사이 시점
    조회가 **그것을 못 보는지** 확인한다. 여기가 뚫리면 밸류 팩터가 분기마다
    2주씩 미래를 본다.
    """
    rows = shares_rows(share_facts(facts("AAPL")), ticker="AAPL", since=date(2026, 1, 1))
    store.append("market_stats", rows, ingest_run_id="test-us-shares")

    mid_july = datetime(2026, 7, 25, tzinfo=UTC)
    seen = store.get("market_stats", as_of=mid_july, lookback=400, market=str(Market.US))
    assert datetime(2026, 7, 17, tzinfo=UTC) not in set(seen["valid_from"]), (
        "7월 25일에는 7월 17일 기준 주식수가 아직 공시되지 않았다"
    )

    after = store.get(
        "market_stats", as_of=datetime(2026, 8, 5, tzinfo=UTC), lookback=400,
        market=str(Market.US),
    )
    latest = after[after["valid_from"] == datetime(2026, 7, 17, tzinfo=UTC)]
    assert len(latest) == 1
    assert float(latest.iloc[0]["value"]) == 14594180000.0


def test_시가총액이_창고에_실제로_쌓인다(store: Any) -> None:
    """대시보드(`_leaders`)가 읽는 모양 그대로 들어가는지 — metric=market_cap,
    market=US, 그리고 그날 값이 그날 조회에 보이는지."""
    shares = shares_rows(share_facts(facts("AAPL")), ticker="AAPL", since=date(2026, 1, 1))
    store.append("market_stats", shares, ingest_run_id="test-us-shares")

    session = date(2026, 8, 3)
    lines = build_timelines(
        store.get(
            "market_stats", as_of=datetime(2026, 8, 5, tzinfo=UTC), lookback=400,
            market=str(Market.US),
        ).to_dict(orient="records")
    )
    caps = market_cap_rows([bar("AAPL", session, 200.0)], lines)
    store.append("market_stats", caps, ingest_run_id=market_cap_run_id(Market.US, session))

    frame = store.get(
        "market_stats", as_of=datetime(2026, 8, 4, tzinfo=UTC), lookback=5,
        market=str(Market.US),
    )
    only_caps = frame[frame["metric"] == MARKET_CAP]
    assert len(only_caps) == 1
    assert float(only_caps.iloc[0]["value"]) == pytest.approx(200.0 * 14594180000.0)


def test_창은_구간을_빠짐없이_덮는다() -> None:
    windows = list(year_windows(date(2021, 8, 15), date(2026, 8, 15)))

    assert windows[0][0] == date(2021, 8, 15)
    assert windows[-1][1] == date(2026, 8, 15)
    for earlier, later in pairwise(windows):
        assert later[0] == earlier[1] + timedelta(days=1), "틈이 없어야 한다"


def test_창_경계의_세션도_적재된다(store: Any) -> None:
    """``year_windows`` 는 틈이 없는데 **읽기가 틈을 만든다.**

    ``store.get`` 의 ``until`` 은 열린 끝(``valid_from < ?``)이고
    ``year_windows`` 의 ``window_end`` 는 닫힌 끝이다. 창 끝을 그대로 넘기면
    경계 하루가 어느 창에도 안 걸려 사라진다 — 위 테스트가 통과하는데도
    사라진다, 창이 아니라 창을 쓰는 쪽의 결함이기 때문이다.

    실측(2026-08-15, 5년 적재): NYSE 거래일 1,252일 중 1,249일만 시가총액이
    있었고 빠진 셋은 2023-08-14 · 2024-08-13 · 2025-08-13 — 전부 창 경계였다.
    """
    from quant_rl_trading.replay.clock import ReplayClock
    from tools.backfill import run_us_market_cap_backfill

    today = date(2026, 8, 14)
    now = datetime(2026, 8, 14, 12, tzinfo=UTC)
    # years=2 이면 창은 [today-730, today-366], [today-365, today] 로 갈린다.
    boundary = today - timedelta(days=366)

    store.append(
        "market_stats",
        [
            {
                "entity_id": "US:AAPL",
                "valid_from": datetime(2024, 1, 1, tzinfo=UTC),
                "observed_at": datetime(2024, 1, 2, tzinfo=UTC),
                "source": SOURCE,
                "market": str(Market.US),
                "metric": SHARES,
                "value": 1_000.0,
            }
        ],
        ingest_run_id="test-shares",
    )
    sessions = [boundary - timedelta(days=1), boundary, boundary + timedelta(days=1)]
    store.append(
        "prices",
        [dict(bar("AAPL", day, 10.0), source="ls_us", market=str(Market.US)) for day in sessions],
        ingest_run_id="test-prices",
    )

    assert run_us_market_cap_backfill(store, ReplayClock(now), years=2) == 0

    caps = store.get("market_stats", as_of=now, lookback=800, market=str(Market.US))
    caps = caps[caps["metric"] == MARKET_CAP]
    assert set(caps["valid_from"].dt.date) == set(sessions), "경계 세션이 빠졌다"
