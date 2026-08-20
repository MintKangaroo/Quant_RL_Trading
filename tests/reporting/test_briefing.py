"""시황 브리핑 데이터 계층.

여기서 고정하는 사실은 이렇다.

1. **시장마다 직전 세션이 다르다** — 2026-08-17(월)은 국장 휴장·미장 개장이다.
   날짜 하나를 양쪽에 쓰면 그날 메일의 절반이 거짓이 된다
2. **없는 것은 없다고 적는다** — 지수가 안 들어왔으면 줄이 사라지는 게 아니라
   이유가 붙는다
3. **변동성 지수는 가격지수와 갈린다** — VIX 는 ``kind="volatility"`` 다
4. **상승 종목에 하한이 걸린다** — 동전주가 30% 올라도 시황이 아니다
5. **같은 as_of 면 같은 결과** — 벽시계를 읽는 곳이 없다
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pandas as pd
import pytest

from quant_rl_trading.collectors.market_hours import Market, is_trading_day
from quant_rl_trading.reporting import briefing as briefing_module
from quant_rl_trading.reporting.sessions import expected_session
from quant_rl_trading.store import Store

# 국장 08-14(금) 마감 · 미장 08-14(금) 마감이 모두 지난 시각.
NOW = datetime(2026, 8, 15, 6, 0, tzinfo=UTC)

KR_BIG, KR_PENNY, KR_THIN = "KR:005930", "KR:900001", "KR:900002"
US_BIG = "US:AAPL"


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
        entity,
        moment,
        market=market,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=value / max(close, 1e-9),
        value=value,
        adj_factor=1.0,
    )


def _index(entity: str, moment: datetime, market: str, close: float) -> dict[str, Any]:
    return _row(
        entity,
        moment,
        market=market,
        board="",
        open=close,
        high=close,
        low=close,
        close=close,
        volume=0.0,
        value=0.0,
    )


def _session(day: date, hour: int = 7) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


@pytest.fixture
def seeded(store: Store) -> Store:
    """국장 지수는 08-13 까지, 미장 지수는 08-14 까지.

    **일부러 어긋나게 깐다.** 2026-08-15 실제 창고가 그랬고, 그 모양에서
    메일이 이유를 말하는지가 이 테스트의 핵심이다.
    """
    store.seed_config_defaults()

    thursday, friday = date(2026, 8, 13), date(2026, 8, 14)

    store.append(
        "indices",
        [
            _index("KR:IDX:KOSPI", _session(date(2026, 8, 12)), "KR", 3000.0),
            _index("KR:IDX:KOSPI", _session(thursday), "KR", 3030.0),
            _index("KR:IDX:KOSDAQ", _session(date(2026, 8, 12)), "KR", 800.0),
            _index("KR:IDX:KOSDAQ", _session(thursday), "KR", 792.0),
            _index("US:IDX:SP500", _session(thursday), "US", 6000.0),
            _index("US:IDX:SP500", _session(friday), "US", 6060.0),
            _index("US:IDX:NASDAQ", _session(thursday), "US", 20000.0),
            _index("US:IDX:NASDAQ", _session(friday), "US", 20200.0),
            _index("US:IDX:VIX", _session(thursday), "US", 15.0),
            _index("US:IDX:VIX", _session(friday), "US", 18.0),
        ],
        ingest_run_id="idx-1",
    )
    store.append(
        "prices",
        [
            # 대형주 — 하한을 넉넉히 넘는다. +5%.
            _price(KR_BIG, _session(thursday), "KR", 70_000, 5e11),
            _price(KR_BIG, _session(friday), "KR", 73_500, 5e11),
            # 동전주 — +30% 지만 주가가 100원이다. 하한에 걸려야 한다.
            _price(KR_PENNY, _session(thursday), "KR", 100, 5e10),
            _price(KR_PENNY, _session(friday), "KR", 130, 5e10),
            # 거래대금 미달 — +20% 지만 하루 1천만원 거래다.
            _price(KR_THIN, _session(thursday), "KR", 50_000, 1e7),
            _price(KR_THIN, _session(friday), "KR", 60_000, 1e7),
            _price(US_BIG, _session(thursday), "US", 200.0, 5e9),
            _price(US_BIG, _session(friday), "US", 210.0, 5e9),
        ],
        ingest_run_id="px-1",
    )
    store.append(
        "universe",
        [
            _row(KR_BIG, _session(friday), market="KR", name="삼성전자",
                 is_listed=True, is_tradable=True, delisted_on=None),
            _row(KR_PENNY, _session(friday), market="KR", name="동전주",
                 is_listed=True, is_tradable=True, delisted_on=None),
            _row(KR_THIN, _session(friday), market="KR", name="한산주",
                 is_listed=True, is_tradable=True, delisted_on=None),
            _row(US_BIG, _session(friday), market="US", name="Apple",
                 is_listed=True, is_tradable=True, delisted_on=None),
        ],
        ingest_run_id="uni-1",
    )
    store.append(
        "fx",
        [
            _row("FX:USDKRW", _session(thursday), rate=1380.0),
            _row("FX:USDKRW", _session(friday), rate=1392.0),
        ],
        ingest_run_id="fx-1",
    )
    return store


# -- 세션 -----------------------------------------------------------------------


def test_market_sessions_differ_when_only_one_market_is_open(seeded: Store) -> None:
    """2026-08-17(월) — 국장 휴장, 미장 개장.

    좌우가 서로 다른 직전 세션을 갖는 날이 실재한다. 날짜 하나로 뭉치면
    이 날 국장 칸에 열리지도 않은 세션이 찍힌다.
    """
    # 미장 08-17 마감(16:00 ET = 20:00 UTC) + 공표지연 후.
    monday_night = datetime(2026, 8, 17, 21, 0, tzinfo=UTC)
    assert expected_session(seeded, Market.KR, as_of=monday_night) == date(2026, 8, 14)
    assert expected_session(seeded, Market.US, as_of=monday_night) == date(2026, 8, 17)


def test_expected_session_waits_for_publication(seeded: Store) -> None:
    """마감했다고 바로 기대 세션이 되지 않는다.

    미장 16:00 ET 마감 직후 20분은 아직 종가를 받을 수 없는 구간이다.
    그때 오늘을 기대 세션이라 부르면 메일이 매일 "미장 없음" 을 외친다.
    """
    just_closed = datetime(2026, 8, 14, 20, 5, tzinfo=UTC)  # 16:05 ET
    assert expected_session(seeded, Market.US, as_of=just_closed) == date(2026, 8, 13)
    after = datetime(2026, 8, 14, 20, 30, tzinfo=UTC)  # 16:30 ET, 공표지연 20분 경과
    assert expected_session(seeded, Market.US, as_of=after) == date(2026, 8, 14)


def test_substitute_holiday_is_a_closed_day_for_one_market_only(seeded: Store) -> None:
    """2026-08-17 은 광복절 대체공휴일 — **국장 휴장 · 미장 개장.**

    메일이 그날 국장 칸을 통째로 비우는 근거가 이 한 줄이다
    (``render._is_closed``). 달력이 이 날을 거래일로 되돌리면 08-14 종가가
    08-17 브리핑에 다시 실린다.
    """
    holiday = date(2026, 8, 17)
    assert is_trading_day(Market.KR, holiday) is False
    assert is_trading_day(Market.US, holiday) is True
    # 그 앞뒤는 두 시장 모두 거래일이다 — 억제가 하루에만 걸린다.
    for day in (date(2026, 8, 14), date(2026, 8, 18)):
        assert is_trading_day(Market.KR, day) is True


# -- 없는 것은 없다고 -------------------------------------------------------------


def test_missing_index_session_says_why(seeded: Store) -> None:
    """국장 지수가 08-13 까지고 08-14 가 없다 — 빈칸이 아니라 이유가 나와야 한다."""
    result = briefing_module.build_briefing(seeded, as_of=NOW)
    kr = result.markets["KR"]

    assert kr.index_session.expected == date(2026, 8, 14)
    assert kr.index_session.observed == date(2026, 8, 13)
    assert not kr.index_session.ok
    assert "2026-08-13" in (kr.index_session.note or "")
    # **달력일이 아니라 세션 수로 센다.** 금요일 것이 없는 채로 월요일에 보면
    # 달력으로는 3일이지만 빠진 세션은 하나다.
    assert "2026-08-14 까지 1개 세션이 안 들어왔다" in (kr.index_session.note or "")
    # 그 사실이 상단 요약(notes)까지 올라온다.
    assert any("지수" in note for note in result.notes)


def test_absent_index_keeps_its_row(seeded: Store) -> None:
    """창고에 아예 없는 지수(다우·SOX)는 목록에서 사라지지 않는다.

    줄이 사라지면 아무도 그것이 없다는 것을 모른다. 이 저장소의 규칙은
    "없는 것은 없다고 적는다" 이지 "없는 것은 안 보인다" 가 아니다.
    """
    result = briefing_module.build_briefing(seeded, as_of=NOW)
    labels = {row.label: row for row in result.markets["US"].prices}
    assert "다우" in labels
    assert labels["다우"].close is None
    assert labels["다우"].note == "창고에 이 지수가 없다"


def test_stale_index_row_is_labelled(seeded: Store) -> None:
    """08-13 종가를 08-14 인 것처럼 내보내지 않는다."""
    result = briefing_module.build_briefing(seeded, as_of=NOW)
    kospi = next(row for row in result.markets["KR"].prices if row.label == "코스피")
    assert kospi.close == 3030.0
    assert kospi.session == date(2026, 8, 13)
    assert "2026-08-14 미수집" in (kospi.note or "")


def test_stale_headline_index_reaches_the_top_summary(store: Store) -> None:
    """대표 지수 하나만 낡아도 맨 위에서 말한다.

    시장 세션 상태는 그 시장 지수 중 **가장 최신** 하나로 정해진다. 실측으로
    미장은 다우가 08-14, S&P 500 이 08-13 까지였다 — 그러면 시장 단위로는
    "다 들어왔다" 가 되고, 정작 제목 줄에 쓰는 대표 지수가 하루 낡은 채로
    조용히 나간다.
    """
    store.seed_config_defaults()
    store.append(
        "indices",
        [
            _index("US:IDX:SP500", _session(date(2026, 8, 12)), "US", 6000.0),
            _index("US:IDX:SP500", _session(date(2026, 8, 13)), "US", 6060.0),
            # 다우만 08-14 까지 들어왔다 — 시장 단위 판정은 이걸 보고 "정상" 이 된다.
            _index("US:IDX:DJIA", _session(date(2026, 8, 13)), "US", 53_000.0),
            _index("US:IDX:DJIA", _session(date(2026, 8, 14)), "US", 53_100.0),
        ],
        ingest_run_id="head-1",
    )
    result = briefing_module.build_briefing(store, as_of=NOW)
    assert result.markets["US"].index_session.ok, "시장 단위로는 정상으로 보인다"
    assert any("S&P 500" in note and "2026-08-14 미수집" in note for note in result.notes)


def test_multi_session_change_says_so(store: Store) -> None:
    """08-07 대비 08-13 을 "+3.5%" 라고만 적으면 하루 등락으로 읽힌다.

    창고에 구멍이 있는 동안 실제로 이런 줄이 나온다. 등락률 자체는 맞지만
    **기간이 틀리게 읽힌다** — 그래서 기준 세션을 함께 적는다.
    """
    store.seed_config_defaults()
    store.append(
        "indices",
        [
            _index("KR:IDX:KOSPI", _session(date(2026, 8, 7)), "KR", 3000.0),
            # 08-10·11·12 세션이 통째로 없다.
            _index("KR:IDX:KOSPI", _session(date(2026, 8, 13)), "KR", 3105.0),
        ],
        ingest_run_id="gap-1",
    )
    kospi = next(
        row
        for row in briefing_module.build_briefing(store, as_of=NOW).markets["KR"].prices
        if row.label == "코스피"
    )
    assert kospi.change == pytest.approx(0.035)
    assert "2026-08-07 대비" in (kospi.note or "")
    assert "3개 세션이 창고에 없다" in (kospi.note or "")


def test_adjacent_sessions_get_no_gap_note(seeded: Store) -> None:
    """08-12 → 08-13 은 붙어 있는 두 거래일이다. 군더더기를 붙이지 않는다."""
    kospi = next(
        row
        for row in briefing_module.build_briefing(seeded, as_of=NOW).markets["KR"].prices
        if row.label == "코스피"
    )
    assert "대비" not in (kospi.note or "")


def test_fx_gap_is_named(store: Store) -> None:
    """환율이 아예 없으면 0 이 아니라 문장이 나온다."""
    store.seed_config_defaults()
    result = briefing_module.build_briefing(store, as_of=NOW)
    assert result.fx["rate"] is None
    assert "USD/KRW" in (result.fx_note or "")


# -- 변동성 --------------------------------------------------------------------


def test_volatility_index_is_separated(seeded: Store) -> None:
    """VIX 는 가격지수 목록에 들어가지 않는다.

    한 표에 섞이면 VIX +20% 가 나스닥 +20% 와 같은 뜻으로 읽힌다 —
    실제로는 반대 사건이다.
    """
    us = briefing_module.build_briefing(seeded, as_of=NOW).markets["US"]
    price_ids = {row.entity_id for row in us.prices}
    vol_ids = {row.entity_id for row in us.volatility}

    assert "US:IDX:VIX" not in price_ids
    assert "US:IDX:VIX" in vol_ids
    assert all(row.kind == "volatility" for row in us.volatility)
    assert all(row.kind == "price" for row in us.prices)

    vix = next(row for row in us.volatility if row.entity_id == "US:IDX:VIX")
    assert vix.change == pytest.approx(0.2)  # 15 → 18


# -- 순위표 --------------------------------------------------------------------


def _ranked(brief: Any, key: str) -> Any:
    return next(rank for rank in brief.rankings if rank.key == key)


def test_rankings_apply_turnover_and_price_floor(seeded: Store) -> None:
    """동전주와 한산주가 순위표에 못 들어온다."""
    kr = briefing_module.build_briefing(seeded, as_of=NOW).markets["KR"]
    for key in ("value",):
        picked = {row.entity_id for row in _ranked(kr, key).rows}
        assert picked == {KR_BIG}, f"{key} 순위에 하한이 안 걸렸다"
        assert KR_PENNY not in picked, "주가 하한이 동전주를 못 걸렀다"
        assert KR_THIN not in picked, "거래대금 하한이 한산주를 못 걸렀다"
    assert kr.floor.eligible == 1
    # 하한값 자체를 들고 다녀야 메일 각주에 적을 수 있다.
    assert kr.floor.min_turnover == 1_000_000_000
    assert kr.floor.min_price == 1000
    assert kr.floor.currency == "KRW"


def test_floor_is_per_currency(seeded: Store) -> None:
    """원과 달러를 한 숫자로 재지 않는다. $5M 을 5,000,000,000 원과 견주면
    미장 순위가 통째로 비어 버린다."""
    us = briefing_module.build_briefing(seeded, as_of=NOW).markets["US"]
    assert us.floor.currency == "USD"
    assert us.floor.min_turnover == 5_000_000
    assert {row.entity_id for row in _ranked(us, "value").rows} == {US_BIG}


def test_rankings_are_two_and_say_their_sort_key(seeded: Store) -> None:
    """표마다 무엇으로 줄 세웠는지 적는다 — 안 적으면 무엇을 본 건지 모른다."""
    kr = briefing_module.build_briefing(seeded, as_of=NOW).markets["KR"]
    assert [rank.key for rank in kr.rankings] == ["value", "market_cap"]
    assert all(rank.sort_by for rank in kr.rankings)
    assert _ranked(kr, "value").sort_by != _ranked(kr, "market_cap").sort_by


def test_volume_ranking_is_gone_and_stays_gone(store: Store) -> None:
    """거래량(주식 수) 순위표는 **일부러 없앴다.** 다시 넣지 마라.

    한때 있었고, 주식 수로 세면 싼 주식이 늘 이겨서 뺐다 — 2026-08-12 미장
    1·5위가 $1.52·$1.36 짜리였다. **하한이 없어서가 아니다**(하한은 걸려
    있었고 둘 다 통과했다). 주식 수라는 척도 자체가 싼 쪽에 기울어 있어서
    하한은 바닥만 자를 뿐 순위를 못 바꾼다.

    아래 데이터가 그 기울기를 보여준다 — 싼 주식이 거래대금은 작은데
    거래량은 크다. 거래량 표가 되살아나면 이 종목이 1위로 올라온다.
    """
    store.seed_config_defaults()
    friday, thursday = date(2026, 8, 14), date(2026, 8, 13)
    rows = []
    for entity, close, value in (
        ("KR:100001", 2_000.0, 4e10),    # 싼 주식 — 거래량이 크다
        ("KR:100002", 500_000.0, 5e10),  # 비싼 주식 — 거래대금이 크다
    ):
        for day in (thursday, friday):
            rows.append(_price(entity, _session(day), "KR", close, value))
    store.append("prices", rows, ingest_run_id="rank-1")
    kr = briefing_module.build_briefing(store, as_of=NOW).markets["KR"]

    assert "volume" not in [rank.key for rank in kr.rankings], (
        "거래량 순위표가 되살아났다 — 왜 없앴는지 이 독스트링을 읽어라"
    )
    # 거래대금 1위는 비싼 주식이다. 거래량으로 세웠다면 싼 쪽이 1위였다.
    assert _ranked(kr, "value").rows[0].entity_id == "KR:100002"


def test_unmeasured_change_stays_none(store: Store) -> None:
    """직전 종가가 없는 종목의 등락률은 None 이다. **0.0 이 아니다** —
    0% 는 "보합" 이라는 다른 사실이다."""
    store.seed_config_defaults()
    store.append(
        "prices",
        [_price("KR:100003", _session(date(2026, 8, 14)), "KR", 50_000.0, 9e10)],
        ingest_run_id="new-1",
    )
    kr = briefing_module.build_briefing(store, as_of=NOW).markets["KR"]
    row = _ranked(kr, "value").rows[0]
    assert row.entity_id == "KR:100003"
    assert row.change is None


def test_market_cap_ranking_reports_coverage(seeded: Store) -> None:
    """시총은 아는 종목 중 상위다. 몇 종목을 아는지 들고 다녀야 각주를 쓴다."""
    store_brief = briefing_module.build_briefing(seeded, as_of=NOW).markets["KR"]
    cap = _ranked(store_brief, "market_cap")
    # 창고에 market_stats 를 안 깔았다 — 없으면 없다고 적어야 한다.
    assert cap.rows == []
    assert cap.note is not None


def test_market_cap_ignores_future_dated_rows(store: Store) -> None:
    """**표지 날짜가 미래인 행을 최신으로 집으면 안 된다.**

    창고의 미장 ``shares`` 에 실제로 2028-08-01 짜리 행이 있다. ``lookback``
    은 valid_from 의 하한만 자르므로, ``until`` 없이 읽으면 2년 뒤 값이
    "최신" 으로 뽑혀 순위가 통째로 뒤집힌다.
    """
    store.seed_config_defaults()
    today, future = date(2026, 8, 14), date(2028, 8, 1)
    store.append(
        "market_stats",
        [
            _row("KR:100001", _session(today), market="KR", metric="market_cap", value=1e12),
            _row("KR:100002", _session(today), market="KR", metric="market_cap", value=2e12),
            # 미래 표지. 관측시각은 지금이라 게이트는 통과한다.
            {
                "entity_id": "KR:100001",
                "valid_from": _session(future),
                "observed_at": _session(today),
                "source": "test",
                "market": "KR",
                "metric": "market_cap",
                "value": 9e15,
            },
        ],
        ingest_run_id="cap-1",
    )
    kr = briefing_module.build_briefing(store, as_of=NOW).markets["KR"]
    cap = _ranked(kr, "market_cap")
    assert cap.session == today, "미래 표지 세션이 최신으로 뽑혔다"
    assert cap.rows[0].entity_id == "KR:100002"
    assert cap.rows[0].metric == 2e12


# -- 결정론 --------------------------------------------------------------------


def test_same_as_of_gives_same_briefing(seeded: Store) -> None:
    """리포트의 결정론 테스트 (reporting.md §5).

    같은 as_of 로 두 번 만들면 바이트 단위로 같아야 한다. 하나라도 벽시계를
    읽으면 여기서 깨진다 (불변식 2).
    """
    first = briefing_module.build_briefing(seeded, as_of=NOW).as_dict()
    second = briefing_module.build_briefing(seeded, as_of=NOW).as_dict()
    assert first == second


def test_as_of_hides_the_future(seeded: Store) -> None:
    """되감으면 그 시점 이후 값이 안 보인다 (불변식 9)."""
    earlier = datetime(2026, 8, 14, 6, 0, tzinfo=UTC)
    kr = briefing_module.build_briefing(seeded, as_of=earlier).markets["KR"]
    kospi = next(row for row in kr.prices if row.label == "코스피")
    # 08-13 종가는 그날 16:00 KST 이후 관측이라 08-14 06:00 UTC 에는 보인다.
    assert kospi.session == date(2026, 8, 13)
    # 08-14 시세(그날 07:00 UTC 관측)는 아직 안 보인다.
    assert kr.price_session.observed == date(2026, 8, 13)


# -- 매매 섹션은 없다 -------------------------------------------------------------


def test_briefing_carries_no_performance_section(seeded: Store) -> None:
    """실매매가 없는 동안 성과·보유 섹션을 만들지 않는다.

    빈 표를 내보내면 읽는 사람이 그것을 "손실 0" 으로 읽는다. 없는 것을
    0 으로 그리는 것이 이 저장소가 가장 경계하는 실패다.
    """
    payload = briefing_module.build_briefing(seeded, as_of=NOW).as_dict()
    flat = str(payload)
    for banned in ("pnl", "nav", "positions", "holdings", "return_pct"):
        assert banned not in flat



# -- RSI ----------------------------------------------------------------------


def test_wilder_rsi_boundaries() -> None:
    """경계에서 손으로 검산한다. **0 으로 나누는 자리가 있다.**

    하락이 하나도 없는 구간은 평균손실이 0 이라 그냥 두면 inf 가 나온다.
    inf 하나가 조용히 퍼지는 사고를 이 저장소는 이미 겪었다
    (analysts 를 침묵시킨 종가 0 세션).
    """
    from quant_rl_trading.reporting.briefing import wilder_rsi

    assert wilder_rsi(pd.Series(range(1, 40), dtype=float), 14) == 100.0
    assert wilder_rsi(pd.Series(range(40, 1, -1), dtype=float), 14) == 0.0
    # 움직임이 아예 없으면 상승도 하락도 0 이다. 100 이 아니라 50 이다 —
    # "쉼 없이 오르는 중" 과 "안 움직임" 은 정반대다.
    assert wilder_rsi(pd.Series([100.0] * 40), 14) == 50.0


def test_wilder_rsi_returns_none_when_short() -> None:
    """표본이 모자라면 **None**. 0 으로 채우지 않는다 — 0 은 극단적
    과매도라는 뜻이고 "못 쟀다" 와 정반대의 말이다."""
    from quant_rl_trading.reporting.briefing import wilder_rsi

    assert wilder_rsi(pd.Series([1.0, 2.0, 3.0]), 14) is None
    # 기간과 딱 같아도 차분이 하나 모자라다.
    assert wilder_rsi(pd.Series(range(14), dtype=float), 14) is None
    assert wilder_rsi(pd.Series(range(15), dtype=float), 14) is not None


def test_index_row_carries_rsi() -> None:
    """as_dict 가 RSI 를 싣는다 — 화면·API 가 같은 값을 본다."""
    from quant_rl_trading.reporting.briefing import IndexRow

    row = IndexRow(
        entity_id="US:IDX:NASDAQ", label="나스닥", kind="price",
        close=26331.1, change=0.0016, session=date(2026, 8, 19),
        rsi=53.3, rsi_zone=None,
    )
    payload = row.as_dict()
    assert payload["rsi"] == 53.3
    assert payload["rsi_zone"] is None

    # 값이 없으면 키는 있고 값이 None 이다. 키가 사라지면 화면이 옛 메일과
    # 새 메일에서 다른 모양을 그린다.
    bare = IndexRow(
        entity_id="US:IDX:DOW", label="다우", kind="price",
        close=None, change=None, session=None,
    )
    assert bare.as_dict()["rsi"] is None
