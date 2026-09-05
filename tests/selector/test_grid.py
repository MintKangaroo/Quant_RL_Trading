"""격자 탐색 계약 테스트.

여기서 증명하는 것은 넷이다.

1. **격자가 심플렉스를 빠짐없이 덮는다** — 빠지면 "탐색했다" 가 거짓이 된다
2. **이웃 관계가 좌표계에 안 흔들린다** — 흔들리면 평활이 엉뚱한 점을 섞는다
3. **잡음보다 작은 초과는 "고르지 못했다" 로 판정한다** — 이 모듈이 존재하는
   이유가 그것이다. GA 는 여기서 실패했다
4. **동일가중을 반드시 같이 잰다** — 비교 대상이 없으면 순위는 뜻이 없다
"""

from __future__ import annotations

import math

import pytest

from quant_rl_trading.selector import grid
from quant_rl_trading.selector.evolution import FitnessResult, Individual

ANALYSTS = ("fundamental", "risk", "event")


def _result(individual: Individual, fitness: float, folds=(0.1, 0.1)) -> FitnessResult:
    return FitnessResult(
        individual=individual,
        fitness=fitness,
        ir_median=fitness,
        turnover_median=0.0,
        l1_term=0.0,
        per_fold_ir=tuple(folds),
    )


class Test격자:
    def test_해상도_0_1_이면_66점이다(self) -> None:
        """2-심플렉스 · 해상도 0.1 — 메모리의 66점이 여기서 나온다."""
        points = grid.simplex_grid(ANALYSTS, steps=10)
        assert len(points) == 66
        assert grid.grid_size(ANALYSTS, steps=10) == 66

    def test_해상도_0_05_면_231점이다(self) -> None:
        assert grid.grid_size(ANALYSTS, steps=20) == 231
        assert len(grid.simplex_grid(ANALYSTS, steps=20)) == 231

    def test_모든_점의_가중치_합이_1이다(self) -> None:
        for individual in grid.simplex_grid(ANALYSTS, steps=10):
            assert sum(individual.genes) == pytest.approx(1.0)

    def test_원점은_격자에_없다(self) -> None:
        """전부 0 이면 합성 점수가 0건이다 — 나쁜 배합이 아니라 안 돈 백테스트다."""
        for individual in grid.simplex_grid(ANALYSTS, steps=10):
            assert sum(individual.genes) > 0.0

    def test_중복이_없다(self) -> None:
        points = grid.simplex_grid(ANALYSTS, steps=10)
        assert len({p.genes for p in points}) == len(points)


class Test이웃:
    @staticmethod
    def _points(steps: int = 10):
        return [
            grid.GridPoint(individual=ind, result=_result(ind, 0.0), steps=steps)
            for ind in grid.simplex_grid(ANALYSTS, steps=steps)
        ]

    def test_내부점의_이웃은_여섯이다(self) -> None:
        """3개 축에서 한 칸 옮기는 방법은 3×2 = 6 이다."""
        points = self._points()
        interior = next(p for p in points if all(c > 0 for c in p.counts))
        assert len(grid.neighbors(interior, points)) == 6

    def test_꼭짓점의_이웃은_둘이다(self) -> None:
        """모서리는 실제로 갈 곳이 적다 — 그게 옳다."""
        points = self._points()
        corner = next(p for p in points if sorted(p.counts) == [0, 0, 10])
        assert len(grid.neighbors(corner, points)) == 2

    def test_0이_섞인_점도_같은_좌표계를_쓴다(self) -> None:
        """회귀 — 해상도를 유전자에서 되짚으면 여기서 깨진다.

        ``(0, 0.5, 0.5)`` 은 steps=10 격자의 점이다. 가장 작은 양수 유전자로
        되짚으면 steps=2 가 나와 좌표가 ``(0,1,1)`` 이 되고, 같은 격자의 두
        점이 서로 다른 좌표계를 갖게 되어 이웃 판정이 조용히 어긋난다.
        """
        points = self._points()
        half = next(
            p for p in points
            if sorted(round(g, 6) for g in p.individual.genes) == [0.0, 0.5, 0.5]
        )
        assert sorted(half.counts) == [0, 5, 5]
        # 변 위의 점이므로 이웃은 넷이다(안쪽 둘 + 변을 따라 둘).
        assert len(grid.neighbors(half, points)) == 4

    def test_평활은_무한대를_섞지_않는다(self) -> None:
        """-inf 하나가 섞이면 그 주변 지형이 통째로 사라진다."""
        individuals = grid.simplex_grid(ANALYSTS, steps=10)
        points = [
            grid.GridPoint(
                individual=ind,
                result=_result(ind, float("-inf") if index % 2 else 1.0),
                steps=10,
            )
            for index, ind in enumerate(individuals)
        ]
        interior = next(p for p in points if all(c > 0 for c in p.counts))
        value = grid.smoothed_fitness(interior, points)
        assert math.isfinite(value)


