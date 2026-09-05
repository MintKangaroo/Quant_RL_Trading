"""학습 진단 지표 — `rl-training.md §10` 이 학습 탭에 찍으라고 한 것들.

여기 있는 것은 **판정 도구**다. 학습을 시키는 코드가 자기 성적을 스스로
계산하면, 고장 났을 때 성적도 같이 고장 나서 조용히 통과한다.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

Array = npt.NDArray[np.float64]


def explained_variance(predicted: Array, actual: Array) -> float:
    """`1 - Var(actual - predicted) / Var(actual)`.

    가치함수가 리턴의 분산 중 얼마를 설명하는지. 0 근처 고착이 곧 실패이고,
    선행 프로젝트는 여기서 벗어나지 못한 채 9차까지 갔다.

    **분모가 0 이면 판정 불가다.** 리턴이 상수면 설명할 분산 자체가 없다 —
    이때 1.0 을 돌려주면 "완벽하게 설명했다" 로 읽혀서, 보상이 통째로 죽은
    고장이 만점으로 찍힌다. NaN 을 돌려주고 호출부가 걸리게 둔다.
    """
    variance = float(np.var(actual))
    if variance <= 0.0:
        return float("nan")
    return 1.0 - float(np.var(actual - predicted)) / variance


def feature_attribution(gradients: Array, mask: npt.NDArray[np.bool_]) -> Array:
    """종목축 피처별 정책 그래디언트 기여도. ``gradients`` 는 (B, N, F).

    **유효 슬롯만 센다.** 패딩 칸의 입력은 0 이고 그래디언트도 0 이라, 같이
    평균 내면 유효 후보가 적은 배치일수록 모든 피처가 나란히 작아진다 —
    순위는 유지되지만 크기 비교가 무의미해진다.

    절댓값의 평균을 쓴다. 부호 있는 평균은 **정확히 반대로 읽힐 수 있다** —
    종목마다 부호가 갈리는 강한 피처가 0 으로 상쇄되어, 아무도 안 보는
    피처처럼 보인다.
    """
    weights = mask.astype(np.float64)[:, :, None]
    total = weights.sum()
    if total <= 0.0:
        raise ValueError("유효 슬롯이 하나도 없다. 마스크가 통째로 False 다")
    return (np.abs(gradients) * weights).sum(axis=(0, 1)) / total
