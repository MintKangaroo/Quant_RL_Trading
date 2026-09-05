"""`slow` 표시가 붙은 시험은 기본 실행에서 건너뛴다.

`@pytest.mark.slow` 를 붙이는 것만으로는 아무것도 안 걸러진다 — pytest 는
표시된 시험도 그냥 돈다. 그러면 오라클 카나리의 200k 판본(8분)이 일상적인
`pytest tests/` 마다 붙어서, 결국 누군가 그 파일을 통째로 지우게 된다.

전역 `addopts` 에 `-m "not slow"` 를 넣지 않은 이유는, 이 저장소에서 여러
작업이 동시에 테스트를 돌리기 때문이다. 전역 설정을 바꾸면 앞으로 붙는 모든
`slow` 표시가 **아무도 모르게** 기본 실행에서 빠진다.

돌리는 법:

    uv run pytest tests/rl -m slow
"""

from __future__ import annotations

import pytest


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if "slow" in str(config.getoption("-m") or ""):
        return
    skip = pytest.mark.skip(reason="느린 시험. `-m slow` 로 따로 돌린다")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip)
