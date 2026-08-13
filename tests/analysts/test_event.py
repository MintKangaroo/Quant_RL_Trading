"""Event Analyst.

여기서 고정하는 핵심은 **시장 전체 달력 이벤트가 점수에 들어가지 않는다**는
것이다. 전 종목에 같은 날 일어나는 사건은 횡단면 z 로 만들면 전부 0이 되어
Selector 에 아무 정보도 주지 않는다. 그걸 점수에 넣으면 계산만 늘고 IC 는 0인데,
"이벤트도 넣었다" 는 착각만 남는다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from lattice.analysts.event import (
    EventAnalyst,
    calendar_context,
    is_quarter_end_month,
    monthly_expiry,
)
from lattice.collectors.market_hours import Market
from lattice.replay.clock import ReplayClock

NOW = datetime(2026, 8, 12, tzinfo=UTC)
#: 관측시각은 세션 마감 후. 봉과 같은 규칙이다.
PUBLISH = timedelta(hours=7)


def sessions(count: int, *, end: date = date(2026, 8, 10)) -> list[date]:
    """단순 연속일. 거래일 달력은 여기서 검증할 대상이 아니다."""
    return [end - timedelta(days=offset) for offset in range(count - 1, -1, -1)]


@pytest.fixture
def seeded(store):  # type: ignore[no-untyped-def]
    """세 종목. 하나는 신규 상장, 하나는 거래정지 이력, 하나는 상폐."""
    store.seed_config_defaults()
    days = sessions(120)

    universe_rows = []
    price_rows = []
    for index, day in enumerate(days):
        stamp = datetime(day.year, day.month, day.day, tzinfo=UTC)
        observed = stamp + PUBLISH

        members = [("KR:000100", "고참", True)]
        if index >= 100:  # 최근 20세션에 상장한 신규주
            members.append(("KR:000200", "신입", True))
        # 상폐 종목: 마지막 세션에 is_listed=False
        members.append(("KR:000300", "퇴장", index < len(days) - 1))

        for entity, name, listed in members:
            universe_rows.append({
                "entity_id": entity, "valid_from": stamp, "observed_at": observed,
                "source": "test", "market": "KR", "name": name,
                "is_listed": listed, "is_tradable": listed, "delisted_on": None,
            })
            # 고참은 최근 10세션 중 5일 거래정지 (봉이 없다)
            halted = entity == "KR:000100" and len(days) - index <= 10 and index % 2 == 0
            if not halted:
                price_rows.append({
                    "entity_id": entity, "valid_from": stamp, "observed_at": observed,
                    "source": "test", "market": "KR",
                    "open": 1000.0, "high": 1010.0, "low": 990.0, "close": 1000.0 + index,
                    "volume": 10000.0, "value": 1.0e7, "adj_factor": None,
                })

    store.append("universe", universe_rows, ingest_run_id="u-seed")
    store.append("prices", price_rows, ingest_run_id="p-seed")
    return store


def run(seeded):  # type: ignore[no-untyped-def]
    analyst = EventAnalyst(seeded, ReplayClock(NOW), market=Market.KR)
    return {signal.entity_id: signal for signal in analyst.run(NOW)}


# -- 달력은 점수에 들어가지 않는다 ---------------------------------------------


def test_market_wide_calendar_is_not_scored(seeded) -> None:
    """만기일·분기말은 전 종목 공통이라 횡단면 정보가 없다.

    점수에 들어갔다면 모든 종목이 같은 값을 받아 z가 0이 되고, 그건 신호가
    아니라 낭비다. 피처 목록에 아예 없어야 한다.
    """
    analyst = EventAnalyst(seeded, ReplayClock(NOW), market=Market.KR)
    features = analyst.features(NOW)

    assert not features.empty
    for banned in ("days_to_expiry", "quarter_end_month", "expiry", "fomc"):
        assert banned not in features.columns


def test_calendar_is_available_as_context(seeded) -> None:
    """점수엔 없지만 맥락으로는 필요하다 — 그날이 만기였다는 사실이
    이상한 체결을 설명하는 경우가 있다."""
    context = {item.key: item for item in calendar_context(date(2026, 6, 11))}

    assert context["days_to_expiry"].value == 0.0     # 2026-06-11 = 둘째 목요일
    assert context["quarter_end_month"].value == 1.0  # 6월


def test_monthly_expiry_is_the_second_thursday() -> None:
    assert monthly_expiry(date(2026, 8, 1)) == date(2026, 8, 13)
    assert monthly_expiry(date(2026, 8, 31)) == date(2026, 8, 13)
    assert monthly_expiry(date(2026, 6, 30)) == date(2026, 6, 11)


def test_quarter_end_months() -> None:
    assert is_quarter_end_month(date(2026, 3, 5))
    assert not is_quarter_end_month(date(2026, 4, 5))


# -- 종목별 신호 -----------------------------------------------------------------


def test_new_listing_scores_below_the_veteran(seeded) -> None:
    """신규 상장주는 변동성이 크고 수급이 불안정하다."""
    signals = run(seeded)

    assert signals["KR:000200"].score < signals["KR:000100"].score


def test_delisted_symbol_gets_no_signal_at_all(seeded) -> None:
    """상폐는 감점이 아니라 **배제**다.

    가중 합에 감점으로 넣으면 "오래됐고 정지 이력도 없다" 는 장점이 상쇄해서
    상폐 종목이 양수를 받는다. 실제로 그렇게 만들었다가 이 테스트에 잡혔다
    (+0.11). 데이터 유니버스는 상폐를 품지만 매매 유니버스는 아니다.
    """
    signals = run(seeded)

    assert "KR:000300" not in signals
    assert {"KR:000100", "KR:000200"} <= set(signals)


def test_거래정지_피처는_더_이상_없다(seeded) -> None:
    """no_halt 는 뺐다 (2026-08-14). **코드가 아니라 데이터가 이유다.**

    이 테스트가 예전에 통과한 것은 합성 데이터에서 "유니버스에는 있고 봉은
    없는" 날을 일부러 만들었기 때문이다. 실제 KRX 는 정지 종목에도 거래량 0 인
    봉을 주므로 그 조건이 **한 번도 성립하지 않았다** — 실측 고유값 1개,
    표준편차 0.0000.

    상수 열은 점수에 아무것도 더하지 않으면서 가중치는 그대로 먹는다. 이
    피처는 event 가중치의 45% 를 들고 아무 일도 하지 않았다.

    합성 데이터로 통과하는 테스트가 **현실에서 그 코드가 도는지는 말해 주지
    않는다.** 진짜 거래정지 데이터가 들어오면 그때 다시 붙인다.
    """
    analyst = EventAnalyst(seeded, ReplayClock(NOW), market=Market.KR)
    features = analyst.features(NOW)

    assert "no_halt" not in features.columns


# -- 공통 계약 --------------------------------------------------------------------


def test_scores_are_bounded_and_evidence_present(seeded) -> None:
    signals = run(seeded)

    for signal in signals.values():
        assert -1.0 <= signal.score <= 1.0
        assert signal.analyst == "event"
        assert signal.evidence, "왜 이 점수인지 남아야 한다"


def test_confidence_is_not_self_assigned(seeded) -> None:
    """에이전트가 스스로 매기면 과신한다. 호출자가 롤링 IC 로 넣는다."""
    signals = run(seeded)

    assert all(signal.confidence == 0.0 for signal in signals.values())


def test_empty_store_yields_no_signals(store) -> None:
    """관측이 없으면 점수를 지어내지 않는다."""
    store.seed_config_defaults()

    assert EventAnalyst(store, ReplayClock(NOW), market=Market.KR).run(NOW) == []
