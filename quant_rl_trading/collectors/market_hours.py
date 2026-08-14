"""장 운영시간·휴장일.

port(collectors): LS_USA scheduling/market_hours.py 의 **방식**을 이식한다.
코드가 아니라 접근법이다 — 라이브러리 조회(XNYS)로 휴장일을 얻는 방식.

LS_KR 은 휴장일을 27줄짜리 수작업 텍스트 파일(`config/krx_holidays.txt`)로
들고 있었고 2026년만 커버했다. 갱신을 잊으면 **조용히 틀린다** — 휴장일에
주문을 내거나, 거래일을 휴장일로 착각해 하루를 통째로 건너뛴다.
Quant_RL_Trading 는 KR 도 라이브러리(XKRX)로 조회한다 (postmortem-ls.md §6-5).

DST 는 라이브러리와 tz 데이터가 처리한다. 손으로 계산하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
import threading
from enum import StrEnum
from functools import lru_cache
from zoneinfo import ZoneInfo

import exchange_calendars as xcals


class Market(StrEnum):
    KR = "KR"
    US = "US"


@dataclass(frozen=True)
class MarketSpec:
    calendar: str
    timezone: str
    regular_open: time
    regular_close: time


SPECS: dict[Market, MarketSpec] = {
    # port: LS_KR scheduling/market_hours.py:23-24 (정규장 09:00~15:30 KST)
    Market.KR: MarketSpec("XKRX", "Asia/Seoul", time(9, 0), time(15, 30)),
    # port: LS_USA scheduling/market_hours.py:24-25 (09:30~16:00 ET)
    Market.US: MarketSpec("XNYS", "America/New_York", time(9, 30), time(16, 0)),
}


#: 달력 만들기를 직렬화한다. ``lru_cache`` 는 **동시에 들어온 첫 호출 둘을
#: 막지 못한다** — 두 스레드가 같이 달력을 지으면 exchange_calendars 내부
#: 상태가 엉켜 엉뚱한 곳에서 터진다. 실측(대시보드 병렬 요청):
#:
#:     ValueError: Length of values (999) does not match length of index (998)
#:     KeyError: Timestamp('2072-06-06 00:00:00')
#:
#: 지어진 뒤에는 읽기만 하므로 잠글 것이 없다. 잠그는 것은 짓는 순간뿐이다.
_CALENDAR_LOCK = threading.Lock()


@lru_cache(maxsize=4)
def _calendar(name: str) -> xcals.ExchangeCalendar:
    with _CALENDAR_LOCK:
        return xcals.get_calendar(name)


def local_time(market: Market, moment: datetime) -> datetime:
    """UTC 시각을 그 시장의 지역 시각으로. 입력은 항상 tz-aware 여야 한다."""
    if moment.tzinfo is None:
        raise ValueError(f"타임존 없는 시각: {moment!r}")
    return moment.astimezone(ZoneInfo(SPECS[market].timezone))


def is_trading_day(market: Market, day: date) -> bool:
    return bool(_calendar(SPECS[market].calendar).is_session(day.isoformat()))


def is_regular_session(market: Market, moment: datetime) -> bool:
    """정규장 중인지. 주문 가능 여부의 기준."""
    spec = SPECS[market]
    here = local_time(market, moment)
    if not is_trading_day(market, here.date()):
        return False
    return spec.regular_open <= here.time() < spec.regular_close


def previous_trading_day(market: Market, day: date) -> date:
    calendar = _calendar(SPECS[market].calendar)
    session = calendar.previous_session(day.isoformat())
    return session.date()  # type: ignore[no-any-return]


def trading_days(market: Market, start: date, end: date) -> list[date]:
    calendar = _calendar(SPECS[market].calendar)
    sessions = calendar.sessions_in_range(start.isoformat(), end.isoformat())
    return [session.date() for session in sessions]
