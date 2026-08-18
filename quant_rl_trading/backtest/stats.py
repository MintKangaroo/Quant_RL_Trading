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
    #:
    #: **분자·분모가 둘 다 연율화되어 있다.** 2026-08-15 이전에는 분자가 구간
    #: 누적수익(예: 15거래일치)이고 분모만 연율화 변동성이라 단위가 어긋났고,
    #: 그래서 값의 크기가 **구간 길이에 딸려 움직였다** — 같은 전략을 15일
    #: 창에서 재느냐 60일 창에서 재느냐로 숫자가 4배 달라졌다. 이 값을 유일하게
    #: 쓰는 곳이 진화 적합도(``selector/evolution.py``)이고, 거기 붙은 페널티
    #: 계수(L1·회전율)는 "적합도의 몇 %" 로 정해지므로, 적합도의 스케일이
    #: 구간 길이에 따라 변하면 계수가 무엇을 뜻하는지 말할 수 없게 된다.
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


def annualized_return_over_vol(returns: Sequence[float]) -> float:
    """연율화 수익 / 연율화 변동성. 표본이 2개 미만이거나 변동성 0이면 0.

    분자는 **일간 수익의 평균 × 연간 거래일**이다. 구간 누적수익을 복리로
    연율화(``(1+r)^(246/n)-1``)하지 않는 이유는, 짧은 창에서 그 값이 폭발하기
    때문이다 — 15거래일 +5% 는 연 +125% 가 되어 폴드 간 분산이 실제 위험이
    아니라 창 길이의 산물이 된다. 분모의 변동성도 일간 표준편차에서 연율화하니,
    분자를 같은 일간 수익 열에서 만들어야 **같은 표본에서 나온 두 통계의 비**가
    된다.

    **주의 — 짧은 창에서 이 값은 추정치로서 매우 시끄럽다.** 일간 수익 n개로
    잰 연율화 비율의 표준오차는 대략 ``sqrt(246/n)`` 이다. n=15 면 약 4.0,
    n=60 이면 약 2.0 이다. 진화가 이 값의 폴드 간 차이로 개체를 고르려면 그
    차이가 이 잡음보다 커야 한다.
    """
    if len(returns) < 2:
        return 0.0
    annual_vol = volatility(returns)
    if annual_vol <= 0:
        return 0.0
    mean_daily = sum(returns) / len(returns)
    return mean_daily * TRADING_DAYS_PER_YEAR / annual_vol


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
        return_over_vol=annualized_return_over_vol(returns),
        turnover=traded_value / average_nav if average_nav > 0 else 0.0,
        fill_rate=filled / requested if requested else 0.0,
        action_reflection=action_reflection,
    )
