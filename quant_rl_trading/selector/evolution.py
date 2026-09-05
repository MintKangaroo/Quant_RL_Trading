"""Analyst 가중치 진화 — selector.md §4. **아직 룰 베이스라인 위의 실험이다.**

    population 64, generations 40, tournament k=3, SBX(eta=15) 0.9,
    gaussian mutation sigma=0.05 rate 0.2, elitism 2
    fitness = IR(검증구간) - λ_L1·Σ|wᵢ| - λ_turn·회전율

## 유전자는 스칼라가 아니라 원가중치다

``Individual.genes`` 는 Analyst 별 **정규화 전** 값이다([0, 1] 박스). 실제
합성(``combine.combined_scores``)에는 ``normalized()`` 를 쓴다 — 그 합성 공식
자체가 Σwᵢ 로 나눠 스케일에 무관하기 때문에, 정규화 안 된 값 그대로 넘기면
같은 표현형이 무한히 많은 유전형을 갖게 되어 교차·변이가 의미를 잃는다.

L1 페널티는 **원가중치**에 건다(``Individual.l1``). 정규화된 벡터에 걸면
Σ|wᵢ| ≡ 1(합이 1인 음이 아닌 벡터라서)이라 상수가 되어 아무것도 못 민다.
원가중치에 걸면 전체를 균일하게 줄이는 것도 이론상 이득이지만(합성은 스케일
불변이므로), 그 방향은 결국 ``combine.EPSILON`` 문턱을 넘어 Analyst 가 탈락하는
지점에서 IR 을 깎는다 — 그래서 무한정 줄어들지 않고 자기 제한적이다. 완벽한
해는 아니다. 안정성 검사가 그 잔여 불안정성까지 함께 잡아낸다.

## 적합도는 IR 이 아니라 IR 의 대역이다

``backtest.stats.Performance.return_over_vol`` 은 **IR 이 아니다** — 벤치마크
초과수익이 아니라 절대수익/변동성이다(총수익지수가 창고에 없어서, backtest.md
§5). 진짜 IR 이 들어오기 전까지는 이 값을 쓴다. 다른 이름을 붙이면 나중에
"이게 IR 이었나" 를 아무도 못 묻게 되므로, 여기서도 ``ir_median`` 이라 부르되
이 문서가 그 경고를 들고 있다.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from quant_rl_trading.backtest import loop as loop_module
from quant_rl_trading.backtest import stats as stats_module
from quant_rl_trading.collectors.market_hours import Market, trading_days

if TYPE_CHECKING:
    from quant_rl_trading.store import Store

ANALYST_WEIGHTS = "analyst_weights"

#: 유전자 박스 제약. SBX·변이가 이 안에서만 움직인다.
GENE_LOWER = 0.0
GENE_UPPER = 1.0

#: kickoff 3-4 가 못 박은 값. config 가 아니라 여기 상수인 이유는 이게
#: population·generations·l1_penalty(``store.config``)와 달리 **탐색 규모가
#: 아니라 알고리즘 자체의 정의**이기 때문이다 — 바뀌면 다른 알고리즘이 된다.
DEFAULT_TOURNAMENT_K = 3
DEFAULT_CROSSOVER_RATE = 0.9
DEFAULT_SBX_ETA = 15.0
DEFAULT_MUTATION_SIGMA = 0.05
DEFAULT_MUTATION_RATE = 0.2
DEFAULT_ELITISM = 2
#: 조기 종료. selector.md §4 과적합 방지 표.
DEFAULT_PATIENCE = 10

#: 회전율 페널티 계수. config 에 없다(``selector.l1_penalty`` 만 있다) — 팀장
#: 지시로 이 파일은 config 를 새로 만들 권한이 없다. CLI/함수 인자로 덮을 것.
DEFAULT_TURNOVER_PENALTY = 0.05

#: 안정성 검사 문턱. 상위 10개 정규화 가중치의 평균 쌍별 L1 거리.
#: 두 벡터가 완전히 다른 원소에 몰려 있으면(one-hot vs one-hot) 거리는 최대
#: 2 다. 0.25 는 그 8분의 1 — "거의 같은 배합" 정도로 잡았다. 근거가 이론이
#: 아니라 어림값이라는 것을 여기 적어 둔다.
DEFAULT_STABILITY_THRESHOLD = 0.25
DEFAULT_STABILITY_TOP_N = 10

#: 안정성 검사의 귀무분포. ``stability_report`` 가 재는 "상위 개체가 서로 비슷한
#: 정도" 는 **지형의 봉우리만이 아니라 작은 개체군의 유전적 드리프트로도** 작아
#: 진다. 적합도를 유전자와 무관한 난수로 바꿔 시드 20개를 돌려 실측한 결과
#: (2026-08-15), 신호가 전혀 없는 지형에서도 이렇게 나온다:
#:
#:     pop 16 × gen  2 →  0/20 통과 (거리 중앙값 0.488)
#:     pop 16 × gen 15 → 18/20 통과 (거리 중앙값 0.099)   ← 거짓 양성 90%
#:     pop 64 × gen 15 →  1/20 통과 (거리 중앙값 0.497)
#:     pop 64 × gen 40 →  7/20 통과 (거리 중앙값 0.295)
#:
#: 즉 **거리 하나만으로는 노이즈와 봉우리를 구분할 수 없다.** 개체군이 작고
#: 세대가 길수록 드리프트가 이겨서, 검사가 막으라고 만들어진 바로 그 경우에
#: 통과 도장을 찍는다. 채택 판정은 이 거리와 함께 반드시
#: ``holdout_report`` (동일가중 대비 홀드아웃 성적) 를 봐야 한다.
NOISE_FLOOR_DISTANCE = {
    (16, 15): 0.099,
    (16, 40): 0.095,
    (64, 15): 0.497,
    (64, 40): 0.295,
}

#: 드리프트 귀무분포를 몇 번 복제해 만들지. 이 복제는 **백테스트를 안 돈다** —
#: GA 연산자만 난수 적합도로 돌리므로 pop 64 × gen 40 에서도 초 단위다.
DEFAULT_NULL_REPLICATES = 40

#: 귀무분포 하위 몇 분위를 통과선으로 볼지. 0.05 면 **거짓 양성 5%** 다
#: (정의상 — 신호가 없는 지형의 5%가 우연히 이보다 촘촘하게 몰린다).
#: 옛 절대 문턱 방식은 같은 조건에서 90% 였다.
NULL_ALPHA = 0.05


# ---------------------------------------------------------------------------
# 유전자
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Individual:
    """한 후보 가중치 배합. ``genes[i]`` 는 ``analysts[i]`` 의 원가중치."""

    analysts: tuple[str, ...]
    genes: tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.analysts) != len(self.genes):
            raise ValueError("analysts 와 genes 길이가 다르다")

    def as_dict(self) -> dict[str, float]:
        return dict(zip(self.analysts, self.genes, strict=True))

    def normalized(self) -> dict[str, float]:
        """합성에 넘길 값. 합이 1이 되게 정규화 — selector.md §2."""
        total = sum(self.genes)
        if total <= 1e-12:
            return dict.fromkeys(self.analysts, 0.0)
        return {a: g / total for a, g in zip(self.analysts, self.genes, strict=True)}

    def l1(self) -> float:
        """페널티 항의 Σ|wᵢ|. 원가중치 기준(모듈 docstring 참고)."""
        return sum(abs(g) for g in self.genes)

    def gene_hash(self) -> str:
        """결정론적 짧은 지문. 적재 run_id 를 만드는 데 쓴다."""
        raw = ",".join(f"{g:.10f}" for g in self.genes)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def random_individual(analysts: Sequence[str], rng: np.random.Generator) -> Individual:
    genes = tuple(float(v) for v in rng.uniform(GENE_LOWER, GENE_UPPER, size=len(analysts)))
    return Individual(analysts=tuple(analysts), genes=genes)


def initial_population(
    analysts: Sequence[str], size: int, rng: np.random.Generator
) -> list[Individual]:
    if size <= 0:
        raise ValueError("population 은 1 이상이어야 한다")
    return [random_individual(analysts, rng) for _ in range(size)]


# ---------------------------------------------------------------------------
# 연산자 — 선택 · 교차 · 변이 · 엘리트
# ---------------------------------------------------------------------------


def tournament_select(
    population: Sequence[Individual],
    fitnesses: Sequence[float],
    k: int,
    rng: np.random.Generator,
) -> Individual:
    """k 명을 뽑아 그중 가장 적합한 것. k=3 이 kickoff 명세.

    **중복 없이** 뽑는다(``replace=False``) — 복원추출을 쓰면 population 이
    작을 때 같은 개체가 토너먼트에 두 번 들어가 사실상 k 가 줄어든다.
    """
    size = min(k, len(population))
    indices = rng.choice(len(population), size=size, replace=False)
    best = max(indices, key=lambda i: fitnesses[int(i)])
    return population[int(best)]


def sbx_crossover(
    parent_a: Individual,
    parent_b: Individual,
    *,
    eta: float,
    rng: np.random.Generator,
    lower: float = GENE_LOWER,
    upper: float = GENE_UPPER,
) -> tuple[Individual, Individual]:
    """모의 이진 교차(SBX). 유전자별로 독립 적용한다.

    두 부모 값이 같으면(``|ga-gb|`` 가 극히 작으면) 그대로 물려준다 — beta
    공식이 그 근방에서 불안정하다.
    """
    genes_a: list[float] = []
    genes_b: list[float] = []
    for gene_a, gene_b in zip(parent_a.genes, parent_b.genes, strict=True):
        if abs(gene_a - gene_b) < 1e-14 or rng.random() > 0.5:
            genes_a.append(gene_a)
            genes_b.append(gene_b)
            continue
        u = float(rng.random())
        if u <= 0.5:
            beta = (2.0 * u) ** (1.0 / (eta + 1.0))
        else:
            beta = (1.0 / (2.0 * (1.0 - u))) ** (1.0 / (eta + 1.0))
        child_a = 0.5 * ((1 + beta) * gene_a + (1 - beta) * gene_b)
        child_b = 0.5 * ((1 - beta) * gene_a + (1 + beta) * gene_b)
        genes_a.append(min(max(child_a, lower), upper))
        genes_b.append(min(max(child_b, lower), upper))
    return (
        replace(parent_a, genes=tuple(genes_a)),
        replace(parent_b, genes=tuple(genes_b)),
    )


def gaussian_mutate(
    individual: Individual,
    *,
    sigma: float,
    rate: float,
    rng: np.random.Generator,
    lower: float = GENE_LOWER,
    upper: float = GENE_UPPER,
) -> Individual:
    """유전자마다 독립적으로 ``rate`` 확률로 N(0, sigma) 를 더한다."""
    genes = list(individual.genes)
    for i in range(len(genes)):
        if rng.random() < rate:
            genes[i] = min(max(genes[i] + float(rng.normal(0.0, sigma)), lower), upper)
    return replace(individual, genes=tuple(genes))


def next_generation(
    population: Sequence[Individual],
    fitnesses: Sequence[float],
    *,
    tournament_k: int,
    crossover_rate: float,
    sbx_eta: float,
    mutation_sigma: float,
    mutation_rate: float,
    elitism: int,
    rng: np.random.Generator,
) -> list[Individual]:
    """다음 세대. 엘리트는 **변이 없이** 그대로 넘어간다 — 적합도가 재평가될
    때(다른 폴드로) 우연히 나빠질 수 있지만, 그건 리샘플링이 하는 일이지 여기서
    또 흔들 일이 아니다.
    """
    ranked = sorted(range(len(population)), key=lambda i: fitnesses[i], reverse=True)
    elites = [population[i] for i in ranked[:elitism]]
    children: list[Individual] = list(elites)
    while len(children) < len(population):
        parent_a = tournament_select(population, fitnesses, tournament_k, rng)
        parent_b = tournament_select(population, fitnesses, tournament_k, rng)
        if rng.random() < crossover_rate:
            child_a, child_b = sbx_crossover(parent_a, parent_b, eta=sbx_eta, rng=rng)
        else:
            child_a, child_b = parent_a, parent_b
        child_a = gaussian_mutate(
            child_a, sigma=mutation_sigma, rate=mutation_rate, rng=rng
        )
        children.append(child_a)
        if len(children) < len(population):
            child_b = gaussian_mutate(
                child_b, sigma=mutation_sigma, rate=mutation_rate, rng=rng
            )
            children.append(child_b)
    return children[: len(population)]


# ---------------------------------------------------------------------------
# 안정성 검사 — selector.md §4, "이 검사를 통과하지 못한 가중치는 채택하지 않는다"
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StabilityReport:
    top_n: int
    mean_pairwise_distance: float
    threshold: float
    stable: bool
    verdict: str
    #: 드리프트 귀무분포의 하위 ``NULL_ALPHA`` 분위. 관측 거리가 이보다 작아야
    #: "유전적 드리프트만으로는 설명 안 된다" 고 말할 수 있다. None 이면 귀무
    #: 분포를 안 재고 절대 문턱만으로 판정한 것이다(옛 방식 — 못 믿는다).
    null_quantile: float | None = None
    null_replicates: int = 0

    def summary(self) -> str:
        head = (
            f"상위 {self.top_n}개 평균 쌍별 거리 {self.mean_pairwise_distance:.3f} "
            f"(문턱 {self.threshold:.3f}"
        )
        if self.null_quantile is not None:
            head += (
                f", 드리프트 귀무 {int(NULL_ALPHA * 100)}%분위 {self.null_quantile:.3f} "
                f"· 복제 {self.null_replicates}회"
            )
        return f"{head}) → {'안정' if self.stable else '불안정'}. {self.verdict}"


def mean_pairwise_distance(individuals: Sequence[Individual]) -> float:
    """정규화 가중치의 평균 쌍별 L1 거리. 0 이면 표현형이 한 점으로 붕괴했다.

    ``stability_report`` 가 상위 N개에 쓰는 것과 같은 척도다. **개체군 전체**에
    걸어 세대마다 기록하면 다양성 붕괴가 언제 일어났는지가 보인다 — 마지막
    한 번만 재면 "붕괴한 채로 끝났다" 는 것만 알고 언제부터인지를 모른다.
    """
    if len(individuals) < 2:
        return 0.0
    analysts = individuals[0].analysts
    vectors = [
        [ind.normalized().get(a, 0.0) for a in analysts] for ind in individuals
    ]
    return statistics.mean(
        sum(abs(x - y) for x, y in zip(v1, v2, strict=True))
        for v1, v2 in itertools.combinations(vectors, 2)
    )


def gene_spread(individuals: Sequence[Individual]) -> float:
    """원가중치 축별 표준편차의 평균 — **유전형** 다양성.

    표현형 거리(``mean_pairwise_distance``)와 따로 보는 이유: 합성 공식이 스케일
    불변이라 ``(0.2,0.2,0.2)`` 와 ``(0.8,0.8,0.8)`` 은 표현형이 같다. 표현형만
    보면 다양성이 0 인데 유전형은 아직 퍼져 있는 상태가 존재하고, 그때 진화는
    아직 죽지 않았다.
    """
    if len(individuals) < 2:
        return 0.0
    genes = np.array([ind.genes for ind in individuals], dtype=float)
    return float(genes.std(axis=0).mean())


@dataclass(frozen=True)
class GAParams:
    """드리프트 귀무분포를 만들 때 진짜 실행과 맞춰야 하는 값들.

    귀무분포는 "같은 연산자를 같은 규모로 돌렸을 때 **신호가 없어도** 얼마나
    몰리는가" 라서, 하나라도 다르면 다른 분포가 된다.
    """

    n_analysts: int
    population_size: int
    generations: int
    tournament_k: int = DEFAULT_TOURNAMENT_K
    crossover_rate: float = DEFAULT_CROSSOVER_RATE
    sbx_eta: float = DEFAULT_SBX_ETA
    mutation_sigma: float = DEFAULT_MUTATION_SIGMA
    mutation_rate: float = DEFAULT_MUTATION_RATE
    elitism: int = DEFAULT_ELITISM
    stability_top_n: int = DEFAULT_STABILITY_TOP_N


def drift_null_distances(
    params: GAParams,
    *,
    replicates: int = DEFAULT_NULL_REPLICATES,
    seed: int = 0,
) -> list[float]:
    """**적합도가 유전자와 무관할 때** 상위 N개가 얼마나 몰리는지의 분포.

    이것이 이 모듈에서 제일 중요한 함수다. ``stability_report`` 가 재는 거리는
    지형의 봉우리만이 아니라 **유전적 드리프트**로도 작아진다 — 토너먼트 선택과
    엘리트 보존은 적합도가 순수 난수여도 개체군을 한 점으로 몬다. 그래서 거리
    하나만 보면 "신호가 있다" 와 "개체군이 작고 세대가 길다" 를 구분할 수 없다.

    여기서는 **적합도를 난수로 바꾼 같은 규모의 진화**를 여러 번 돌려, 드리프트
    만으로 나올 수 있는 거리의 분포를 만든다. 관측 거리가 이 분포의 아래쪽
    꼬리에 있어야만 "드리프트로는 설명 안 된다" 고 말할 수 있다.

    백테스트를 돌지 않는다 — 난수 하나 뽑는 게 적합도라서 pop 64 × gen 40 도
    초 단위다. 진화 한 번이 몇 시간인 것에 비하면 공짜다.
    """
    if replicates < 1:
        raise ValueError("replicates 는 1 이상이어야 한다")
    analysts = tuple(f"_null{i}" for i in range(params.n_analysts))
    distances: list[float] = []
    for replicate in range(replicates):
        rng = np.random.default_rng((seed + 1) * 1_000_003 + replicate)

        def random_fitness(
            individual: Individual, generation: int, _rng: np.random.Generator = rng
        ) -> FitnessResult:
            value = float(_rng.normal(0.0, 1.0))
            return FitnessResult(
                individual=individual, fitness=value, ir_median=value,
                turnover_median=0.0, l1_term=0.0,
            )

        outcome = evolve(
            analysts=analysts,
            population_size=params.population_size,
            generations=params.generations,
            evaluate=random_fitness,
            tournament_k=params.tournament_k,
            crossover_rate=params.crossover_rate,
            sbx_eta=params.sbx_eta,
            mutation_sigma=params.mutation_sigma,
            mutation_rate=params.mutation_rate,
            elitism=params.elitism,
            # 조기 종료는 끈다 — 난수 적합도는 최고값이 계속 갱신되거나 전혀
            # 갱신되지 않아, patience 가 걸리면 세대 수가 실제 실행과 달라진다.
            patience=params.generations + 1,
            seed=replicate,
            stability_top_n=params.stability_top_n,
            # **재귀 금지.** 귀무분포를 만드는 중에 또 귀무분포를 만들면 끝나지
            # 않는다. 여기서는 거리만 필요하다.
            null_replicates=0,
        )
        distances.append(outcome.stability.mean_pairwise_distance)
    return sorted(distances)


def stability_report(
    top: Sequence[Individual],
    *,
    threshold: float = DEFAULT_STABILITY_THRESHOLD,
    null_distances: Sequence[float] | None = None,
) -> StabilityReport:
    """상위 개체들의 정규화 가중치가 얼마나 비슷한가.

    **거리가 작다는 것만으로는 봉우리의 증거가 못 된다.** 토너먼트 선택과 엘리트
    보존은 적합도가 순수 난수여도 개체군을 한 점으로 몬다 — 옛 판정(절대 문턱
    0.25 하나)은 pop 16 × gen 15 에서 **신호가 전혀 없는 지형을 20번 중 18번**
    "안정" 으로 통과시켰다(``NOISE_FLOOR_DISTANCE``). 자기가 막으라고 만들어진
    바로 그 경우에서 통과 도장을 찍은 것이다.

    그래서 ``null_distances`` — 같은 규모·같은 연산자로 **적합도만 난수로** 돌린
    드리프트 귀무분포 — 를 같이 받는다. 관측 거리가 그 분포의 하위
    ``NULL_ALPHA`` 분위보다 작아야만 "드리프트로는 설명 안 된다" 고 말한다.
    거짓 양성률이 정의상 ``NULL_ALPHA`` 로 내려간다.

    ``null_distances`` 를 안 주면 옛 방식(절대 문턱만)으로 판정하고, 리포트의
    ``null_quantile`` 이 None 으로 남아 **그 판정을 믿으면 안 된다는 표시**가 된다.
    """
    if len(top) < 2:
        return StabilityReport(
            top_n=len(top),
            mean_pairwise_distance=0.0,
            threshold=threshold,
            stable=False,
            verdict="개체가 2개 미만이라 안정성을 판단할 수 없다",
        )
    mean_distance = mean_pairwise_distance(top)
    within_threshold = mean_distance <= threshold

    if not null_distances:
        verdict = (
            "상위 개체들의 가중치가 서로 비슷하다 — 다만 **드리프트 귀무분포를 "
            "재지 않았다.** 이 판정만으로 채택하지 마라(작은 개체군에서는 신호가 "
            "없어도 이렇게 나온다)"
            if within_threshold
            else "상위 개체들의 가중치가 제각각이다 — 적합도 지형이 평평하다는 "
            "뜻이고, 이 진화 결과는 노이즈다. 동일가중을 쓰는 것이 낫다"
        )
        return StabilityReport(
            top_n=len(top),
            mean_pairwise_distance=mean_distance,
            threshold=threshold,
            stable=within_threshold,
            verdict=verdict,
        )

    quantile = float(np.quantile(np.asarray(null_distances, dtype=float), NULL_ALPHA))
    beats_drift = mean_distance < quantile
    stable = within_threshold and beats_drift
    if not within_threshold:
        verdict = (
            "상위 개체들의 가중치가 제각각이다 — 적합도 지형이 평평하다는 뜻이고, "
            "이 진화 결과는 노이즈다. 동일가중을 쓰는 것이 낫다"
        )
    elif not beats_drift:
        verdict = (
            f"상위 개체들이 몰려 있긴 하지만 **유전적 드리프트만으로 나올 수 있는 "
            f"정도다**(귀무 {int(NULL_ALPHA * 100)}%분위 {quantile:.3f}). 봉우리를 "
            "찾았다는 증거가 아니다 — 동일가중을 쓰는 것이 낫다"
        )
    else:
        verdict = (
            "상위 개체들이 드리프트로 설명되는 것보다 촘촘하게 몰렸다 — 적합도 "
            "지형에 실제 봉우리가 있다는 증거다. 다만 채택의 최종 근거는 "
            "홀드아웃 성적이다(holdout_report)"
        )
    return StabilityReport(
        top_n=len(top),
        mean_pairwise_distance=mean_distance,
        threshold=threshold,
        stable=stable,
        verdict=verdict,
        null_quantile=quantile,
        null_replicates=len(null_distances),
    )


# ---------------------------------------------------------------------------
# 세대 루프
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FitnessResult:
    individual: Individual
    fitness: float
    ir_median: float
    turnover_median: float
    l1_term: float
    per_fold_ir: tuple[float, ...] = ()
    notes: tuple[str, ...] = ()


EvaluateFn = Callable[[Individual, int], FitnessResult]


@dataclass(frozen=True)
class GenerationRecord:
    generation: int
    best_fitness: float
    mean_fitness: float
    best_individual: Individual
    #: 아래는 전부 2026-08-15 추가. 기존 세 필드의 뜻은 그대로다.
    #: 적합도 곡선만으로는 "수렴했다" 와 "다양성이 죽어 더 못 움직인다" 가
    #: 구분되지 않아서, 세대마다 같이 찍는다.
    worst_fitness: float = 0.0
    std_fitness: float = 0.0
    #: 개체군 **전체**의 표현형 다양성. 상위 N개만 보는 안정성 검사와 다르다.
    diversity: float = 0.0
    #: 상위 10개의 표현형 다양성 — 안정성 검사와 같은 척도를 세대마다.
    diversity_top: float = 0.0
    #: 유전형 다양성(원가중치 표준편차).
    gene_spread: float = 0.0
    #: 적합도가 -inf 인 개체 수. 폴드가 통째로 결과를 못 낸 개체다 —
    #: 이 값이 크면 적합도 곡선이 아니라 **창고·폴드 설정**을 먼저 의심해야 한다.
    failed: int = 0

    def as_json(self) -> dict[str, object]:
        """체크포인트 한 줄. 개체는 정규화 가중치로 남긴다(사람이 읽는다)."""
        return {
            "generation": self.generation,
            "best_fitness": self.best_fitness,
            "mean_fitness": self.mean_fitness,
            "worst_fitness": self.worst_fitness,
            "std_fitness": self.std_fitness,
            "diversity": self.diversity,
            "diversity_top": self.diversity_top,
            "gene_spread": self.gene_spread,
            "failed": self.failed,
            "best_weights": self.best_individual.normalized(),
            "best_genes": list(self.best_individual.genes),
        }


class JsonlCheckpoint:
    """세대마다 한 줄씩 append 하는 체크포인트.

    **왜 도구가 아니라 여기 있는가.** 2026-08-15 새벽, 16×15 진화가 3시간을
    돌다 죽었는데 로그에 세대가 한 줄도 없었다 — ``evolve`` 가 ``history`` 를
    다 모은 뒤 호출부가 한꺼번에 찍는 구조라, 중간에 죽으면 그때까지 한 것이
    통째로 사라진다. 진화는 몇 시간짜리 작업이고 이 기계는 다른 작업과 램을
    나눠 쓴다. **중단은 예외가 아니라 기본값이다.**

    ``flush()`` 를 매 줄 부른다 — 버퍼에 남은 줄은 죽을 때 같이 죽는다.
    파일은 append 로만 연다: 재실행이 앞선 실행의 기록을 지우면 안 된다.
    """

    def __init__(self, path: Path, *, every: int = 1) -> None:
        if every < 1:
            raise ValueError("checkpoint_every 는 1 이상이어야 한다")
        self.path = Path(path)
        self.every = every
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, record: GenerationRecord) -> None:
        if record.generation % self.every:
            return
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.as_json(), ensure_ascii=False) + "\n")
            handle.flush()


#: ``evolve`` 가 세대를 끝낼 때마다 부른다. 예외를 던지면 진화가 멈춘다 —
#: 체크포인트를 못 쓰는 상태로 몇 시간을 더 도는 것보다 낫다.
CheckpointFn = Callable[[GenerationRecord], None]


@dataclass
class EvolutionResult:
    population: list[Individual]
    fitnesses: list[FitnessResult]
    history: list[GenerationRecord]
    stopped_early: bool
    generations_run: int
    stability: StabilityReport
    #: 최종 채택 여부. 안정성 검사를 통과 못 하면 False — 진화 결과를 쓰지
    #: 말라는 뜻이다(호출부가 이 값을 무시하고 밀어붙이면 안 된다).
    adopt: bool = field(init=False)

    def __post_init__(self) -> None:
        self.adopt = self.stability.stable


def evolve(
    *,
    analysts: Sequence[str],
    population_size: int,
    generations: int,
    evaluate: EvaluateFn,
    tournament_k: int = DEFAULT_TOURNAMENT_K,
    crossover_rate: float = DEFAULT_CROSSOVER_RATE,
    sbx_eta: float = DEFAULT_SBX_ETA,
    mutation_sigma: float = DEFAULT_MUTATION_SIGMA,
    mutation_rate: float = DEFAULT_MUTATION_RATE,
    elitism: int = DEFAULT_ELITISM,
    patience: int = DEFAULT_PATIENCE,
    seed: int = 0,
    stability_top_n: int = DEFAULT_STABILITY_TOP_N,
    stability_threshold: float = DEFAULT_STABILITY_THRESHOLD,
    checkpoint: CheckpointFn | None = None,
    null_replicates: int = DEFAULT_NULL_REPLICATES,
) -> EvolutionResult:
    """population × generations 진화 한 번.

    ``evaluate(individual, generation)`` 이 적합도 평가다 — **실제 백테스트를
    돌리는 것은 이 함수의 책임이 아니다.** 호출부(``backtest_fitness`` 또는
    ``tools/run_evolution.py``)가 폴드 리샘플링·창고 격리를 쥐고, 여기는
    "세대마다 다른 값이 나올 수 있다" 는 것만 안다. 같은 시드 → 같은 population
    이력이 나온다(연산자가 전부 주입된 ``rng`` 만 쓴다) — ``evaluate`` 자체가
    결정론적이라면 전체가 결정론적이다.
    """
    if not analysts:
        raise ValueError(
            "진화시킬 Analyst 가 없다 — IC 0.03 을 통과한 것이 하나도 없다. "
            "이 상태에서 진화를 돌리면 노이즈를 최적화하게 된다(selector.md §2)"
        )
    rng = np.random.default_rng(seed)
    population = initial_population(tuple(analysts), population_size, rng)

    history: list[GenerationRecord] = []
    fitnesses: list[FitnessResult] = []
    best_fitness = float("-inf")
    best_generation = -1
    stopped_early = False
    generations_run = 0

    for generation in range(generations):
        fitnesses = [evaluate(individual, generation) for individual in population]
        scores = [result.fitness for result in fitnesses]
        best_index = max(range(len(scores)), key=lambda i: scores[i])
        # -inf 는 "탐색 안 한 지역" 이라 평균·표준편차를 삼켜 버린다. 통계는
        # 유한한 것만으로 내고, 몇 개가 실패했는지는 따로 센다.
        finite = [s for s in scores if s != float("-inf")]
        ranked_now = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        record = GenerationRecord(
            generation=generation,
            best_fitness=scores[best_index],
            mean_fitness=statistics.mean(scores),
            best_individual=population[best_index],
            worst_fitness=min(finite) if finite else float("-inf"),
            std_fitness=statistics.pstdev(finite) if len(finite) > 1 else 0.0,
            diversity=mean_pairwise_distance(population),
            diversity_top=mean_pairwise_distance(
                [population[i] for i in ranked_now[:stability_top_n]]
            ),
            gene_spread=gene_spread(population),
            failed=len(scores) - len(finite),
        )
        history.append(record)
        if checkpoint is not None:
            checkpoint(record)
        generations_run = generation + 1

        if scores[best_index] > best_fitness + 1e-9:
            best_fitness = scores[best_index]
            best_generation = generation
        elif generation - best_generation >= patience:
            stopped_early = True
            break

        population = next_generation(
            population,
            scores,
            tournament_k=tournament_k,
            crossover_rate=crossover_rate,
            sbx_eta=sbx_eta,
            mutation_sigma=mutation_sigma,
            mutation_rate=mutation_rate,
            elitism=elitism,
            rng=rng,
        )

    ranked = sorted(range(len(fitnesses)), key=lambda i: fitnesses[i].fitness, reverse=True)
    top = [fitnesses[i].individual for i in ranked[:stability_top_n]]
    # 귀무분포는 **실제로 돈 세대 수**로 만든다 — 조기 종료로 5세대만 돌았는데
    # 40세대짜리 드리프트 분포와 비교하면 통과가 너무 쉬워진다.
    null_distances = (
        drift_null_distances(
            GAParams(
                n_analysts=len(analysts),
                population_size=population_size,
                generations=generations_run,
                tournament_k=tournament_k,
                crossover_rate=crossover_rate,
                sbx_eta=sbx_eta,
                mutation_sigma=mutation_sigma,
                mutation_rate=mutation_rate,
                elitism=elitism,
                stability_top_n=stability_top_n,
            ),
            replicates=null_replicates,
            seed=seed,
        )
        if null_replicates > 0
        else None
    )
    stability = stability_report(
        top, threshold=stability_threshold, null_distances=null_distances
    )

    return EvolutionResult(
        population=population,
        fitnesses=fitnesses,
        history=history,
        stopped_early=stopped_early,
        generations_run=generations_run,
        stability=stability,
    )


# ---------------------------------------------------------------------------
# 적합도 — 실제 백테스트로. selector.md §3.
# ---------------------------------------------------------------------------


def _fold_end(market: Market, start: date, trading_days_count: int) -> date:
    """``start`` 부터 거래일 ``trading_days_count`` 개를 채우는 마지막 날."""
    span = timedelta(days=int(trading_days_count * 7 / 5) + 20)
    sessions = trading_days(market, start, start + span)
    if len(sessions) < trading_days_count:
        raise ValueError(
            f"{start} 부터 거래일 {trading_days_count}개를 채울 수 없다 "
            f"(가진 것 {len(sessions)}개) — 폴드 창을 줄이거나 구간을 늘릴 것"
        )
    return sessions[trading_days_count - 1]


def _annualized_turnover(performance: stats_module.Performance) -> float:
    """구간 회전율을 연 회전율로. 거래일이 없으면 0.

    적합도의 두 항(IR·회전율)이 **같은 시간 단위**여야 계수가 뜻을 갖는다.
    """
    if performance.days <= 0:
        return 0.0
    return performance.turnover * stats_module.TRADING_DAYS_PER_YEAR / performance.days


def backtest_fitness(
    store: Store,
    individual: Individual,
    *,
    market: str,
    fold_starts: Sequence[date],
    fold_trading_days: int,
    generation: int,
    weight_as_of: datetime,
    capital: float,
    warmup_days: int = 0,
    board: str = "KOSPI",
    l1_penalty: float = 0.0,
    turnover_penalty: float = DEFAULT_TURNOVER_PENALTY,
    produce_signals: bool = True,
) -> FitnessResult:
    """개체 하나를 **실제 백테스트**로 채점한다(selector.md §3 — 점수 상관이
    아니라 비용·라운딩 포함).

    ``store`` 는 이미 이 개체 전용으로 격리된 창고여야 한다 — journal 테이블과
    ``analyst_weights`` 를 다른 개체와 공유하면 같은 날짜의 주문·체결이
    충돌한다(자연키가 날짜 기준이라). ``tools/run_evolution.py`` 가 개체마다
    오버레이를 새로 깐다.

    ``weight_as_of`` 는 이 개체의 가중치가 **관측된 것으로 칠 시각**이다.
    모든 폴드의 워밍업 시작일보다 앞서야 한다 — 아니면 dual-time 게이트가
    가중치를 못 보고 후보가 조용히 0건으로 끝난다(워크포워드 규칙,
    backtest.md §7).

    ``produce_signals=False`` 면 Analyst 를 다시 돌리지 않고 **창고에 이미 있는
    신호**를 쓴다. 신호는 가중치와 무관하므로(가중치는 합성 단계에서만 쓴다)
    개체마다 다시 계산할 이유가 없다 — 프라이밍 레이어가 한 번 채워 두고
    개체 레이어가 링크로 보면 된다. 이것이 진화 비용의 대부분이다.

    적합도 = median(IR) - λ_L1·Σ|wᵢ| - λ_turn·median(회전율).
    폴드가 하나도 성적을 못 내면(거래일이 없거나 전부 실패) -inf 를 준다 —
    "탐색하지 않은 지역" 과 "나쁜 지역" 을 구분해야 진화가 그쪽으로 다시
    가지 않는다.
    """
    market_enum = Market(market)
    weights = individual.normalized()
    rows = [
        {
            "entity_id": analyst,
            "valid_from": weight_as_of,
            "observed_at": weight_as_of,
            "source": "evolution",
            "market": market,
            "weight": weight,
            "analyst_version": f"evo-g{generation}-{individual.gene_hash()}",
        }
        for analyst, weight in weights.items()
    ]
    run_id = f"evolution-{market}-g{generation}-{individual.gene_hash()}"
    if not store.ingest_run_recorded(ANALYST_WEIGHTS, run_id):
        store.append(ANALYST_WEIGHTS, rows, ingest_run_id=run_id, source="evolution")

    ir_values: list[float] = []
    turnover_values: list[float] = []
    notes: list[str] = []
    for fold_start in fold_starts:
        try:
            fold_end = _fold_end(market_enum, fold_start, fold_trading_days)
        except ValueError as error:
            notes.append(str(error))
            continue
        result = loop_module.run(
            store,
            start=fold_start,
            end=fold_end,
            market=market,
            capital=capital,
            board=board,
            warmup_days=warmup_days,
            produce_signals=produce_signals,
        )
        if result.performance is None:
            notes.append(f"{fold_start}: 성적 없음({'; '.join(result.notes)})")
            continue
        ir_values.append(result.performance.return_over_vol)
        # ``Performance.turnover`` 는 **구간 누적**이다(체결금액/평균NAV). 리포트는
        # 그 정의로 보는 게 맞지만, 적합도에서는 IR 항이 연율화된 값이라 그대로
        # 빼면 두 항의 단위가 다르다 — 폴드를 길게 잡을수록 회전율 페널티만
        # 자동으로 커진다. 여기서만 연율로 환산해 맞춘다.
        turnover_values.append(_annualized_turnover(result.performance))

    if not ir_values:
        return FitnessResult(
            individual=individual,
            fitness=float("-inf"),
            ir_median=0.0,
            turnover_median=0.0,
            l1_term=individual.l1() * l1_penalty,
            notes=tuple(notes) or ("폴드 전부 결과 없음",),
        )

    ir_median = statistics.median(ir_values)
    turnover_median = statistics.median(turnover_values)
    l1_term = l1_penalty * individual.l1()
    fitness = ir_median - l1_term - turnover_penalty * turnover_median
    return FitnessResult(
        individual=individual,
        fitness=fitness,
        ir_median=ir_median,
        turnover_median=turnover_median,
        l1_term=l1_term,
        per_fold_ir=tuple(ir_values),
        notes=tuple(notes),
    )


# ---------------------------------------------------------------------------
# 홀드아웃 — selector.md §4 "최종 검증은 별도 테스트 폴드에서 딱 한 번"
# ---------------------------------------------------------------------------


def uniform_individual(analysts: Sequence[str]) -> Individual:
    """동일가중 개체. 진화가 이겨야 할 상대다 — selector.md §4 는 안정성 검사를
    통과 못 하면 "동일가중을 쓰는 것이 낫다" 고 했다. 그 문장에 값을 붙이려면
    동일가중을 **같은 폴드에서 같은 방식으로 채점**해야 한다.
    """
    return Individual(analysts=tuple(analysts), genes=tuple([1.0] * len(analysts)))


@dataclass(frozen=True)
class HoldoutReport:
    """학습에 한 번도 안 쓴 폴드에서 잰 성적."""

    folds: tuple[date, ...]
    train_fitness: float
    best: FitnessResult
    uniform: FitnessResult
    verdict: str

    @property
    def edge(self) -> float:
        """동일가중 대비 초과 IR. **이 값이 0 이하면 진화는 아무것도 못 벌었다.**"""
        return self.best.ir_median - self.uniform.ir_median

    @property
    def generalization_gap(self) -> float:
        """학습 적합도 빼기 홀드아웃 적합도. 크면 학습 구간에 맞춘 것이다."""
        return self.train_fitness - self.best.fitness

    @property
    def beats_uniform(self) -> bool:
        return self.edge > 0.0

    def summary(self) -> str:
        return (
            f"홀드아웃 {len(self.folds)}폴드 · 최고개체 IR {self.best.ir_median:+.4f} vs "
            f"동일가중 IR {self.uniform.ir_median:+.4f} (초과 {self.edge:+.4f}) · "
            f"일반화 격차 {self.generalization_gap:+.4f}. {self.verdict}"
        )


def holdout_report(
    best: Individual,
    *,
    analysts: Sequence[str],
    folds: Sequence[date],
    train_fitness: float,
    evaluate: Callable[[Individual], FitnessResult],
) -> HoldoutReport:
    """진화 결과를 **학습에 안 쓴 폴드**에서 딱 한 번 채점한다.

    ``evaluate`` 가 홀드아웃 폴드 위의 백테스트다 — ``evolve`` 와 같은 이유로
    창고·오버레이 격리는 호출부 몫이다(개체마다 새 레이어를 깔아야 journal 이
    충돌하지 않는다).

    **동일가중을 같이 잰다.** 이것이 이 함수의 핵심이다. 홀드아웃 IR 이 양수라는
    것만으로는 진화가 기여했다는 증거가 안 된다 — 그 구간이 그냥 좋은 장이었을
    수 있다. 같은 폴드의 동일가중을 빼야 **가중치 탐색이 번 것**이 남는다.

    안정성 검사(``stability_report``)를 대체하는 게 아니라 **보완한다**:
    그 검사는 작은 개체군에서 드리프트를 봉우리로 오인한다(``NOISE_FLOOR_DISTANCE``).
    홀드아웃은 그 오인에 면역이다 — 드리프트는 홀드아웃 성적을 만들어 주지 않는다.
    """
    if not folds:
        raise ValueError(
            "홀드아웃 폴드가 없다 — 학습 구간 밖에 폴드를 만들 수 있는 날짜가 "
            "있어야 한다. 구간을 넓히거나 fold_days 를 줄일 것"
        )
    best_result = evaluate(best)
    uniform_result = evaluate(uniform_individual(analysts))
    edge = best_result.ir_median - uniform_result.ir_median
    if best_result.fitness == float("-inf"):
        verdict = (
            "홀드아웃에서 성적을 하나도 못 냈다 — 진화 결과를 채택할 근거가 없다"
        )
    elif edge > 0:
        verdict = (
            "진화한 가중치가 홀드아웃에서 동일가중을 이겼다 — 학습 구간 밖에서도 "
            "기여가 남았다"
        )
    else:
        verdict = (
            "진화한 가중치가 홀드아웃에서 동일가중을 못 이겼다 — 학습 구간에서만 "
            "좋았던 것이고, 동일가중을 쓰는 것이 낫다(selector.md §4)"
        )
    return HoldoutReport(
        folds=tuple(folds),
        train_fitness=train_fitness,
        best=best_result,
        uniform=uniform_result,
        verdict=verdict,
    )


def resample_folds(
    available_starts: Sequence[date],
    count: int,
    rng: np.random.Generator,
    *,
    min_gap_days: int = 0,
) -> list[date]:
    """세대마다 검증 구간을 바꾼다(selector.md §4 과적합 방지).

    복원추출 없이 뽑되, 가용한 시작일보다 ``count`` 가 많으면 있는 만큼만
    준다 — 없는 폴드를 지어내지 않는다.

    ``min_gap_days`` 는 뽑힌 시작일 사이의 **최소 간격(달력일)** 이다. 0 이면
    옛 동작 그대로다.

    **왜 필요한가.** 시작일만 무작위로 뽑으면 두 폴드가 거의 같은 구간이 될 수
    있다. 2026-08-15 실측(seed 0, 15거래일 폴드, 2개월 후보군): 15세대 중 네
    세대에서 두 시작일이 2~6일 차이였다 — 15거래일 창이니 **13일가량 겹친다.**
    그 세대에서는 "여러 시작일의 중앙값으로 평가한다" 는 과적합 방어가 사실상
    폴드 하나짜리가 되고, 그런 줄 모른 채 적합도를 믿게 된다.

    간격을 못 채우면 **있는 만큼만 준다.** 억지로 채우려고 겹치는 폴드를 끼워
    넣으면 방어가 있다고 착각하게 되므로, 모자라는 쪽이 정직하다.
    """
    if not available_starts:
        return []
    n = min(count, len(available_starts))
    if min_gap_days <= 0:
        indices = rng.choice(len(available_starts), size=n, replace=False)
        return [available_starts[int(i)] for i in indices]

    # 무작위 순서로 훑으며 이미 뽑은 것과 충분히 떨어진 것만 취한다. 그리디라
    # 최대 개수를 보장하진 않지만, 편향 없이 뽑으면서 겹침을 막는다.
    order = rng.permutation(len(available_starts))
    picked: list[date] = []
    for index in order:
        candidate = available_starts[int(index)]
        if all(abs((candidate - chosen).days) >= min_gap_days for chosen in picked):
            picked.append(candidate)
            if len(picked) == n:
                break
    return picked
