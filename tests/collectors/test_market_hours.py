"""거래일 달력 — 라이브러리가 모르는 휴장일을 덮는 예외층.

``exchange_calendars`` 의 XKRX 는 **뒤늦게 지정된 한국 공휴일을 모른다.**
선거일과 법 개정으로 부활한 공휴일은 달력이 배포된 뒤에 정해지기 때문이다.
실측(창고 `prices` 5년 대조): 그런 날이 2026 년에 둘 있었고, 두 날 모두
KRX 는 전 종목 종가 0 으로 응답했다 — 응답이 맞고 달력이 틀렸다.

유령 거래일 하나가 커버리지(verify_m1), 벤치마크 결측 판정, Data Quality
결측 경고를 한꺼번에 오염시킨다. 셋 다 "그 시장의 거래일" 을 분모로 쓴다.
"""

from __future__ import annotations

from datetime import date

import exchange_calendars as xcals
import pytest

from quant_rl_trading.collectors.market_hours import (
    _KR_EXTRA_HOLIDAYS,
    _OVERRIDES,
    SPECS,
    Market,
    is_trading_day,
    previous_trading_day,
    trading_days,
)

#: 실측으로 확인한 유령 거래일. 달력은 거래일이라 했고 KRX 는 휴장이라 했다.
GHOST_DAYS = [
    date(2026, 6, 3),   # 제9회 전국동시지방선거
    date(2026, 7, 17),  # 제헌절 (2026년 공휴일 재지정)
]


@pytest.mark.parametrize("day", GHOST_DAYS)
def test_ghost_days_are_not_kr_trading_days(day: date) -> None:
    assert is_trading_day(Market.KR, day) is False


@pytest.mark.parametrize("day", GHOST_DAYS)
def test_ghost_days_are_absent_from_trading_days_range(day: date) -> None:
    span = trading_days(Market.KR, date(2026, 1, 1), date(2026, 12, 31))
    assert day not in span
    assert span == sorted(span)


@pytest.mark.parametrize(
    "day",
    [date(2026, 6, 2), date(2026, 6, 4), date(2026, 7, 16), date(2026, 7, 20)],
)
def test_neighbours_stay_trading_days(day: date) -> None:
    """예외층이 옆날까지 지우면 안 된다.

    창고 대조에서 역방향(달력=휴장, 실제=개장)은 5년간 0건이었다. 즉 달력을
    **깎기만** 해야 하고 덧붙일 것은 없다.
    """
    assert is_trading_day(Market.KR, day) is True


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2026, 6, 4), date(2026, 6, 2)),   # 6/3 을 건너뛴다
        (date(2026, 7, 20), date(2026, 7, 16)),  # 7/17 과 주말을 건너뛴다
    ],
)
def test_previous_trading_day_skips_ghost_days(day: date, expected: date) -> None:
    """``previous_session`` 을 한 번만 부르면 유령 날을 답한다.

    수집기(`market_collector`)가 "오늘이 휴장이면 직전 거래일" 로 대상일을
    고른다. 여기서 유령 날이 나오면 종가 0 인 날을 다시 긁는다.
    """
    assert previous_trading_day(Market.KR, day) == expected


def test_override_entries_are_still_needed() -> None:
    """라이브러리가 따라잡았으면 예외 항목을 지워야 한다.

    이 테스트가 깨지면 결함이 아니라 **정리 신호**다. 예외층은 라이브러리와의
    차이만 들어야 하고, 이미 반영된 날을 계속 들고 있으면 다음 사람이 무엇이
    살아 있는 예외인지 모른다.
    """
    calendar = xcals.get_calendar(SPECS[Market.KR].calendar)
    stale = [
        day for day in sorted(_KR_EXTRA_HOLIDAYS)
        if not calendar.is_session(day.isoformat())
    ]
    assert not stale, (
        f"exchange_calendars 가 이제 이 날들을 휴장으로 안다: {stale}. "
        "market_hours._KR_EXTRA_HOLIDAYS 에서 지워라."
    )


def test_override_days_are_weekdays() -> None:
    """주말을 예외로 넣는 것은 무의미하다 — 이미 휴장이다.

    주말이 목록에 들어 있다면 날짜를 잘못 옮겨 적었다는 뜻이다.
    """
    weekend = [day for day in sorted(_KR_EXTRA_HOLIDAYS) if day.weekday() >= 5]
    assert not weekend


def test_us_override_is_empty() -> None:
    """XNYS 는 손댈 이유가 없다. 비어 있음을 못 박아 조용한 추가를 막는다."""
    override = _OVERRIDES[Market.US]
    assert not override.extra_holidays
    assert not override.extra_sessions


def test_no_extra_sessions_yet() -> None:
    """반대 방향은 아직 비어 있다.

    5년 대조에서 "달력은 휴장인데 실제로 장이 선 날" 은 0건이었다. 여기에
    무언가 들어간다면 새 실측 근거가 붙어야 한다.
    """
    assert not _OVERRIDES[Market.KR].extra_sessions
