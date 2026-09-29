"""재조정 주기 — 오늘 다시 고르나, 들고 있나 (selector.md §5 7번, 시행 AO).

랭커는 5세션 수익을 맞히는데 매일 다시 고르면 점수의 매일 흔들림이 그대로 매매가 된다. 10세션마다 고르면
원천 넷 모두 올랐다(연 +6.0% → +12.2%, 회전 34 → 21).

**순수 함수다.** anchor 부터 그 시장의 거래일을 세어 `k mod every == 0` 이면 재조정일. 놓친 재조정일을 기억하는
상태를 두지 않는다 — 상태가 생기면 창고와 어긋나고, 백테스트와 라이브가 다른 날을 고른다(불변식 5).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from quant_rl_trading.collectors.market_hours import Market, trading_days
from quant_rl_trading.selector.candidates import market_config
from quant_rl_trading.store import ConfigNotFound, Store


@dataclass(frozen=True)
class Cadence:
    """오늘이 재조정일인가와 그 이유(로그·화면용)."""

    rebalance: bool
    every: int
    index: int | None = None

    def describe(self) -> str:
        if self.every <= 1:
            return "매일 재조정"
        if self.index is None:
            return f"{self.every}세션 주기 — anchor 전이라 재조정"
        step = self.index % self.every
        if self.rebalance:
            return f"{self.every}세션 주기 재조정일 (anchor 뒤 {self.index}번째 세션)"
        return f"{self.every}세션 주기 보유일 ({step}/{self.every}, 다음 재조정까지 {self.every - step}세션)"


def decide(anchor: date | None, every: int, day: date, market: Market) -> Cadence:
    """anchor 에서 day 까지 그 시장 거래일을 센다. anchor 가 없거나 day 가 anchor 전이면 재조정한다."""
    if every <= 1 or anchor is None or day < anchor:
        return Cadence(rebalance=True, every=max(every, 1))
    sessions = [d for d in trading_days(market, anchor, day) if d <= day]
    # day 가 거래일이 아니면(휴장에 불린 경우) 직전 거래일의 번호를 쓴다 — 주문은 어차피 집행 게이트가 막는다.
    index = len(sessions) - 1 if sessions and sessions[-1] == day else len(sessions)
    return Cadence(rebalance=index % every == 0, every=every, index=index)


def settings(store: Store, *, as_of: datetime, market: str) -> tuple[int, date | None]:
    """(주기, anchor). 키가 없으면 (1, None) = 매일(옛 동작)."""
    try:
        every = int(market_config(store, "selector.rebalance_every", as_of=as_of, market=market))
    except ConfigNotFound:
        every = 1
    anchor: date | None = None
    if every > 1:
        try:
            raw = str(store.config("selector.rebalance_anchor", as_of=as_of)).strip()
        except ConfigNotFound:
            raw = ""
        anchor = date.fromisoformat(raw) if raw else None
    return every, anchor


def for_session(store: Store, *, as_of: datetime, market: str) -> Cadence:
    """설정에서 주기·anchor 를 읽는다. 키가 없으면 매일(옛 동작)."""
    every, anchor = settings(store, as_of=as_of, market=market)
    return decide(anchor, every, as_of.date(), Market(market))

#: 다음 재조정일을 찾는 창(달력일). 주기 10세션에 연휴를 넉넉히 넘긴다.
NEXT_SEARCH_DAYS = 120


def next_rebalance(store: Store, *, as_of: datetime, market: str) -> date | None:
    """as_of **다음** 첫 재조정 세션의 날짜. 매일 재조정이면 다음 거래일, 창 안에 없으면 None.

    판정은 ``for_session`` 과 같은 ``decide`` 를 날마다 부른 것이다 — 화면이 주기를 따로 세면 세션과 어긋난다.
    설정은 **as_of 시점 값**으로 한 번만 읽는다(미래 날짜로 config 를 물으면 되감은 화면이 뒤에 바뀐 설정을 본다).
    """
    from datetime import timedelta

    every, anchor = settings(store, as_of=as_of, market=market)
    start = as_of.date() + timedelta(days=1)
    for day in trading_days(Market(market), start, start + timedelta(days=NEXT_SEARCH_DAYS)):
        if decide(anchor, every, day, Market(market)).rebalance:
            return day
    return None
