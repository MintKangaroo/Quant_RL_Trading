from __future__ import annotations

import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# tools/ 는 배포 패키지가 아니라 레포 도구다. import 경로만 열어준다.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def store(tmp_path: Path):  # type: ignore[no-untyped-def]
    """빈 임시 창고.

    데이터 계약 테스트는 전부 진짜 Parquet 을 깔고 진짜 store.get() 을 부른다.
    목으로 통과하는 계약 테스트는 계약을 검증하지 않는다.
    """
    from quant_rl_trading.store import Store

    return Store(root=tmp_path / "data")


@pytest.fixture
def ts() -> Callable[..., datetime]:
    """UTC 타임스탬프 헬퍼. 테스트에서 naive 시각을 실수로 만들지 않게 한다."""

    def build(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
        return datetime(year, month, day, hour, minute, tzinfo=UTC)

    return build
