"""가중치 진화 — GA 연산자는 순수 함수로, 적합도는 진짜 백테스트로 검증한다.

목으로 적합도를 흉내 내는 진화 테스트는 진화를 검증하지 않는다(selector.md
§3) — 그래서 통합 테스트 한 개는 ``backtest_fitness`` 로 실제 창고 위를 돈다.
나머지는 연산자 자체(선택·교차·변이·안정성 검사)의 계약을 목적으로 한다.
"""

from __future__ import annotations

import json
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
                "observed_at": _moment(day), "source": "test", "analyst": "fundamental",
                "analyst_version": "fundamental-v0.1.0",
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


# ---------------------------------------------------------------------------
# 다양성 — 세대마다 기록한다
# ---------------------------------------------------------------------------


def test_같은_배합만_모이면_다양성이_0이다() -> None:
    # 유전자는 다르지만 정규화하면 같은 배합 — 합성 공식이 스케일 불변이라
    # 표현형은 하나다.
    population = [
        evolution.Individual(analysts=("a", "b"), genes=(0.2, 0.4)),
        evolution.Individual(analysts=("a", "b"), genes=(0.4, 0.8)),
        evolution.Individual(analysts=("a", "b"), genes=(0.1, 0.2)),
    ]
    assert evolution.mean_pairwise_distance(population) == pytest.approx(0.0)
    # 표현형은 붕괴했어도 유전형은 아직 퍼져 있다 — 둘을 따로 재는 이유다.
    assert evolution.gene_spread(population) > 0.0


def test_서로_다른_방향이면_다양성이_크다() -> None:
    population = [
        evolution.Individual(analysts=("a", "b"), genes=(1.0, 0.0)),
        evolution.Individual(analysts=("a", "b"), genes=(0.0, 1.0)),
    ]
    # one-hot 두 개는 L1 거리 최대 2 다.
    assert evolution.mean_pairwise_distance(population) == pytest.approx(2.0)


def test_세대기록이_다양성과_실패개수를_담는다() -> None:
    def evaluate(individual: evolution.Individual, generation: int) -> evolution.FitnessResult:
        # 첫 개체만 -inf — "폴드가 전부 결과를 못 냈다" 를 흉내 낸다.
        fitness = float("-inf") if individual.genes[0] < 0.05 else individual.genes[0]
        return evolution.FitnessResult(
            individual=individual, fitness=fitness, ir_median=0.0,
            turnover_median=0.0, l1_term=0.0,
        )

    result = evolution.evolve(
        analysts=("a", "b", "c"), population_size=12, generations=3,
        evaluate=evaluate, seed=11,
    )
    for record in result.history:
        assert record.diversity > 0.0
        assert record.gene_spread > 0.0
        # 평균·표준편차는 -inf 를 빼고 낸다 — 안 그러면 통계가 통째로 -inf 다.
        assert record.std_fitness == record.std_fitness  # NaN 아님
        assert record.worst_fitness > float("-inf") or record.failed == 12


# ---------------------------------------------------------------------------
# 체크포인트 — 중단이 기본값이라 보고 매 세대 남긴다
# ---------------------------------------------------------------------------


def _rising_evaluate(
    individual: evolution.Individual, generation: int
) -> evolution.FitnessResult:
    fitness = individual.normalized().get("a", 0.0)
    return evolution.FitnessResult(
        individual=individual, fitness=fitness, ir_median=fitness,
        turnover_median=0.0, l1_term=0.0,
    )


