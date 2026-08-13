"""룰 베이스라인 계약 테스트.

**M4 에서 RL 의 경쟁자가 될 것이다.** 여기가 허술하면 RL 이 실제로 나은지
모르는 채로 승격시키게 된다 — 선행 프로젝트가 9차까지 간 이유가 이런 종류의
느슨한 기준이다.
"""

from __future__ import annotations

import pytest

from lattice.allocator.baseline import AllocatorParams, Baseline, allocate

#: 실제 설정값. 상한이 잘 걸리는지 보는 테스트가 쓴다.
PARAMS = AllocatorParams(
    baseline=Baseline.SCORE, max_position_weight=0.15, cash_buffer=0.05
)

#: 상한을 느슨하게 푼 것. **배분 모양**을 보는 테스트가 쓴다 — 상한이 걸리면
#: 전 종목이 같은 값이 되어 모양 자체가 사라진다.
LOOSE = AllocatorParams(
    baseline=Baseline.SCORE, max_position_weight=0.9, cash_buffer=0.05
)


def test_동일가중은_똑같이_나눈다() -> None:
    params = AllocatorParams(
        baseline=Baseline.EQUAL, max_position_weight=0.15, cash_buffer=0.05
    )
    # 8종목이면 종목당 11.875% 라 상한(15%)에 안 닿는다.
    scores = {chr(65 + index): 0.1 * (index + 1) for index in range(8)}

    weights = allocate(scores=scores, params=params)

    assert len(set(round(value, 9) for value in weights.values())) == 1
    assert sum(weights.values()) == pytest.approx(0.95)


def test_스코어_비례는_점수만큼_준다() -> None:
    weights = allocate(scores={"A": 0.6, "B": 0.3, "C": 0.1}, params=LOOSE)

    assert weights["A"] > weights["B"] > weights["C"]
    assert sum(weights.values()) == pytest.approx(0.95)


def test_후보가_적은_날은_현금이_남는다() -> None:
    """**상한의 정직한 결과다.** 3종목으로는 분산될 수 없다.

    종목당 15% 상한이면 3종목은 45% 까지만 담긴다. 나머지 55% 가 현금인 것은
    계산 사고가 아니라 리스크 결정이다 — 억지로 채우면 한 종목이 30% 가 된다.
    """
    weights = allocate(scores={"A": 0.9, "B": 0.5, "C": 0.1}, params=PARAMS)

    assert sum(weights.values()) == pytest.approx(0.45)


def test_음수_점수는_사지_않는다() -> None:
    """음수는 '덜 좋은 매수 후보' 가 아니라 팔 이유다.

    그대로 정규화하면 부호가 뒤집혀 가장 나쁜 종목에 가장 큰 비중이 갈 수 있다.
    """
    weights = allocate(scores={"A": 0.5, "B": -0.9}, params=LOOSE)

    assert "B" not in weights
    assert weights["A"] == pytest.approx(0.9)   # 상한까지만


def test_전부_음수면_아무것도_사지_않는다() -> None:
    assert allocate(scores={"A": -0.1, "B": -0.5}, params=PARAMS) == {}


def test_종목_상한을_넘지_않는다() -> None:
    weights = allocate(scores={"A": 10.0, "B": 1.0, "C": 1.0}, params=PARAMS)

    assert max(weights.values()) <= 0.15 + 1e-9


def test_상한에서_깎인_몫은_나머지에_다시_나눈다() -> None:
    """깎고 끝내면 아무도 의도하지 않은 현금이 생긴다.

    소수 종목만 남는 날에는 절반이 현금이 되는 일도 생긴다 — 그건 배분
    결정이 아니라 계산 사고다.
    """
    scores = {"A": 10.0, "B": 1.0, "C": 1.0, "D": 1.0, "E": 1.0, "F": 1.0, "G": 1.0}

    weights = allocate(scores=scores, params=PARAMS)

    assert sum(weights.values()) == pytest.approx(0.95)
    assert max(weights.values()) <= 0.15 + 1e-9


def test_전부_상한에_닿으면_나머지는_현금이다() -> None:
    """상한을 넘겨 배분하는 것보다 현금이 낫다."""
    weights = allocate(scores={"A": 1.0, "B": 1.0, "C": 1.0}, params=PARAMS)

    assert sum(weights.values()) == pytest.approx(0.45)   # 3종목 × 15%
    assert all(value <= 0.15 + 1e-9 for value in weights.values())


def test_변동성_역가중은_변동성이_없으면_거부한다() -> None:
    """모르는 분모를 지어내지 않는다. 지어낸 분모는 대개 그 종목에 유리하게 틀린다."""
    params = AllocatorParams(
        baseline=Baseline.SCORE_INVERSE_VOL, max_position_weight=0.5, cash_buffer=0.05
    )

    with pytest.raises(ValueError, match="지어내지 않는다"):
        allocate(scores={"A": 1.0}, params=params)


def test_변동성_역가중은_안정된_종목에_더_준다() -> None:
    params = AllocatorParams(
        baseline=Baseline.SCORE_INVERSE_VOL, max_position_weight=0.9, cash_buffer=0.05
    )

    weights = allocate(
        scores={"A": 1.0, "B": 1.0},
        params=params,
        volatility={"A": 0.01, "B": 0.04},
    )

    assert weights["A"] > weights["B"]
    assert sum(weights.values()) == pytest.approx(0.95)


def test_변동성을_모르는_종목은_배분에서_빠진다() -> None:
    params = AllocatorParams(
        baseline=Baseline.SCORE_INVERSE_VOL, max_position_weight=0.9, cash_buffer=0.05
    )

    weights = allocate(
        scores={"A": 1.0, "B": 1.0}, params=params, volatility={"A": 0.02}
    )

    assert "B" not in weights
    assert weights["A"] == pytest.approx(0.9)   # 종목 상한 0.9 에서 멈춘다


def test_후보가_없으면_전부_현금이다() -> None:
    assert allocate(scores={}, params=PARAMS) == {}
