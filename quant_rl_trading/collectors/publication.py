"""관측시각 정책 — 어떤 레코드를 "언제 알 수 있었나" 로 찍을지.

백필에서 가장 쉽게, 가장 조용히 망가지는 지점이다.

과거 5년치를 오늘 내려받아 ``observed_at`` 을 **오늘**로 찍으면, 5년치 전부가
"2026-08-10 에 알았다" 가 된다. 게이트는 정직하게 동작해서 2023년 조회에
아무것도 돌려주지 않고, 리플레이는 텅 빈 시장 위에서 돈다. 반대로 봉의 날짜를
그대로 찍으면 그날 자정부터 종가를 알고 있던 것이 되어 전 구간이 미래를 본다.

정답은 **원 공표 시각** — 그 세션의 종료 시각에 공표 지연을 더한 순간이다.
그때 그 값은 실제로 공개돼 있었고, 그 시점 이후의 조회가 그것을 보는 것은 옳다.

라이브와 백필이 갈리는 지점은 이 정책 객체 하나뿐이다. ``if backfill:`` 분기를
만들지 않는다 (불변식 5) — 정규화 코드는 양쪽에서 글자 하나까지 같다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol, runtime_checkable
from zoneinfo import ZoneInfo

from quant_rl_trading.collectors.errors import CollectorError
from quant_rl_trading.collectors.market_hours import SPECS, Market, is_trading_day
from quant_rl_trading.replay.clock import Clock
from quant_rl_trading.store import Store


class NotYetPublished(CollectorError):
    """아직 공표되지 않은 사실을 저장하려 했다."""


class NotATradingDay(CollectorError):
    """휴장일에는 세션이 없고, 세션이 없으면 공표 시각도 없다."""


@runtime_checkable
class ObservedAtPolicy(Protocol):
    """거래일 하나를 관측시각 하나로 바꾼다. 그것만 한다.

    ``extra_lag_days`` 는 **데이터셋마다 다른 공표 지연**을 위한 것이다.
    같은 세션의 사실이라도 일봉은 당일 마감 직후에 나오고 공매도 잔고는
    T+1~2 에 나온다. 하나의 지연으로 뭉뚱그리면 늦게 나오는 쪽이 통째로
    미래를 본다.
    """

    def for_session(self, day: date, *, extra_lag_days: int = 0) -> datetime: ...


@dataclass(frozen=True)
class FetchTimePolicy:
    """라이브 수집 경로 — 관측시각은 우리가 받아온 시각이다.

    실시간으로 받는 값은 공표 시각보다 늦게 도착한다. 늦은 쪽이 진실이므로
    실제 수신 시각을 찍는다. 공표 지연은 이미 수신 시각에 반영돼 있으므로
    ``extra_lag_days`` 를 더하지 않는다.
    """

    clock: Clock

    def for_session(self, day: date, *, extra_lag_days: int = 0) -> datetime:
        return self.clock.now()


@dataclass(frozen=True)
class PublicationPolicy:
    """백필 경로 — 관측시각은 그 사실이 공개된 시각이다.

    세션 종료 지역시각 + 공표 지연 → UTC. 지역시각을 거치므로 서머타임은
    tz 데이터가 처리한다. 손으로 계산하지 않는다.
    """

    market: Market
    lag_seconds: float
    clock: Clock

    def for_session(self, day: date, *, extra_lag_days: int = 0) -> datetime:
        if not is_trading_day(self.market, day):
            raise NotATradingDay(f"{self.market} {day.isoformat()} 는 거래일이 아니다")

        spec = SPECS[self.market]
        close_local = datetime.combine(
            day, spec.regular_close, tzinfo=ZoneInfo(spec.timezone)
        )
        # 지연은 거래일로 센다. 달력일로 세면 금요일 세션의 T+2 가 일요일이
        # 되어, 실제로는 화요일에야 알 수 있는 것을 일요일부터 아는 게 된다.
        for _ in range(max(extra_lag_days, 0)):
            close_local = _next_session_close(self.market, close_local, spec.regular_close)
        published = (close_local + timedelta(seconds=self.lag_seconds)).astimezone(UTC)

        now = self.clock.now()
        if published > now:
            # 오늘 장 마감 전에 오늘 봉을 백필하려는 경우가 여기 걸린다.
            # 미래에 공표될 값을 지금 저장하면, 그 값은 저장된 순간부터
            # 아무도 검증할 수 없는 미래 정보가 된다.
            raise NotYetPublished(
                f"{self.market} {day.isoformat()} 세션은 {published.isoformat()} 에 "
                f"공표된다. 현재 {now.isoformat()} — 아직 알 수 없는 사실이다"
            )
        return published


def _next_session_close(market: Market, moment: datetime, close: time) -> datetime:
    """다음 거래일의 마감 지역시각."""
    day = moment.date() + timedelta(days=1)
    while not is_trading_day(market, day):
        day += timedelta(days=1)
    return datetime.combine(day, close, tzinfo=moment.tzinfo)


CONFIG_LAG_KEYS: dict[Market, str] = {
    Market.KR: "backfill.kr_publication_lag_seconds",
    Market.US: "backfill.us_publication_lag_seconds",
}


def publication_policy(store: Store, market: Market, *, clock: Clock) -> PublicationPolicy:
    """임계치를 store.config 에서 읽어 정책을 만든다 (불변식 10).

    ``as_of`` 는 현재 시각이다 — 지금 이 백필이 어떤 지연 설정으로 돌았는지는
    지금 발효 중인 값으로 결정되고, 그 값 자체가 config 테이블에 이중시간으로
    남아 있어 나중에 재구성된다.
    """
    now = clock.now()
    lag = store.config(CONFIG_LAG_KEYS[market], as_of=now)
    return PublicationPolicy(market=market, lag_seconds=float(lag), clock=clock)


def resolve(policy: ObservedAtPolicy | datetime, day: date) -> datetime:
    """정책이든 고정 시각이든 관측시각 하나로 좁힌다.

    라이브 호출부가 이미 계산해 둔 ``datetime`` 을 그대로 넘길 수 있게 해서,
    정규화 함수가 두 갈래로 갈라지지 않게 한다.
    """
    if isinstance(policy, datetime):
        return policy
    return policy.for_session(day)


#: 발행 일정을 **확인하지 못한** 데이터셋에서, 공표 추정치를 수집 시각까지
#: 끌어올릴 창. 이 안에서 수집됐다면 "그날 것을 그날 받았다" 이므로 하한을
#: 건다. 이보다 오래된 세션은 추정치를 그대로 둔다 — 5년치를 오늘 내려받았다고
#: 5년치 전부를 "오늘 알았다" 로 찍으면 게이트가 정직하게 동작해서 리플레이가
#: 빈 시장 위에서 돈다(모듈 서두).
SCHEDULE_UNKNOWN_WINDOW = timedelta(days=1)


def not_before_collection(published: datetime, collected_at: datetime) -> datetime:
    """공표 추정치와 수집 시각 중 **늦은 쪽**. 단, 최근 세션에만.

    ``published`` 가 ``SCHEDULE_UNKNOWN_WINDOW`` 보다 오래됐으면 그대로 둔다.
    """
    if collected_at <= published:
        return published
    if collected_at - published > SCHEDULE_UNKNOWN_WINDOW:
        return published
    return collected_at


@dataclass(frozen=True)
class UnverifiedSchedulePolicy:
    """**발행 일정을 확인하지 못한** 데이터셋 — 수집 시각보다 이르게 찍지 않는다.

    ``PublicationPolicy`` 의 지연은 일봉 기준이다(세션 마감 + lag). 마감 직후에
    나오지 않는 데이터셋에 그 값을 그대로 쓰면 통째로 미래를 본다. 실측
    (2026-08-21, 최근 20일 131,200행): ``flows`` 의 observed_at 이 예외 없이
    ``valid_from + 7.00시간``(= 16:00 KST 정각, 표준편차 0.00)인데 실제 수집은
    22:40 에 돈다. 22:40 에 받은 수급이 "16:00 에 알 수 있었다" 로 적히고,
    Analyst 의 세션 as_of 가 정확히 16:00 이라 경계에서 그대로 읽힌다.
    불변식 3 이 막으려던 바로 그 구멍이다.

    **KRX 투자자별 순매수도 LS ``t1717`` 도 언제 값을 내는지 우리는 확인하지
    못했다.** 확인하지 못한 것을 상수로 박으면 그 상수가 다시 같은 거짓말이
    된다. 그래서 반박할 수 없는 하한 하나만 건다 — 우리가 받아 온 시각보다
    이르게 찍지 않는다. 늦게 찍는 쪽은 안전하다(못 보는 것뿐이다). 이르게
    찍는 쪽만 미래를 본다.

    발행 일정을 실제로 확인하면 이 감싸개를 벗기고 데이터셋별 lag 를 주는
    것이 맞다. 그때까지 이 클래스의 존재 자체가 "아직 모른다" 는 표시다.
    """

    inner: ObservedAtPolicy
    clock: Clock

    def for_session(self, day: date, *, extra_lag_days: int = 0) -> datetime:
        published = self.inner.for_session(day, extra_lag_days=extra_lag_days)
        return not_before_collection(published, self.clock.now())