class Test판정:
    @staticmethod
    def _search(fitness_of, folds_of=None):
        def evaluate(individual: Individual) -> FitnessResult:
            folds = folds_of(individual) if folds_of else (0.1, 0.1)
            return _result(individual, fitness_of(individual), folds)

        return grid.search(ANALYSTS, evaluate=evaluate, steps=10)

    def test_잡음보다_작은_초과는_고르지_못한_것이다(self) -> None:
        """**이 모듈이 존재하는 이유.** 폴드가 크게 흩어지는데 점수 차이가
        작으면, 1위는 1위가 아니라 그날 운이 좋았던 점이다.
        """
        report = self._search(
            # 배합에 거의 무관한 적합도 — 차이가 잡음보다 작다.
            lambda ind: 1.0 + 0.001 * ind.normalized()["fundamental"],
            # 폴드가 크게 흩어진다 → SE 가 크다.
            lambda ind: (0.5, -0.5, 0.8, -0.9),
        )
        assert not report.resolvable
        assert "격자는 아무것도 고르지 못했다" in report.verdict
        assert "동일가중을 쓴다" in report.verdict

    def test_잡음을_넘고_평활과_일치하면_봉우리로_본다(self) -> None:
        report = self._search(
            # fundamental 쪽으로 갈수록 단조 증가 — 진짜 봉우리다.
            lambda ind: 10.0 * ind.normalized()["fundamental"],
            lambda ind: (0.10, 0.11, 0.09, 0.10),
        )
        assert report.resolvable
        assert "봉우리로 볼 만하다" in report.verdict
        # 꼭짓점(fundamental 1.0)이 최고여야 한다.
        assert report.best.individual.normalized()["fundamental"] == pytest.approx(1.0)

    def test_단발_잡음은_평활이_잡아낸다(self) -> None:
        """한 점만 튀면 최고점과 평활 최고점이 갈린다 — 그걸 말해야 한다."""
        spike = grid.simplex_grid(ANALYSTS, steps=10)[7]

        def fitness(individual: Individual) -> float:
            return 100.0 if individual.genes == spike.genes else 1.0

        report = self._search(fitness, lambda ind: (0.010, 0.011, 0.009))
        assert report.best.individual.genes == spike.genes
        assert report.best_smoothed.counts != report.best.counts
        assert "단발 잡음" in report.verdict

    def test_동일가중을_반드시_같이_잰다(self) -> None:
        """해상도 10 은 3 으로 안 나눠떨어져 동일가중이 격자 위에 없다.
        그래도 비교 대상은 있어야 한다 — 가장 가까운 점으로 때우면 "이겼다"
        가 반올림 오차가 된다.
        """
        seen: list[tuple[float, ...]] = []

        def evaluate(individual: Individual) -> FitnessResult:
            seen.append(individual.genes)
            return _result(individual, 1.0)

        report = grid.search(ANALYSTS, evaluate=evaluate, steps=10)
        weights = report.uniform.individual.normalized()
        assert all(v == pytest.approx(1 / 3) for v in weights.values())
        # 격자 66점 + 동일가중 1점.
        assert len(seen) == 67

    def test_폴드가_하나면_잡음을_못_쟀다고_말한다(self) -> None:
        """못 쟀을 때 1위를 믿게 두지 않는다."""
        report = self._search(lambda ind: 1.0, lambda ind: (0.1,))
        assert math.isnan(report.noise)
        assert not report.resolvable
        assert "폴드가 둘 미만이라" in report.verdict

    def test_폴드가_완전히_일치하면_못_쟀다고_하지_않는다(self) -> None:
        """잡음 0 은 측정 결과다. "못 쟀다" 와 같은 말로 뭉뚱그리면,
        폴드가 잘 맞은 격자와 폴드가 없는 격자를 구분할 수 없다.
        """
        report = self._search(
            lambda ind: 10.0 * ind.normalized()["fundamental"],
            lambda ind: (0.1, 0.1),
        )
        assert report.noise == 0.0
        assert report.resolvable
        assert "잡음을 못 쟀다" not in report.verdict
