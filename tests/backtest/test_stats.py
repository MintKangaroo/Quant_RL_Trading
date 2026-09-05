"""구간 성적 통계.

여기서 지키는 것은 하나다: **``return_over_vol`` 은 구간 길이에 안 딸린다.**
이 값을 유일하게 쓰는 곳이 진화 적합도(``selector/evolution.py``)이고, 거기
붙은 L1·회전율 페널티 계수는 "적합도의 몇 %" 로 정해진다. 적합도의 스케일이
폴드 길이에 따라 변하면 그 계수가 무엇을 뜻하는지 말할 수 없게 된다.
"""

from __future__ import annotations

import math

import pytest

from quant_rl_trading.backtest import stats


def _index(returns: list[float]) -> list[float]:
    values = [1.0]
    for r in returns:
        values.append(values[-1] * (1.0 + r))
    return values


def test_표본이_2개_미만이면_0이다() -> None:
    assert stats.annualized_return_over_vol([]) == 0.0
    assert stats.annualized_return_over_vol([0.01]) == 0.0


def test_변동성이_0이면_0이다() -> None:
    """수익이 매일 똑같으면 위험조정수익이 무한대가 아니라 0 이다 — 지어내지
    않는다(``volatility`` 가 같은 규약을 쓴다)."""
    assert stats.annualized_return_over_vol([0.01, 0.01, 0.01]) == 0.0


def test_교과서_정의와_같다() -> None:
    returns = [0.01, -0.004, 0.007, 0.002, -0.001]
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    expected = mean * stats.TRADING_DAYS_PER_YEAR / (
        math.sqrt(variance) * math.sqrt(stats.TRADING_DAYS_PER_YEAR)
    )
    assert stats.annualized_return_over_vol(returns) == pytest.approx(expected)


def test_구간_길이가_달라도_값이_안_변한다() -> None:
    """**이 테스트가 이 파일의 이유다.**

    2026-08-15 이전에는 분자가 구간 누적수익이고 분모만 연율화라, 같은 일간
    수익 패턴을 15일 창에서 재느냐 60일 창에서 재느냐로 값이 4배 달라졌다.
    진화는 폴드마다 이 값을 비교해 개체를 고르므로, 그 차이가 가중치가 아니라
    창 길이에서 나오면 진화가 고르는 것은 노이즈다.
    """
    pattern = [0.006, -0.003, 0.004, -0.002]
    short = stats.annualized_return_over_vol(pattern * 4)   # 16일
    long = stats.annualized_return_over_vol(pattern * 16)   # 64일
    # 완전히 같지는 않다 — 표본 표준편차의 (n-1) 보정이 남는다. 그건 **추정량의
    # 소표본 편의**이지 구간 길이에 딸린 스케일이 아니다. 2.5% 안이다.
    assert short == pytest.approx(long, rel=0.03)


def test_옛_정의는_구간_길이에_딸려_움직였다() -> None:
    """회귀 방지 — 옛 계산을 여기 재현해 두고 '이건 다르다' 를 못 박는다."""
    pattern = [0.006, -0.003, 0.004, -0.002]

    def old(returns: list[float]) -> float:
        total = _index(returns)[-1] / _index(returns)[0] - 1.0
        return total / stats.volatility(returns)

    # 창을 4배로 늘리면 옛 값도 약 4배가 됐다 — 전략이 아니라 창이 바뀐 것뿐인데.
    assert old(pattern * 16) / old(pattern * 4) > 3.5
    # 새 정의는 같은 입력에서 사실상 그대로다.
    new_ratio = stats.annualized_return_over_vol(pattern * 16) / (
        stats.annualized_return_over_vol(pattern * 4)
    )
    assert 0.97 < new_ratio < 1.03


def test_손실_구간은_음수다() -> None:
    assert stats.annualized_return_over_vol([-0.01, -0.002, -0.006, 0.001]) < 0


def test_summarize가_같은_값을_쓴다() -> None:
    """``summarize`` 가 따로 계산하면 두 숫자가 갈라진다."""
    returns = [0.01, -0.004, 0.007, 0.002]
    performance = stats.summarize(
        index_values=_index(returns),
        returns=returns,
        traded_value=1_000.0,
        average_nav=10_000.0,
        requested=10,
        filled=8,
        action_reflection=0.8,
    )
    assert performance.return_over_vol == pytest.approx(
        stats.annualized_return_over_vol(returns)
    )
    assert performance.turnover == pytest.approx(0.1)
    assert performance.fill_rate == pytest.approx(0.8)
