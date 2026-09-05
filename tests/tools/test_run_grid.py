"""격자 실행기의 폴드 선택 계약.

폴드가 겹치면 **표본 수를 잘못 세게 된다.** 60거래일짜리 폴드 둘이 하루
차이면 그 둘의 중앙값은 표본 2개가 아니라 1개고, 그러면 ``grid.fold_noise``
가 잡음을 실제보다 작게 추정해 "격자가 골랐다" 는 거짓 판정이 나온다.

실제로 학습 폴드에만 등간격을 걸고 홀드아웃에 안 걸어 2026-01-02 ·
2026-01-05 가 나왔다. 두 곳이 같은 규칙을 쓴다는 것을 여기서 지킨다.
"""

from __future__ import annotations

from datetime import date, timedelta
from itertools import pairwise

from tools.run_grid import _spread

#: 연속된 거래일 시작점 200개. ``_available_fold_starts`` 가 돌려주는 모양이다.
STARTS = [date(2025, 1, 2) + timedelta(days=offset) for offset in range(200)]


def test_안_겹치는_폴드를_먼저_고른다() -> None:
    folds, note = _spread(STARTS, 2, fold_days=60)
    assert len(folds) == 2
    assert (folds[1] - folds[0]).days >= 60
    assert note is None


def test_셋도_안_겹치게_고른다() -> None:
    folds, note = _spread(STARTS, 3, fold_days=60)
    assert len(folds) == 3
    gaps = [(b - a).days for a, b in pairwise(folds)]
    assert all(gap >= 60 for gap in gaps)
    assert note is None


def test_구간이_좁으면_겹친다고_말한다() -> None:
    """조용히 겹치는 것이 이 함수가 막으려는 유일한 것이다."""
    narrow = STARTS[:40]
    folds, note = _spread(narrow, 2, fold_days=60)
    assert len(folds) == 2
    assert note is not None
    assert "겹친다" in note
    # 얼마나 겹치는지까지 말해야 표본을 다시 셀 수 있다.
    assert "거래일 겹친다" in note


def test_앞에서_자르지_않는다() -> None:
    """회귀 — starts[:count] 면 하루 간격 폴드가 나온다."""
    folds, _ = _spread(STARTS, 2, fold_days=60)
    assert folds != STARTS[:2]
    assert (folds[1] - folds[0]).days > 1


def test_하나만_필요하면_첫_폴드다() -> None:
    folds, note = _spread(STARTS, 1, fold_days=60)
    assert folds == [STARTS[0]]
    assert note is None


def test_빈_입력은_빈_결과다() -> None:
    assert _spread([], 2, fold_days=60) == ([], None)
    assert _spread(STARTS, 0, fold_days=60) == ([], None)
