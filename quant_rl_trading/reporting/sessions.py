"""이 메일이 다루는 세션은 시장마다 다르다.

미장 마감 뒤에 보내는 한 통이 국장과 미장을 같이 담는다. 그런데 그 둘의
"직전 세션" 은 **같은 날이 아니고, 같은 날일 이유도 없다.**

    2026-08-17(월) — 국장 휴장 · 미장 개장
    → 국장의 직전 세션은 08-14(금), 미장의 직전 세션은 08-17(월)

그래서 날짜를 하나 정해 양쪽에 쓰면 안 된다. 시장마다 **그 시장의 거래일
달력**으로 따로 구한다 (``market_hours.trading_days``).

## 기대 세션과 관측 세션은 다른 것이다

``expected`` 는 달력이 말하는 것 — as_of 시점에 이미 마감되고 공표까지 끝난
마지막 세션이다. ``observed`` 는 창고에 실제로 들어와 있는 마지막 세션이다.

둘이 어긋나는 날이 있다. 2026-08-15 기준으로 국장 지수는 08-13 까지만
들어와 있었고 08-14 가 없었다. 그런 날 화면이 **빈칸을 그리면 거짓말이다** —
"08-14 는 거래일인데 창고에 없다" 가 사실이고, 메일은 그 사실을 적어야 한다.
그래서 ``note`` 를 들고 다닌다.

공표 시각은 ``publication_policy`` 가 안다. 미장 마감은 서머타임에 따라
05:00/06:00 KST 로 움직이는데, 그 계산을 여기서 손으로 하지 않는다 —
지역시각 16:00 ET 를 tz 데이터가 UTC 로 옮긴다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from quant_rl_trading.collectors.market_hours import (
    Market,
    local_time,
    trading_days,
)
from quant_rl_trading.collectors.publication import NotYetPublished, publication_policy
from quant_rl_trading.replay.clock import Clock, ReplayClock
from quant_rl_trading.store import Store

#: 기대 세션을 찾을 때 되돌아볼 달력일. 국장이 설·추석에 주말과 붙어 9일까지
#: 쉰다 (``benchmark.max_staleness_days`` 주석과 같은 사실). 넉넉히 둔다.
SEARCH_DAYS = 20


@dataclass(frozen=True)
class SessionRef:
    """한 시장 · 한 데이터원의 세션 상태.

    ``source`` 를 따로 드는 이유: 같은 시장이라도 지수와 시세가 서로 다른
    날까지 들어와 있다. 2026-08-15 실측으로 국장은 시세가 08-14, 지수가
    08-13 이었고 미장은 그 반대였다. 하나로 뭉치면 둘 중 하나가 거짓이 된다.
    """

    market: str
    source: str
    expected: date | None
    observed: date | None
    note: str | None

    @property
    def ok(self) -> bool:
        return self.expected is not None and self.observed == self.expected

    def as_dict(self) -> dict[str, str | bool | None]:
        return {
            "market": self.market,
            "source": self.source,
            "expected": self.expected.isoformat() if self.expected else None,
            "observed": self.observed.isoformat() if self.observed else None,
            "note": self.note,
            "ok": self.ok,
        }


def expected_session(
    store: Store, market: Market, *, as_of: datetime, clock: Clock | None = None
) -> date | None:
    """``as_of`` 시점에 **이미 공표가 끝난** 마지막 거래일.

    마감만으로 세지 않는다. 미장 16:00 ET 마감 직후 20분은 아직 종가를
    받을 수 없는 구간이고, 그때 오늘 세션을 기대 세션이라 부르면 메일이
    매일 "미장 데이터 없음" 을 외치게 된다.
    """
    # 기본 시계는 as_of 에 멈춘 시계다. 정책이 "아직 미래다" 를 판정할 때
    # 벽시계를 보면 같은 as_of 로 두 번 만든 리포트가 달라진다 (불변식 2·5).
    policy = publication_policy(store, market, clock=clock or ReplayClock(as_of))
    here = local_time(market, as_of).date()
    days = trading_days(market, here - timedelta(days=SEARCH_DAYS), here)
    for day in reversed(days):
        try:
            published = policy.for_session(day)
        except NotYetPublished:
            continue
        if published <= as_of:
            return day
    return None


def describe(
    market: Market, source: str, *, expected: date | None, observed: date | None
) -> SessionRef:
    """기대와 관측을 견줘 **어긋난 이유를 문장으로** 만든다.

    빈칸을 그리지 않기 위한 함수다. 이 문장이 없으면 화면은 "지수 없음" 과
    "오늘은 휴장" 과 "수집이 안 돌았다" 를 전부 같은 공백으로 보여준다.
    """
    label = f"{market} {source}"
    if expected is None:
        note = f"{label}: 최근 {SEARCH_DAYS}일 안에 공표가 끝난 세션이 없다 (휴장 구간)"
    elif observed is None:
        note = f"{label}: {expected.isoformat()} 세션이 창고에 없다 (수집 미실행 또는 실패)"
    elif observed < expected:
        # **달력일이 아니라 세션 수로 센다.** 금요일 것이 없는 채로 월요일에
        # 보면 달력으로는 3일이지만 빠진 세션은 하나다. 달력일로 적으면 주말이
        # 장애처럼 보이고, 진짜 3세션짜리 구멍과 구별되지 않는다.
        missing = len(trading_days(market, observed, expected)) - 1
        note = (
            f"{label}: 창고가 {observed.isoformat()} 까지다. "
            f"{expected.isoformat()} 까지 {missing}개 세션이 안 들어왔다"
        )
    elif observed > expected:
        # 공표 전 데이터가 창고에 있다는 뜻이다. 조용히 쓰면 미래를 보는 것이다.
        note = (
            f"{label}: 창고에 {observed.isoformat()} 가 있는데 공표된 마지막 세션은 "
            f"{expected.isoformat()} 다 — 관측시각을 확인할 것"
        )
    else:
        note = None
    return SessionRef(
        market=str(market), source=source, expected=expected, observed=observed, note=note
    )


def resolve_sessions(
    store: Store, *, as_of: datetime, clock: Clock | None = None
) -> dict[str, date | None]:
    """시장별 기대 세션. ``{"KR": date, "US": date}``.

    브리핑 전체가 이 한 쌍 위에 선다.
    """
    return {
        str(market): expected_session(store, market, as_of=as_of, clock=clock)
        for market in (Market.KR, Market.US)
    }
