"""Quant_RL_Trading — 멀티에이전트 AI 사모펀드.

Analysts score, the Selector nominates, the Allocator sizes, the Executor acts.

불변식은 CLAUDE.md 에 있다. 특히 이 패키지 안에서는:
- 모든 데이터 접근은 ``lattice.store.get(table, as_of=...)`` 을 경유한다.
- 시간은 ``lattice.replay`` 의 Clock 주입으로만 얻는다.
"""
