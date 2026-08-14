"""구간 성적. **NAV 는 여기서 계산하지 않는다** — 회계가 이미 계산했다.

이 파일이 하는 일은 ``nav_daily`` 에 적힌 누적지수를 읽어 낙폭·변동성으로
접는 것뿐이다. 여기서 NAV 를 다시 구하면 회계와 두 개가 되고, 어긋나면 어느
쪽이 맞는지 판정할 방법이 없다 (불변식: accounting.md §8).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

#: 연율화 계수. 국장 기준 연간 거래일.
TRADING_DAYS_PER_YEAR = 246


@dataclass(frozen=True)
class Performance:
    days: int
    total_return: float
    max_drawdown: float
    volatility: float
    #: 벤치마크 없이 계산한 위험조정수익. **IR 이 아니다** — 초과수익이 아니라
    #: 절대수익을 변동성으로 나눈 값이다. IR 은 총수익지수(TR)가 창고에 들어온
    #: 뒤에 계산한다 (backtest.md §5).
    return_over_vol: float
    turnover: float
    fill_rate: float
    action_reflection: float

    def summary(self) -> str:
        return (
            f"{self.days}거래일 · 수익 {self.total_return:+.2%} · "
            f"MDD {self.max_drawdown:.2%} · 변동성 {self.volatility:.2%} · "
            f"회전율 {self.turnover:.2f} · 체결률 {self.fill_rate:.2%} · "
            f"액션반영 {self.action_reflection:.2%}"
        )


def max_drawdown(index_values: Sequence[float]) -> float:
    """최대 낙폭(음수). **누적지수로 잰다** — NAV 원금액으로 재면 입금이 낙폭을
    지우고, 그러면 MDD 예산이 무의미해진다 (accounting.md §6).
    """
    peak = float("-inf")
    worst = 0.0
    for value in index_values:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1.0)
    return worst


def volatility(returns: Sequence[float]) -> float:
    """연율화 변동성. 표본이 2개 미만이면 0 이다 — 지어내지 않는다."""
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
    return math.sqrt(variance) * math.sqrt(TRADING_DAYS_PER_YEAR)


def summarize(
    *,
    index_values: Sequence[float],
    returns: Sequence[float],
    traded_value: float,
    average_nav: float,
    requested: int,
    filled: int,
    action_reflection: float,
) -> Performance:
    """구간 성적 한 묶음.

    회전율은 **체결 금액 / 평균 NAV** 다. 매수와 매도를 모두 세므로 왕복이면
    2 가 된다 — 진화 적합도의 페널티 항이 이 정의를 쓴다 (selector.md §3).
    """
    total = index_values[-1] / index_values[0] - 1.0 if len(index_values) >= 2 else 0.0
    return Performance(
        days=len(index_values),
        total_return=total,
        max_drawdown=max_drawdown(index_values),
        volatility=volatility(returns),
        return_over_vol=(
            total / volatility(returns) if volatility(returns) > 0 else 0.0
        ),
        turnover=traded_value / average_nav if average_nav > 0 else 0.0,
        fill_rate=filled / requested if requested else 0.0,
        action_reflection=action_reflection,
    )
