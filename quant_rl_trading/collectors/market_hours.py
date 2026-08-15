"""장 운영시간·휴장일.

port(collectors): LS_USA scheduling/market_hours.py 의 **방식**을 이식한다.
코드가 아니라 접근법이다 — 라이브러리 조회(XNYS)로 휴장일을 얻는 방식.

LS_KR 은 휴장일을 27줄짜리 수작업 텍스트 파일(`config/krx_holidays.txt`)로
들고 있었고 2026년만 커버했다. 갱신을 잊으면 **조용히 틀린다** — 휴장일에
주문을 내거나, 거래일을 휴장일로 착각해 하루를 통째로 건너뛴다.
Quant_RL_Trading 는 KR 도 라이브러리(XKRX)로 조회한다 (postmortem-ls.md §6-5).

DST 는 라이브러리와 tz 데이터가 처리한다. 손으로 계산하지 않는다.

다만 라이브러리도 **뒤늦게 지정된 휴일은 모른다** — 선거일이나 법 개정으로
부활한 공휴일은 달력이 지어진 뒤에 정해진다. 그래서 라이브러리 위에 얇은
예외층(``_OVERRIDES``)을 덮는다. LS_KR 의 27줄 텍스트 파일로 돌아가는 것이
아니다: 그쪽은 **전체 휴일을 손으로 들었고** 갱신을 잊으면 조용히 틀렸다.
여기서는 라이브러리가 진실의 원천이고 예외층은 **차이만** 든다. 잊는 것은
``tests/collectors/test_market_hours.py`` 의 가드가 막는다.
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


@dataclass(frozen=True)
class CalendarOverride:
    """라이브러리 달력과 실제의 **차이**.

    ``extra_holidays``: 달력은 거래일이라 하나 실제로는 휴장인 날.
    ``extra_sessions``: 달력은 휴장이라 하나 실제로는 장이 선 날.

    반대 방향(``extra_sessions``)은 지금 비어 있다. 창고의 KR 세션
    5년치(2021-08~2026-08, 1,221 세션)를 대조했을 때 한 건도 없었다.
    칸을 남겨두는 이유는 다음에 그 방향이 나왔을 때 구조를 새로 짜지
    않기 위해서다.
    """

    extra_holidays: frozenset[date] = frozenset()
    extra_sessions: frozenset[date] = frozenset()


#: 라이브러리(exchange_calendars 4.13.2, XKRX)가 모르는 KR 휴장일.
#:
#: **창고 실측으로 확인한 것만 넣는다.** 두 날 모두 KRX 에 시세를 요청하면
#: 전 종목 종가가 0 으로 오고, ``krx_source.ohlcv_on`` 이 그것을 휴장으로
#: 막는다. 즉 응답이 맞고 달력이 틀렸다.
#:
#: 5년 대조에서 나온 유령 거래일은 정확히 이 둘뿐이다. 세 번째 후보였던
#: 2026-08-11 은 **창고 행이 0건** 이라 성격이 다르다 — 공휴일이 아니고
#: 수집이 안 돈 날이다. 그런 날을 여기 넣으면 수집 실패가 휴장으로
#: 세탁되고 커버리지가 영원히 100% 로 보인다. 넣지 않는다.
_KR_EXTRA_HOLIDAYS = frozenset(
    {
        date(2026, 6, 3),   # 제9회 전국동시지방선거 — 선거일은 달력이 지어진 뒤 정해진다
        date(2026, 7, 17),  # 제헌절 — 2026년부터 공휴일로 재지정
    }
)

_OVERRIDES: dict[Market, CalendarOverride] = {
    Market.KR: CalendarOverride(extra_holidays=_KR_EXTRA_HOLIDAYS),
    Market.US: CalendarOverride(),
}

#: **예외 목록을 어디까지 실제로 대조했는가.**
#:
#: 이 날까지는 XKRX 가 내놓는 KR 휴장일을 한 날씩 훑어 확인했다. 그 뒤는
#: 확인하지 않았다 — 모르는 구간이지 "빠진 날이 없는" 구간이 아니다.
#:
#: 이 상수가 없으면 목록은 LS_KR 의 27줄 텍스트 파일과 같은 것이 된다.
#: 그 파일이 조용히 썩은 이유는 목록이 짧아서가 아니라 **언제까지 유효한지
#: 아무도 안 적었고 아무도 안 물어봤기 때문**이다. 여기서는
#: ``tests/collectors/test_market_hours.py`` 의 가드가 이 날이 가까워지면
#: 실패해서 사람에게 다음 구간을 실측으로 채우게 한다.
#:
#: **갱신 방법**: 짐작으로 날짜만 늘리지 마라. 그 구간의 KRX 휴장일을 실제로
#: 확인하고(창고 대조 또는 KRX 응답), 빠진 날을 위 목록에 넣은 뒤 이 날을
#: 옮긴다. 확인 못 한 날을 넣는 쪽이 빠뜨리는 쪽보다 위험하다 — 빠뜨리면
#: 전 종목 종가 0 으로 드러나지만, 잘못 넣으면 장이 열린 날을 조용히
#: 건너뛴다.
VETTED_THROUGH = date(2027, 8, 13)

#: 가드가 몇 날 전에 울리는가. 실측에는 사람의 시간이 필요하므로 만료 당일이
#: 아니라 여유를 두고 깨진다.
VETTING_NOTICE_DAYS = 90


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
    override = _OVERRIDES[market]
    if day in override.extra_holidays:
        return False
    if day in override.extra_sessions:
        return True
    return bool(_calendar(SPECS[market].calendar).is_session(day.isoformat()))


def is_regular_session(market: Market, moment: datetime) -> bool:
    """정규장 중인지. 주문 가능 여부의 기준."""
    spec = SPECS[market]
    here = local_time(market, moment)
    if not is_trading_day(market, here.date()):
        return False
    return spec.regular_open <= here.time() < spec.regular_close


def previous_trading_day(market: Market, day: date) -> date:
    """``day`` 직전 거래일. ``day`` 자신은 세지 않는다.

    예외층이 준 휴장일은 건너뛴다. 라이브러리 ``previous_session`` 을 한 번만
    부르면 2026-06-04 의 직전을 2026-06-03(유령)으로 답한다.
    """
    calendar = _calendar(SPECS[market].calendar)
    override = _OVERRIDES[market]
    cursor = day
    while True:
        session = calendar.previous_session(cursor.isoformat()).date()
        # 라이브러리가 건너뛴 구간에 예외 세션이 있으면 그쪽이 더 가깝다.
        nearer = [d for d in override.extra_sessions if session < d < cursor]
        candidate = max(nearer) if nearer else session
        if candidate not in override.extra_holidays:
            return candidate
        cursor = candidate


def trading_days(market: Market, start: date, end: date) -> list[date]:
    calendar = _calendar(SPECS[market].calendar)
    override = _OVERRIDES[market]
    sessions = {session.date() for session in calendar.sessions_in_range(start.isoformat(), end.isoformat())}
    sessions -= override.extra_holidays
    sessions |= {d for d in override.extra_sessions if start <= d <= end}
    return sorted(sessions)
