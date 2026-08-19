"""Selector — Analyst 점수를 합치고 후보를 고른다.

**RL 이 실패해도 이 층이 작동하면 시스템은 돈을 번다** (selector.md 서두).
M3 의 룰 베이스라인이 곧 이것이다.

    combine.py     Σ(wᵢ·scoreᵢ·confᵢ) / Σ(wᵢ·confᵢ)
    constraints.py 알파가 아닌 Analyst — 점수를 안 매기고 꼬리를 자른다
    filters.py     살 수 있는 종목만 남긴다
    candidates.py  6단계 선정 파이프라인
    weights.py     IC 측정 결과에서 온 Analyst 가중치
    pipeline.py    창고에 붙여 한 번에 돌린다
    evolution.py   가중치 진화(selector.md §4) — GA + 실제 백테스트 적합도

진화는 작동하는 파이프라인 위에서만 의미가 있다. 룰 베이스라인이 먼저 돈
뒤에야 진화가 그 위에서 뭘 개선하는지 말할 수 있다.
"""

from quant_rl_trading.selector.candidates import (
    Candidate,
    SelectionParams,
    SelectionTrace,
    correlation_matrix,
    rejected_entities,
    select,
)
from quant_rl_trading.selector.combine import Contribution, combined_scores, contributions
from quant_rl_trading.selector.constraints import (
    CONSTRAINT_ANALYSTS,
    ConstraintParams,
    alpha_weights,
    apply_risk_floor,
    constraint_scores,
)
from quant_rl_trading.selector.evolution import (
    EvolutionResult,
    FitnessResult,
    GenerationRecord,
    HoldoutReport,
    Individual,
    JsonlCheckpoint,
    StabilityReport,
    backtest_fitness,
    evolve,
    gaussian_mutate,
    gene_spread,
    holdout_report,
    initial_population,
    mean_pairwise_distance,
    next_generation,
    resample_folds,
    sbx_crossover,
    stability_report,
    tournament_select,
    uniform_individual,
)
from quant_rl_trading.selector.filters import (
    FilterParams,
    FilterResult,
    distressed,
    tradable_universe,
)
from quant_rl_trading.selector.weights import analyst_weights, measured_weights

__all__ = [
    "CONSTRAINT_ANALYSTS",
    "Candidate",
    "ConstraintParams",
    "Contribution",
    "EvolutionResult",
    "FilterParams",
    "FilterResult",
    "FitnessResult",
    "GenerationRecord",
    "HoldoutReport",
    "Individual",
    "JsonlCheckpoint",
    "SelectionParams",
    "SelectionTrace",
    "StabilityReport",
    "alpha_weights",
    "analyst_weights",
    "apply_risk_floor",
    "backtest_fitness",
    "combined_scores",
    "constraint_scores",
    "contributions",
    "correlation_matrix",
    "distressed",
    "evolve",
    "gaussian_mutate",
    "gene_spread",
    "holdout_report",
    "initial_population",
    "mean_pairwise_distance",
    "measured_weights",
    "next_generation",
    "rejected_entities",
    "resample_folds",
    "sbx_crossover",
    "select",
    "stability_report",
    "tournament_select",
    "tradable_universe",
    "uniform_individual",
]
