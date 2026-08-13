"""Allocator — 목표 비중을 정한다.

M3 에서는 **룰 베이스라인**이 이 자리에 있다 (`baseline.py`). RL 은 M4 이고,
작동하는 시스템 위에 얹는다 — RL 이 없으면 아무것도 안 되는 구조로 만들지
않는다 (CLAUDE.md).

M4 에서 RL 이 들어와도 이 파일은 남는다. **베이스라인이 RL 의 경쟁자이자
폴백이다** — 학습이 3회 실패하면 여기로 되돌린다 (milestones.md 중단 기준).
"""

from quant_rl_trading.allocator.baseline import AllocatorParams, Baseline, allocate

__all__ = ["AllocatorParams", "Baseline", "allocate"]