def test_체크포인트가_세대마다_한_줄씩_남는다(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "nested" / "run.jsonl"
    result = evolution.evolve(
        analysts=("a", "b"), population_size=6, generations=4,
        evaluate=_rising_evaluate, seed=3,
        checkpoint=evolution.JsonlCheckpoint(path),
    )
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == result.generations_run == 4
    records = [json.loads(line) for line in lines]
    assert [r["generation"] for r in records] == [0, 1, 2, 3]
    assert records[0]["best_weights"].keys() == {"a", "b"}
    assert "diversity" in records[0] and "gene_spread" in records[0]


def test_진화가_도중에_죽어도_거기까지가_남는다(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """이 테스트가 이 기능의 존재 이유다.

    2026-08-15 새벽, 16×15 진화가 3시간을 돌다 중단됐는데 세대 기록이 한 줄도
    안 남았다 — ``evolve`` 가 ``history`` 를 다 모은 뒤에야 호출부가 찍는
    구조였기 때문이다. 3세대까지 간 것이 통째로 사라졌다.
    """
    path = tmp_path / "crash.jsonl"

    def dies_in_generation_2(
        individual: evolution.Individual, generation: int
    ) -> evolution.FitnessResult:
        if generation == 2:
            raise MemoryError("램이 모자라 죽었다고 치자")
        return _rising_evaluate(individual, generation)

    with pytest.raises(MemoryError):
        evolution.evolve(
            analysts=("a", "b"), population_size=5, generations=10,
            evaluate=dies_in_generation_2, seed=4,
            checkpoint=evolution.JsonlCheckpoint(path),
        )

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2  # 0세대 · 1세대 는 살아남았다
    assert [json.loads(line)["generation"] for line in lines] == [0, 1]


def test_체크포인트는_기존_기록을_덮어쓰지_않는다(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "append.jsonl"
    for _ in range(2):
        evolution.evolve(
            analysts=("a", "b"), population_size=4, generations=2,
            evaluate=_rising_evaluate, seed=5,
            checkpoint=evolution.JsonlCheckpoint(path),
        )
    assert len(path.read_text(encoding="utf-8").strip().splitlines()) == 4


def test_체크포인트_주기를_늘리면_그만큼만_남는다(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "every2.jsonl"
    evolution.evolve(
        analysts=("a", "b"), population_size=4, generations=5,
        evaluate=_rising_evaluate, seed=6,
        checkpoint=evolution.JsonlCheckpoint(path, every=2),
    )
    generations = [
        json.loads(line)["generation"]
        for line in path.read_text(encoding="utf-8").strip().splitlines()
    ]
    assert generations == [0, 2, 4]


def test_체크포인트_주기가_0이면_거부한다(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError, match="1 이상"):
        evolution.JsonlCheckpoint(tmp_path / "x.jsonl", every=0)


# ---------------------------------------------------------------------------
# 홀드아웃 — selector.md §4 "최종 검증은 별도 테스트 폴드에서 딱 한 번"
# ---------------------------------------------------------------------------


def _result(individual: evolution.Individual, ir: float) -> evolution.FitnessResult:
    return evolution.FitnessResult(
        individual=individual, fitness=ir, ir_median=ir, turnover_median=0.0, l1_term=0.0,
    )


def test_동일가중_개체는_모든_가중치가_같다() -> None:
    uniform = evolution.uniform_individual(("a", "b", "c", "d"))
    assert list(uniform.normalized().values()) == pytest.approx([0.25] * 4)


def test_홀드아웃은_동일가중을_같은_폴드에서_같이_잰다() -> None:
    best = evolution.Individual(analysts=("a", "b"), genes=(0.9, 0.1))
    seen: list[evolution.Individual] = []

    def evaluate(individual: evolution.Individual) -> evolution.FitnessResult:
        seen.append(individual)
        # 최고 개체가 동일가중보다 낫다.
        return _result(individual, 0.5 if individual == best else 0.2)

    report = evolution.holdout_report(
        best, analysts=("a", "b"), folds=[date(2026, 5, 4)],
        train_fitness=0.9, evaluate=evaluate,
    )
    assert len(seen) == 2  # 최고 개체 + 동일가중
    assert seen[1].normalized() == {"a": 0.5, "b": 0.5}
    assert report.beats_uniform
    assert report.edge == pytest.approx(0.3)
    # 학습 0.9 → 홀드아웃 0.5. 그 낙차가 일반화 격차다.
    assert report.generalization_gap == pytest.approx(0.4)


def test_홀드아웃에서_동일가중에_지면_동일가중을_권한다() -> None:
    best = evolution.Individual(analysts=("a", "b"), genes=(0.9, 0.1))

    def evaluate(individual: evolution.Individual) -> evolution.FitnessResult:
        return _result(individual, 0.1 if individual == best else 0.4)

    report = evolution.holdout_report(
        best, analysts=("a", "b"), folds=[date(2026, 5, 4)],
        train_fitness=0.9, evaluate=evaluate,
    )
    assert not report.beats_uniform
    assert "동일가중" in report.verdict


def test_홀드아웃_폴드가_없으면_거부한다() -> None:
    with pytest.raises(ValueError, match="홀드아웃 폴드가 없다"):
        evolution.holdout_report(
            evolution.Individual(analysts=("a",), genes=(1.0,)),
            analysts=("a",), folds=[], train_fitness=0.0,
            evaluate=lambda ind: _result(ind, 0.0),
        )


def test_홀드아웃에서_성적을_못_내면_채택_근거가_없다고_말한다() -> None:
    best = evolution.Individual(analysts=("a",), genes=(1.0,))

    def evaluate(individual: evolution.Individual) -> evolution.FitnessResult:
        return evolution.FitnessResult(
            individual=individual, fitness=float("-inf"), ir_median=0.0,
            turnover_median=0.0, l1_term=0.0,
        )

    report = evolution.holdout_report(
        best, analysts=("a",), folds=[date(2026, 5, 4)],
        train_fitness=0.5, evaluate=evaluate,
    )
    assert "근거가 없다" in report.verdict


# ---------------------------------------------------------------------------
# 안정성 검사의 한계 — 이 상수가 거짓말을 막는다
# ---------------------------------------------------------------------------


def test_옛_판정은_신호가_없어도_통과했다() -> None:
    """고쳐진 것이 **무엇이었는지**를 남긴다.

    귀무분포를 끄면(``null_replicates=0``) 옛 판정이 그대로 재현된다 — 적합도가
    유전자와 무관한 난수인데도 pop 16 × gen 15 에서 과반이 "채택 가능" 으로
    나온다. 검사가 재던 것은 지형의 봉우리가 아니라 작은 개체군의 유전적
    드리프트였다.

    같은 조건에서 귀무분포를 켜면 떨어진다는 것은
    ``test_노이즈_지형은_거의_전부_불안정으로_떨어진다`` 가 지킨다. 두 테스트가
    쌍으로 있어야 "고쳤다" 가 주장이 아니라 측정이 된다.
    """
    old_style = sum(
        evolution.evolve(
            analysts=("a", "b", "c"), population_size=16, generations=15,
            evaluate=_noise_evaluate_factory(9_000 + seed), seed=seed,
            patience=999, null_replicates=0,
        ).adopt
        for seed in range(20)
    )
    assert old_style >= 12, (
        f"옛 방식 거짓 양성이 {old_style}/20 — 실측(18/20) 과 크게 다르면 "
        "NOISE_FLOOR_DISTANCE 의 근거를 다시 재라"
    )
    assert (16, 15) in evolution.NOISE_FLOOR_DISTANCE


# ---------------------------------------------------------------------------
# 드리프트 귀무분포 — 대조군이 곧 테스트다
# ---------------------------------------------------------------------------


def _noise_evaluate_factory(seed: int):  # type: ignore[no-untyped-def]
    """적합도가 **유전자와 완전히 무관한** 난수. 신호가 0인 지형."""
    rng = np.random.default_rng(seed)

    def evaluate(
        individual: evolution.Individual, generation: int
    ) -> evolution.FitnessResult:
        value = float(rng.normal(0.0, 1.0))
        return evolution.FitnessResult(
            individual=individual, fitness=value, ir_median=value,
            turnover_median=0.0, l1_term=0.0,
        )

    return evaluate


def _peak_evaluate(
    individual: evolution.Individual, generation: int
) -> evolution.FitnessResult:
    """진짜 봉우리가 있는 지형 — b 가 제일 크게 기여한다."""
    w = individual.normalized()
    value = 0.3 * w["a"] + 0.5 * w["b"] + 0.2 * w["c"]
    return evolution.FitnessResult(
        individual=individual, fitness=value, ir_median=value,
        turnover_median=0.0, l1_term=0.0,
    )


def test_귀무분포는_복제마다_다른_값을_준다() -> None:
    params = evolution.GAParams(n_analysts=3, population_size=8, generations=4)
    distances = evolution.drift_null_distances(params, replicates=6, seed=0)
    assert len(distances) == 6
    assert distances == sorted(distances)
    assert len(set(distances)) > 1  # 전부 같은 값이면 분포가 아니다


def test_귀무분포는_같은_시드면_같다() -> None:
    params = evolution.GAParams(n_analysts=3, population_size=8, generations=4)
    first = evolution.drift_null_distances(params, replicates=5, seed=3)
    second = evolution.drift_null_distances(params, replicates=5, seed=3)
    assert first == second


def test_귀무분포_복제가_0이면_거부한다() -> None:
    params = evolution.GAParams(n_analysts=3, population_size=6, generations=2)
    with pytest.raises(ValueError, match="1 이상"):
        evolution.drift_null_distances(params, replicates=0)


def test_노이즈_지형은_거의_전부_불안정으로_떨어진다() -> None:
    """**이 테스트가 안정성 검사의 존재 이유다.**

    적합도가 유전자와 무관한 난수인데도 옛 판정(절대 문턱 0.25 하나)은
    pop 16 × gen 15 에서 20번 중 18번 "안정" 을 냈다. 드리프트 귀무분포를
    끼운 뒤에는 정의상 ``NULL_ALPHA``(5%) 근처로 떨어져야 한다.

    합성 데이터로 통과하는 테스트가 현실을 안 말해준 전례가 이 저장소에 있다
    ([[constant-feature-eats-weight]]). 여기서는 그 교훈을 뒤집어 쓴다 —
    **노이즈를 넣으면 반드시 떨어진다** 를 못 박는다.
    """
    adopted = sum(
        evolution.evolve(
            analysts=("a", "b", "c"), population_size=16, generations=15,
            evaluate=_noise_evaluate_factory(50_000 + seed), seed=seed,
            patience=999, null_replicates=40,
        ).adopt
        for seed in range(20)
    )
    assert adopted <= 4, (
        f"노이즈 지형에서 {adopted}/20 이 채택됐다 — 검사가 다시 드리프트를 "
        "봉우리로 읽고 있다"
    )


def test_봉우리_지형은_채택된다() -> None:
    """거짓 양성만 잡고 진짜 신호까지 죽이면 검사가 아니라 거부기다."""
    adopted = sum(
        evolution.evolve(
            analysts=("a", "b", "c"), population_size=16, generations=15,
            evaluate=_peak_evaluate, seed=seed, patience=999, null_replicates=40,
        ).adopt
        for seed in range(10)
    )
    assert adopted >= 6, f"진짜 봉우리인데 {adopted}/10 만 채택됐다"


def test_귀무분포_없이_판정하면_믿지_말라고_적는다() -> None:
    """옛 방식(절대 문턱만)으로도 돌아가되, 리포트가 스스로 경고한다."""
    result = evolution.evolve(
        analysts=("a", "b", "c"), population_size=16, generations=15,
        evaluate=_noise_evaluate_factory(7), seed=0, patience=999,
        null_replicates=0,
    )
    assert result.stability.null_quantile is None
    if result.stability.stable:
        assert "믿으면 안 된다" in result.stability.verdict or (
            "재지 않았다" in result.stability.verdict
        )


def test_드리프트_수준으로_몰린_것은_봉우리가_아니라고_말한다() -> None:
    top = [
        evolution.Individual(analysts=("a", "b"), genes=(0.50, 0.50)),
        evolution.Individual(analysts=("a", "b"), genes=(0.51, 0.49)),
    ]
    # 관측 거리(0.02)가 귀무 5%분위보다 크게 만든다.
    report = evolution.stability_report(top, null_distances=[0.001, 0.002, 0.003, 0.9])
    assert not report.stable
    assert "드리프트" in report.verdict
    assert report.null_quantile is not None


def test_귀무분포보다_촘촘하면_봉우리_증거로_친다() -> None:
    top = [
        evolution.Individual(analysts=("a", "b"), genes=(0.500, 0.500)),
        evolution.Individual(analysts=("a", "b"), genes=(0.501, 0.499)),
    ]
    report = evolution.stability_report(top, null_distances=[0.3, 0.4, 0.5, 0.6])
    assert report.stable
    assert "홀드아웃" in report.verdict  # 최종 근거는 여전히 홀드아웃이다


# ---------------------------------------------------------------------------
# 폴드 겹침 — "다중 시작일" 이 사실은 한 개였다
# ---------------------------------------------------------------------------


def test_최소_간격을_주면_겹치는_폴드를_안_뽑는다() -> None:
    starts = [date(2026, 3, 2) + timedelta(days=offset) for offset in range(60)]
    rng = np.random.default_rng(0)
    for _ in range(50):
        picked = evolution.resample_folds(starts, 2, rng, min_gap_days=21)
        assert len(picked) == 2
        assert abs((picked[0] - picked[1]).days) >= 21


def test_간격을_안_주면_옛_동작_그대로다() -> None:
    starts = [date(2026, 3, 2) + timedelta(days=offset) for offset in range(10)]
    picked_a = evolution.resample_folds(starts, 3, np.random.default_rng(5))
    picked_b = evolution.resample_folds(starts, 3, np.random.default_rng(5))
    assert picked_a == picked_b
    assert len(picked_a) == 3


def test_간격을_못_채우면_지어내지_않고_적게_준다() -> None:
    # 후보가 다닥다닥 붙어 있어 21일 간격으로는 하나밖에 못 뽑는다.
    starts = [date(2026, 3, 2) + timedelta(days=offset) for offset in range(5)]
    picked = evolution.resample_folds(starts, 3, np.random.default_rng(0), min_gap_days=21)
    assert len(picked) == 1


def test_후보가_없으면_빈_목록이다() -> None:
    assert evolution.resample_folds([], 2, np.random.default_rng(0), min_gap_days=21) == []
