"""가중치 격자 탐색 — GA 대신 2-심플렉스를 통째로 그린다. 순수 코드.

## 왜 GA 가 아닌가

진화 적합도는 ``Performance.return_over_vol`` 이고, 일간 수익 n개로 잰 이
값의 표준오차는 대략 ``sqrt(246/n)`` 이다. 2026-08-15 실측:

    폴드    거래일   SE(1폴드)   SE(2폴드 중앙값)   240회 평가 비용
    3주      15       4.05        2.86              13시간
    3개월    60       2.02        1.43              54시간
    6개월   123       1.41        1.00             110시간
    1년     246       1.00        0.71             220시간

L1·회전율 페널티 항의 크기는 0.2~2.3 규모다. **측정 잡음이 최적화 대상보다
크다.** 15일 폴드에서는 계수를 어떻게 잡아도 GA 가 가중치를 고르지 못한다.
통계적으로 의미 있는 폴드 길이는 이 기계에서 계산상 불가능하다.

거기에 GA 의 안정성 검사(``evolution.stability_report``)는 작은 개체군의
유전적 드리프트를 봉우리로 오인한다 — 순수 난수 적합도로 pop16×gen15 를
20번 돌렸더니 **18번이 "안정" 도장을 받았다**(``NOISE_FLOOR_DISTANCE``).
즉 그 검사는 자기가 막으라고 만들어진 바로 그 경우에서 실패한다.

**활성 Analyst 가 3개면 탐색공간은 2-심플렉스다.** 격자가 더 싸고 완전하다:

    해상도 0.1  →  66점        해상도 0.05  →  231점

## 이 모듈이 약속하는 것과 약속하지 않는 것

**약속하지 않는다: "최적 가중치".** 위 표대로 잡음이 신호보다 크면 1위 점은
1위가 아니라 그날 운이 좋았던 점이다. 그래서 ``GridReport`` 는 최고점을
내놓을 때 **반드시 그 옆에 잡음 규모를 같이 내놓는다.** 둘을 떼어 놓으면
읽는 사람이 순위를 믿게 되고, 그건 GA 가 하던 실수와 같은 실수다.

**약속한다: 지형.** 격자는 전 구간을 같은 비용으로 재므로 "봉우리냐 평지냐"
에 답할 수 있다. 동일가중 주변이 평지면 가중치를 건드릴 이유가 없다는 뜻이고,
그건 드리프트로 오염된 안정성 검사 없이 곧바로 읽힌다.

**약속한다: 이웃 평활.** 격자는 이웃이 정의되므로 인접 점끼리 평균 내
잡음을 줄일 수 있다. 무작위 표본인 GA 개체군에는 없는 성질이다.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass

from quant_rl_trading.selector.evolution import FitnessResult, Individual, uniform_individual

#: 기본 해상도. 3 Analyst 기준 66점 — 하루 비용을 보고 올리거나 내린다.
DEFAULT_STEPS = 10


def _same_weights(left: dict[str, float], right: dict[str, float], *, tol: float = 1e-9) -> bool:
    """정규화 가중치가 같은가. **dict 를 == 로 비교하지 않는다** —
    격자점은 나눗셈에서 왔고 동일가중은 곱셈에서 와서, 같은 배합이어도
    마지막 자리가 다를 수 있다. 그 한 자리 때문에 "동일가중이 격자에 없다"
    로 갈리면 비교 대상이 통째로 달라진다.
    """
    if left.keys() != right.keys():
        return False
    return all(abs(left[k] - right[k]) <= tol for k in left)


def compositions(total: int, parts: int) -> Iterator[tuple[int, ...]]:
    """``total`` 을 ``parts`` 개의 음이 아닌 정수로 쪼개는 모든 방법.

    격자점의 개수는 ``C(total + parts - 1, parts - 1)`` 이다. parts=3,
    total=10 이면 66. 정수로 세는 이유는 **부동소수로 격자를 만들면 합이
    정확히 1 이 안 되는 점이 생기고**, 그 점만 정규화에서 미세하게 다른
    가중치가 되어 이웃 관계가 깨지기 때문이다.
    """
    if parts <= 0:
        raise ValueError("parts 는 1 이상이어야 한다")
    if parts == 1:
        yield (total,)
        return
    for head in range(total + 1):
        for rest in compositions(total - head, parts - 1):
            yield (head, *rest)


def simplex_grid(analysts: Sequence[str], *, steps: int = DEFAULT_STEPS) -> list[Individual]:
    """심플렉스 위의 모든 격자점. **원점(전부 0)은 뺀다.**

    가중치가 전부 0 이면 합성 점수가 0건이 되어 "나쁜 배합" 이 아니라
    "돌지 않은 백테스트" 가 된다. 그 둘을 같은 표에 올리면 안 된다.
    """
    if steps <= 0:
        raise ValueError("steps 는 1 이상이어야 한다")
    names = tuple(analysts)
    if not names:
        raise ValueError("Analyst 가 없다")
    out = []
    for counts in compositions(steps, len(names)):
        if sum(counts) == 0:
            continue
        out.append(Individual(analysts=names, genes=tuple(c / steps for c in counts)))
    return out


def grid_size(analysts: Sequence[str], *, steps: int = DEFAULT_STEPS) -> int:
    """격자점 개수. **돌리기 전에 비용을 알려면 필요하다.**"""
    n, k = steps, len(analysts)
    return math.comb(n + k - 1, k - 1)


@dataclass(frozen=True)
class GridPoint:
    """격자점 하나와 그 성적.

    ``steps`` 를 **들고 다닌다.** 유전자에서 되짚으려 하면 안 된다 —
    ``(0, 0.5, 0.5)`` 은 steps=10 격자의 점인데 가장 작은 양수 유전자로
    되짚으면 steps=2 가 나오고, 그러면 같은 격자의 두 점이 서로 다른 좌표계를
    갖게 되어 이웃 판정이 조용히 어긋난다.
    """

    individual: Individual
    result: FitnessResult
    steps: int = DEFAULT_STEPS

    @property
    def counts(self) -> tuple[int, ...]:
        """정수 좌표. 이웃 판정이 이걸 쓴다 — 부동소수 비교를 피한다."""
        total = sum(self.individual.genes)
        if total <= 1e-12:
            return tuple(0 for _ in self.individual.genes)
        return tuple(
            round(g / total * self.steps) for g in self.individual.genes
        )


def neighbors(point: GridPoint, points: Sequence[GridPoint]) -> list[GridPoint]:
    """한 칸 옆의 격자점들. 한 Analyst 에서 다른 Analyst 로 1스텝 옮긴 점이다.

    심플렉스 위에서 그 이동은 L1 거리 ``2/steps`` 다 — 한쪽이 줄고 한쪽이
    같은 양만큼 는다. 이 정의를 쓰면 모서리 점의 이웃이 자동으로 적어지고,
    그게 옳다. 모서리는 실제로 갈 곳이 적다.
    """
    target = point.counts
    out = []
    for other in points:
        if other is point:
            continue
        counts = other.counts
        if len(counts) != len(target):
            continue
        diff = [a - b for a, b in zip(counts, target, strict=True)]
        if sum(abs(d) for d in diff) == 2 and sorted(diff)[0] == -1 and sorted(diff)[-1] == 1:
            out.append(other)
    return out


def smoothed_fitness(point: GridPoint, points: Sequence[GridPoint]) -> float:
    """자기 자신과 이웃의 평균 적합도. **잡음을 줄이는 유일한 공짜 수단이다.**

    폴드를 늘려 SE 를 낮추는 데는 시간이 선형으로 든다. 격자는 이미 이웃을
    재 뒀으므로 평균이 공짜다. 지형이 매끄럽다는 가정이 들어가는데, 가중치
    → 성적은 실제로 매끄럽다(가중치를 0.1 옮긴다고 포트폴리오가 통째로
    바뀌지 않는다).

    ``-inf`` 인 점은 평균에서 뺀다 — 하나만 섞여도 평균이 통째로 ``-inf``
    가 되어 그 주변 지형이 통째로 사라진다.
    """
    family = [point, *neighbors(point, points)]
    usable = [p.result.fitness for p in family if math.isfinite(p.result.fitness)]
    if not usable:
        return float("-inf")
    return sum(usable) / len(usable)


def fold_noise(points: Sequence[GridPoint]) -> float:
    """격자 전체에서 모은 **적합도 1점의 표준오차** 추정.

    각 점의 ``per_fold_ir`` 은 같은 가중치를 서로 다른 구간에서 잰 값이다.
    그 흩어짐이 곧 측정 잡음이고, 폴드 k개의 중앙값이면 SE 는 대략
    ``std/sqrt(k)`` 다. 점마다 따로 재면 그 추정 자체가 잡음투성이라
    **격자 전체에서 풀링한다** — 잡음 규모는 가중치에 거의 안 붙는다.

    이 값이 ``best - uniform`` 보다 크면 격자는 아무것도 고르지 못한 것이다.
    그 판정을 사람이 눈대중으로 하게 두지 않으려고 숫자로 돌려준다.
    """
    spreads: list[float] = []
    for point in points:
        fold_irs = [ir for ir in point.result.per_fold_ir if math.isfinite(ir)]
        if len(fold_irs) < 2:
            continue
        mean = sum(fold_irs) / len(fold_irs)
        variance = sum((ir - mean) ** 2 for ir in fold_irs) / (len(fold_irs) - 1)
        spreads.append(math.sqrt(variance) / math.sqrt(len(fold_irs)))
    if not spreads:
        return float("nan")
    return sum(spreads) / len(spreads)


@dataclass(frozen=True)
class GridReport:
    """격자 한 판의 결과. **최고점과 잡음을 절대로 떼어 놓지 않는다.**"""

    points: tuple[GridPoint, ...]
    best: GridPoint
    best_smoothed: GridPoint
    uniform: GridPoint
    noise: float
    verdict: str

    @property
    def edge(self) -> float:
        """최고점 빼기 동일가중. 이 값이 잡음보다 작으면 아무 뜻이 없다."""
        return self.best.result.fitness - self.uniform.result.fitness

    @property
    def resolvable(self) -> bool:
        """잡음과 구분되는가. **채택 판정은 이것 하나로 하지 않는다** —
        홀드아웃(``evolution.holdout_report``)을 같이 봐야 한다.

        ``noise`` 가 NaN 이면 못 잰 것이고, 못 잰 것은 "구분된다" 가 아니다.
        0 은 다르다 — 폴드가 완전히 일치했다는 **측정 결과**이므로, 그때는
        양수 초과가 그대로 구분이 된다.
        """
        if math.isnan(self.noise):
            return False
        return self.edge > self.noise

    @property
    def uniform_rank(self) -> int:
        """동일가중이 격자에서 몇 위인가(1-based).

        **이 값이 상위권이면 이야기는 끝난다.** 66점 중 3위인 배합을 두고
        가중치를 튜닝했다고 말할 수 없다.
        """
        ordered = sorted(
            self.points, key=lambda p: p.result.fitness, reverse=True
        )
        for index, point in enumerate(ordered, start=1):
            if _same_weights(
                point.individual.normalized(), self.uniform.individual.normalized()
            ):
                return index
        return len(ordered)

    def summary(self) -> str:
        weights = self.best.individual.normalized()
        formatted = " · ".join(f"{k} {v:.2f}" for k, v in sorted(weights.items()))
        return (
            f"격자 {len(self.points)}점 · 최고 적합도 {self.best.result.fitness:+.4f} "
            f"({formatted}) · 동일가중 {self.uniform.result.fitness:+.4f} "
            f"({self.uniform_rank}위/{len(self.points)}) · 초과 {self.edge:+.4f} "
            f"· 잡음 SE {self.noise:.4f}. {self.verdict}"
        )


def search(
    analysts: Sequence[str],
    *,
    evaluate: Callable[[Individual], FitnessResult],
    steps: int = DEFAULT_STEPS,
    on_point: Callable[[GridPoint, int, int], None] | None = None,
) -> GridReport:
    """격자 전체를 채점한다.

    ``evaluate`` 는 개체 하나를 백테스트로 채점하는 함수다 —
    ``evolution.backtest_fitness`` 를 창고 격리와 함께 감싸 넘긴다
    (개체마다 오버레이를 새로 깔아야 journal 이 충돌하지 않는다).

    ``on_point`` 는 진행 상황 콜백이다. 격자는 몇 시간짜리라 중간에 죽으면
    무엇까지 했는지 알 수 있어야 한다 — GA 체크포인트가 생긴 이유와 같다.
    """
    individuals = simplex_grid(analysts, steps=steps)
    total = len(individuals)
    points: list[GridPoint] = []
    for index, individual in enumerate(individuals, start=1):
        point = GridPoint(
            individual=individual, result=evaluate(individual), steps=steps
        )
        points.append(point)
        if on_point is not None:
            on_point(point, index, total)

    uniform = uniform_individual(analysts)
    target = uniform.normalized()
    uniform_point = next(
        (p for p in points if _same_weights(p.individual.normalized(), target)),
        None,
    )
    if uniform_point is None:
        # 해상도가 Analyst 수로 안 나눠떨어지면 동일가중이 격자 위에 없다.
        # 비교 대상을 지어내지 않고 따로 한 번 잰다 — 없는 것을 가장 가까운
        # 점으로 때우면 "동일가중을 이겼다" 가 반올림 오차가 된다.
        uniform_point = GridPoint(
            individual=uniform, result=evaluate(uniform), steps=steps
        )

    best = max(points, key=lambda p: p.result.fitness)
    best_smoothed = max(points, key=lambda p: smoothed_fitness(p, points))
    noise = fold_noise(points)

    edge = best.result.fitness - uniform_point.result.fitness
    if math.isnan(noise):
        # **"못 쟀다" 와 "0 이었다" 를 같은 문구로 말하지 않는다.** 폴드가
        # 둘 미만이면 흩어짐을 정의할 수 없어 NaN 이고, 그때 최고점을 순위로
        # 읽으면 근거 없는 1위를 믿게 된다. 반대로 0 은 폴드가 완전히
        # 일치했다는 측정 결과라, 양수 초과는 그대로 뜻이 있다.
        verdict = (
            "폴드가 둘 미만이라 잡음을 못 쟀다 — 최고점을 순위로 읽지 마라. "
            "폴드를 둘 이상으로 늘릴 것"
        )
    elif edge <= noise:
        verdict = (
            f"최고점의 초과({edge:+.4f})가 잡음(SE {noise:.4f}) 안에 있다 — "
            "격자는 아무것도 고르지 못했다. 동일가중을 쓴다"
        )
    elif best_smoothed.counts != best.counts:
        verdict = (
            "최고점과 평활 최고점이 다르다 — 봉우리가 아니라 단발 잡음일 "
            "가능성이 높다. 평활 쪽을 믿고, 홀드아웃으로 확인할 것"
        )
    else:
        verdict = (
            "최고점이 잡음을 넘고 이웃 평활과도 일치한다 — 봉우리로 볼 만하다. "
            "홀드아웃(evolution.holdout_report)으로 확인할 것"
        )
    return GridReport(
        points=tuple(points),
        best=best,
        best_smoothed=best_smoothed,
        uniform=uniform_point,
        noise=noise,
        verdict=verdict,
    )
