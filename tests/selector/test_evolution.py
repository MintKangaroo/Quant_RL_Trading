"""가중치 진화 — GA 연산자는 순수 함수로, 적합도는 진짜 백테스트로 검증한다.

목으로 적합도를 흉내 내는 진화 테스트는 진화를 검증하지 않는다(selector.md
§3) — 그래서 통합 테스트 한 개는 ``backtest_fitness`` 로 실제 창고 위를 돈다.
나머지는 연산자 자체(선택·교차·변이·안정성 검사)의 계약을 목적으로 한다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from quant_rl_trading.collectors.market_hours import Market, trading_days
from quant_rl_trading.selector import evolution

SEOUL = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 8, 12, 6, 40, tzinfo=UTC)


# ---------------------------------------------------------------------------
# 유전자 · 연산자
# ---------------------------------------------------------------------------


def test_normalized은_합이_1이다() -> None:
    individual = evolution.Individual(analysts=("a", "b", "c"), genes=(0.2, 0.6, 0.2))
    normed = individual.normalized()
    assert sum(normed.values()) == pytest.approx(1.0)
    assert normed["b"] > normed["a"] == normed["c"]


def test_유전자가_전부_0이면_normalized도_전부_0이다() -> None:
    individual = evolution.Individual(analysts=("a", "b"), genes=(0.0, 0.0))
    assert individual.normalized() == {"a": 0.0, "b": 0.0}


def test_analysts와_genes_길이가_다르면_거부한다() -> None:
    with pytest.raises(ValueError, match="길이"):
        evolution.Individual(analysts=("a", "b"), genes=(0.1,))


def test_초기_population은_박스_제약_안에_있다() -> None:
    rng = np.random.default_rng(0)
    population = evolution.initial_population(("risk", "event", "fundamental"), 32, rng)
    assert len(population) == 32
    for individual in population:
        assert len(individual.genes) == 3
        assert all(evolution.GENE_LOWER <= g <= evolution.GENE_UPPER for g in individual.genes)


def test_토너먼트_선택은_최고를_이긴_적이_없는_개체는_안_고른다() -> None:
    population = [
        evolution.Individual(analysts=("x",), genes=(0.1,)),
        evolution.Individual(analysts=("x",), genes=(0.5,)),
        evolution.Individual(analysts=("x",), genes=(0.9,)),
    ]
    fitnesses = [0.1, 0.5, 0.9]
    rng = np.random.default_rng(1)
    # k = population 전체면 매번 최고가 뽑혀야 한다.
    for _ in range(20):
        winner = evolution.tournament_select(population, fitnesses, k=3, rng=rng)
        assert winner is population[2]


def test_sbx_교차는_박스_밖으로_안_나간다() -> None:
    rng = np.random.default_rng(2)
    parent_a = evolution.Individual(analysts=("a", "b"), genes=(0.0, 1.0))
    parent_b = evolution.Individual(analysts=("a", "b"), genes=(1.0, 0.0))
    for _ in range(50):
        child_a, child_b = evolution.sbx_crossover(parent_a, parent_b, eta=15.0, rng=rng)
        for child in (child_a, child_b):
            assert all(0.0 <= g <= 1.0 for g in child.genes)


def test_sbx_교차는_결정론적이다() -> None:
    parent_a = evolution.Individual(analysts=("a", "b"), genes=(0.2, 0.8))
    parent_b = evolution.Individual(analysts=("a", "b"), genes=(0.7, 0.3))

    rng1 = np.random.default_rng(42)
    rng2 = np.random.default_rng(42)
    result1 = evolution.sbx_crossover(parent_a, parent_b, eta=15.0, rng=rng1)
    result2 = evolution.sbx_crossover(parent_a, parent_b, eta=15.0, rng=rng2)
    assert result1 == result2


def test_gaussian_변이는_rate_0이면_아무것도_안_바꾼다() -> None:
    rng = np.random.default_rng(3)
    individual = evolution.Individual(analysts=("a", "b"), genes=(0.3, 0.6))
    mutated = evolution.gaussian_mutate(individual, sigma=0.5, rate=0.0, rng=rng)
    assert mutated.genes == individual.genes


def test_gaussian_변이는_박스_밖으로_안_나간다() -> None:
    rng = np.random.default_rng(4)
    individual = evolution.Individual(analysts=("a",), genes=(0.01,))
    for _ in range(50):
        mutated = evolution.gaussian_mutate(individual, sigma=5.0, rate=1.0, rng=rng)
        assert 0.0 <= mutated.genes[0] <= 1.0


def test_다음세대는_엘리트를_그대로_보존한다() -> None:
    rng = np.random.default_rng(5)
    population = evolution.initial_population(("a", "b"), 10, rng)
    fitnesses = [float(i) for i in range(10)]  # 마지막(9번)이 최고
    children = evolution.next_generation(
        population, fitnesses,
        tournament_k=3, crossover_rate=0.9, sbx_eta=15.0,
        mutation_sigma=0.05, mutation_rate=0.2, elitism=2, rng=rng,
    )
    assert len(children) == len(population)
    # 상위 2개(엘리트)는 변형 없이 살아남는다.
    assert population[9] in children
    assert population[8] in children


def test_evolve는_같은_시드면_같은_결과다() -> None:
    """결정론 — 불변식 2 의 정신을 GA 쪽에도 적용한다."""

    def fake_evaluate(individual: evolution.Individual, generation: int) -> evolution.FitnessResult:
        # 목표: 가중치가 [1, 0] 에 가까울수록 높은 적합도. 실제 백테스트가 아니라
        # 연산자 결정론만 검증하는 자리라 여기선 허용된다.
        weights = individual.normalized()
        fitness = weights.get("a", 0.0) - weights.get("b", 0.0)
        return evolution.FitnessResult(
            individual=individual, fitness=fitness, ir_median=fitness,
            turnover_median=0.0, l1_term=0.0,
        )

    result1 = evolution.evolve(
        analysts=("a", "b"), population_size=8, generations=5,
        evaluate=fake_evaluate, seed=7,
    )
    result2 = evolution.evolve(
        analysts=("a", "b"), population_size=8, generations=5,
        evaluate=fake_evaluate, seed=7,
    )
    assert [g.best_fitness for g in result1.history] == [g.best_fitness for g in result2.history]
    assert result1.population == result2.population


def test_evolve는_개선이_없으면_patience_뒤에_멈춘다() -> None:
    def flat_evaluate(individual: evolution.Individual, generation: int) -> evolution.FitnessResult:
        return evolution.FitnessResult(
            individual=individual, fitness=0.0, ir_median=0.0,
            turnover_median=0.0, l1_term=0.0,
        )

    result = evolution.evolve(
        analysts=("a",), population_size=6, generations=40,
        evaluate=flat_evaluate, seed=1, patience=3,
    )
    assert result.stopped_early
    assert result.generations_run == 4  # 0세대 + patience 3


def test_유전자가_없으면_거부한다() -> None:
    with pytest.raises(ValueError, match="Analyst"):
        evolution.evolve(
            analysts=(), population_size=4, generations=1,
            evaluate=lambda ind, gen: None,  # type: ignore[arg-type,return-value]
        )


# ---------------------------------------------------------------------------
# 안정성 검사
# ---------------------------------------------------------------------------


def test_안정성_검사_같은_배합이면_안정이다() -> None:
    top = [
        evolution.Individual(analysts=("a", "b", "c"), genes=(0.5, 0.3, 0.2)),
        evolution.Individual(analysts=("a", "b", "c"), genes=(0.52, 0.28, 0.2)),
        evolution.Individual(analysts=("a", "b", "c"), genes=(0.48, 0.31, 0.21)),
    ]
    report = evolution.stability_report(top)
    assert report.stable
    assert "채택" in report.verdict


def test_안정성_검사_제각각이면_불안정이고_동일가중을_권한다() -> None:
    top = [
        evolution.Individual(analysts=("a", "b", "c"), genes=(1.0, 0.0, 0.0)),
        evolution.Individual(analysts=("a", "b", "c"), genes=(0.0, 1.0, 0.0)),
        evolution.Individual(analysts=("a", "b", "c"), genes=(0.0, 0.0, 1.0)),
    ]
    report = evolution.stability_report(top)
    assert not report.stable
    assert "동일가중" in report.verdict


def test_evolve_결과의_adopt는_안정성을_따른다() -> None:
    """적합도 지형이 완전히 평평하면(전부 같은 점수) 상위 10개가 서로 다른
    방향으로 흩어질 수 있고, 그때 채택 불가로 나와야 한다 — 노이즈를 진화
    결과라고 우기지 않는다."""

    def flat_evaluate(individual: evolution.Individual, generation: int) -> evolution.FitnessResult:
        return evolution.FitnessResult(
            individual=individual, fitness=0.0, ir_median=0.0,
            turnover_median=0.0, l1_term=0.0,
        )

    result = evolution.evolve(
        analysts=("a", "b", "c"), population_size=20, generations=1,
        evaluate=flat_evaluate, seed=2,
    )
    assert result.adopt is result.stability.stable
    assert result.adopt is False  # 무작위 초기 population, 전부 동점 → 제각각


# ---------------------------------------------------------------------------
# 통합 — 실제 백테스트로 적합도를 잰다 (selector.md §3)
# ---------------------------------------------------------------------------

FOLD_START = date(2026, 8, 3)
FOLD_END = date(2026, 8, 6)


def _moment(day: date) -> datetime:
    from quant_rl_trading.backtest.loop import DEFAULT_SNAPSHOT_TIME

    return datetime.combine(day, DEFAULT_SNAPSHOT_TIME, tzinfo=SEOUL)


@pytest.fixture
def warehouse(store):  # type: ignore[no-untyped-def]
    """3종목 · 400세션. tests/backtest/test_loop.py 의 최소 구성을 그대로 쓴다."""
    store.seed_config_defaults()
    entities = ["KR:000100", "KR:000200", "KR:000300"]
    history = [FOLD_START - timedelta(days=offset) for offset in range(400, -1, -1)]

    store.append(
        "fx",
        [
            {
                "entity_id": "FX:USDKRW", "valid_from": _moment(day),
                "observed_at": _moment(day), "source": "test", "rate": 1_350.0,
            }
            for day in [FOLD_START - timedelta(days=offset) for offset in range(400, -10, -1)]
        ],
        ingest_run_id="fx-seed",
    )

    universe_rows = []
    price_rows = []
    for index, day in enumerate(history + trading_days(Market.KR, FOLD_START, FOLD_END)):
        moment = _moment(day)
        for offset, entity in enumerate(entities):
            universe_rows.append({
                "entity_id": entity, "valid_from": moment, "observed_at": moment,
                "source": "test", "market": "KR", "name": entity,
                "is_listed": True, "is_tradable": True, "delisted_on": None,
            })
            close = 10_000.0 + index * (3 + offset) + offset * 500
            price_rows.append({
                "entity_id": entity, "valid_from": moment, "observed_at": moment,
                "source": "test", "market": "KR",
                "open": close, "high": close, "low": close, "close": close,
                "volume": 500_000.0, "value": close * 500_000.0, "adj_factor": None,
            })
    store.append("universe", universe_rows, ingest_run_id="u-seed")
    store.append("prices", price_rows, ingest_run_id="p-seed")

    past = trading_days(Market.KR, FOLD_START - timedelta(days=140), FOLD_START)
    store.append(
        "signals",
        [
            {
                "entity_id": entity, "valid_from": _moment(day),
                "observed_at": _moment(day), "source": "test", "analyst": "risk",
                "analyst_version": "risk-v0.1.0",
                "score": 0.2 + 0.3 * offset, "confidence": 1.0, "horizon_days": 5,
                "features_hash": "x", "evidence_json": "[]", "latency_ms": 1.0,
            }
            for day in past
            if day < FOLD_START
            for offset, entity in enumerate(entities)
        ],
        ingest_run_id="sig-seed",
    )
    return store


def test_backtest_fitness는_실제_백테스트로_채점한다(warehouse) -> None:
    individual = evolution.Individual(analysts=("risk",), genes=(0.8,))
    weight_as_of = _moment(FOLD_START - timedelta(days=30))

    result = evolution.backtest_fitness(
        warehouse, individual,
        market="KR", fold_starts=[FOLD_START], fold_trading_days=2,
        generation=0, weight_as_of=weight_as_of, capital=100_000_000.0,
    )

    assert result.fitness > float("-inf")
    assert result.per_fold_ir  # 폴드 하나라도 성적이 나왔다
    # 진화가 심은 가중치가 실제로 창고에 남는다 — 다음 개체가 읽을 수 있어야
    # 워크포워드가 성립한다.
    weights = warehouse.get("analyst_weights", as_of=_moment(FOLD_START), lookback=60)
    assert not weights.empty


def test_evolve는_작은_규모에서_실제_백테스트_위를_완주한다(warehouse) -> None:
    """population 4 × generations 2. **64×40 은 이 기계에서 절대 안 돌린다** —
    팀장 지시. 이건 배선이 맞는지만 증명한다.
    """
    call_count = 0

    def evaluate(individual: evolution.Individual, generation: int) -> evolution.FitnessResult:
        nonlocal call_count
        call_count += 1
        weight_as_of = _moment(FOLD_START - timedelta(days=30))
        return evolution.backtest_fitness(
            warehouse, individual,
            market="KR", fold_starts=[FOLD_START], fold_trading_days=2,
            generation=generation, weight_as_of=weight_as_of, capital=100_000_000.0,
            l1_penalty=0.01, turnover_penalty=0.05,
        )

    result = evolution.evolve(
        analysts=("risk",), population_size=4, generations=2,
        evaluate=evaluate, seed=0, patience=10,
    )

    assert result.generations_run == 2
    assert call_count == 4 * 2
    assert len(result.fitnesses) == 4
    assert result.stability.top_n >= 1
