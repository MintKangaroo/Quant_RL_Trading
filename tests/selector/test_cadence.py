"""재조정 주기 — anchor 부터 그 시장 거래일을 센다 (selector.md §5 7번)."""

from __future__ import annotations

from datetime import date

from quant_rl_trading.collectors.market_hours import Market, trading_days
from quant_rl_trading.selector.cadence import decide

ANCHOR = date(2026, 9, 28)


def test_anchor_는_재조정일이고_열_세션마다_돌아온다() -> None:
    days = trading_days(Market.KR, ANCHOR, date(2026, 11, 30))
    picks = [d for d in days if decide(ANCHOR, 10, d, Market.KR).rebalance]
    assert picks[0] == ANCHOR
    assert picks == days[::10]


def test_anchor_전과_매일_주기는_언제나_재조정() -> None:
    assert decide(ANCHOR, 10, date(2026, 9, 22), Market.KR).rebalance
    assert decide(None, 10, date(2026, 10, 5), Market.KR).rebalance
    assert all(decide(ANCHOR, 1, d, Market.KR).rebalance for d in trading_days(Market.KR, ANCHOR, date(2026, 10, 30)))


def test_휴장일은_거래일로_세지_않는다() -> None:
    # 10/3 개천절(토)·10/9 한글날(금) — 거래일만 센다.
    days = trading_days(Market.KR, ANCHOR, date(2026, 10, 31))
    assert date(2026, 10, 9) not in days
    assert decide(ANCHOR, 10, days[10], Market.KR).index == 10
