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
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

import numpy as np

from quant_rl_trading.backtest import loop as loop_module
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

    def summary(self) -> str:
        return (
            f"상위 {self.top_n}개 평균 쌍별 거리 {self.mean_pairwise_distance:.3f} "
            f"(문턱 {self.threshold:.3f}) → {'안정' if self.stable else '불안정'}. "
            f"{self.verdict}"
        )


def stability_report(
    top: Sequence[Individual],
    *,
    threshold: float = DEFAULT_STABILITY_THRESHOLD,
) -> StabilityReport:
    """상위 개체들의 정규화 가중치가 얼마나 비슷한가.

    비슷하면(거리가 작으면) 적합도 지형에 봉우리가 있다는 뜻이고 결과를 믿을
    수 있다. 제각각이면 지형이 평평해 진화가 노이즈를 골랐다는 뜻이고, 그때는
    **동일가중을 쓰는 것이 낫다** — 이 함수는 그 문장을 그대로 돌려준다.
    """
    if len(top) < 2:
        return StabilityReport(
            top_n=len(top),
            mean_pairwise_distance=0.0,
            threshold=threshold,
            stable=False,
            verdict="개체가 2개 미만이라 안정성을 판단할 수 없다",
        )
    analysts = top[0].analysts
    normed = [ind.normalized() for ind in top]
    vectors = [[n.get(a, 0.0) for a in analysts] for n in normed]
    distances = [
        sum(abs(x - y) for x, y in zip(v1, v2, strict=True))
        for v1, v2 in itertools.combinations(vectors, 2)
    ]
    mean_distance = statistics.mean(distances)
    stable = mean_distance <= threshold
    verdict = (
        "상위 개체들의 가중치가 서로 비슷하다 — 적합도 지형에 실제 봉우리가 "
        "있다. 진화 결과를 채택할 수 있다"
        if stable
        else "상위 개체들의 가중치가 제각각이다 — 적합도 지형이 평평하다는 "
        "뜻이고, 이 진화 결과는 노이즈다. 동일가중을 쓰는 것이 낫다"
    )
    return StabilityReport(
        top_n=len(top),
        mean_pairwise_distance=mean_distance,
        threshold=threshold,
        stable=stable,
        verdict=verdict,
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
        history.append(
            GenerationRecord(
                generation=generation,
                best_fitness=scores[best_index],
                mean_fitness=statistics.mean(scores),
                best_individual=population[best_index],
            )
        )
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
    stability = stability_report(top, threshold=stability_threshold)

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
        turnover_values.append(result.performance.turnover)

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


def resample_folds(
    available_starts: Sequence[date],
    count: int,
    rng: np.random.Generator,
) -> list[date]:
    """세대마다 검증 구간을 바꾼다(selector.md §4 과적합 방지).

    복원추출 없이 뽑되, 가용한 시작일보다 ``count`` 가 많으면 있는 만큼만
    준다 — 없는 폴드를 지어내지 않는다.
    """
    if not available_starts:
        return []
    n = min(count, len(available_starts))
    indices = rng.choice(len(available_starts), size=n, replace=False)
    return [available_starts[int(i)] for i in indices]
