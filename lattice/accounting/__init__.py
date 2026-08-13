"""회계 — NAV·TWR·낙폭·벤치마크.

**NAV 산출은 이 패키지 한 곳에서만 한다** (accounting.md §8). Executor·
Auditor·dashboard·reporting 이 각자 계산하면 반드시 어긋나고, 어긋나면 어느
쪽이 맞는지 판정할 방법이 없다. 그런데 보상 함수는 그중 하나를 믿는다.

    book.py   체결·배당·입출금을 상태로 접는다 (순수 코드)
    nav.py    NAV 평가, TWR, 낙폭, 혼합 벤치마크
    rates.py  수수료·세금·배당세 — 전부 store.config 에서 온다
"""

from lattice.accounting.book import KRW, USD, Book, Position, Side, Trade
from lattice.accounting.nav import (
    BASE_INDEX,
    Valuation,
    blended_benchmark,
    compound,
    drawdown,
    twr_return,
    value,
)
from lattice.accounting.rates import Rates

__all__ = [
    "BASE_INDEX",
    "KRW",
    "USD",
    "Book",
    "Position",
    "Rates",
    "Side",
    "Trade",
    "Valuation",
    "blended_benchmark",
    "compound",
    "drawdown",
    "twr_return",
    "value",
]
