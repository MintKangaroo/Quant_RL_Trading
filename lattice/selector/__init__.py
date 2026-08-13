"""Selector — Analyst 점수를 합치고 후보를 고른다.

**RL 이 실패해도 이 층이 작동하면 시스템은 돈을 번다** (selector.md 서두).
M3 의 룰 베이스라인이 곧 이것이다.

    combine.py     Σ(wᵢ·scoreᵢ·confᵢ) / Σ(wᵢ·confᵢ)
    filters.py     살 수 있는 종목만 남긴다
    candidates.py  6단계 선정 파이프라인
    weights.py     IC 측정 결과에서 온 Analyst 가중치
    pipeline.py    창고에 붙여 한 번에 돌린다

가중치 진화(selector.md §4)는 아직이다. 먼저 고정 가중치로 도는 것을 만든다 —
진화는 작동하는 파이프라인 위에서만 의미가 있다.
"""

from lattice.selector.candidates import (
    Candidate,
    SelectionParams,
    SelectionTrace,
    correlation_matrix,
    rejected_entities,
    select,
)
from lattice.selector.combine import Contribution, combined_scores, contributions
from lattice.selector.filters import FilterParams, FilterResult, distressed, tradable_universe
from lattice.selector.weights import analyst_weights

__all__ = [
    "Candidate",
    "Contribution",
    "FilterParams",
    "FilterResult",
    "SelectionParams",
    "SelectionTrace",
    "analyst_weights",
    "combined_scores",
    "contributions",
    "correlation_matrix",
    "distressed",
    "rejected_entities",
    "select",
    "tradable_universe",
]
