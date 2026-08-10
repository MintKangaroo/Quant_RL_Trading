"""게이트 시그니처 — as_of 는 키워드 필수다.

위치인자로 두면 언젠가 빠뜨린 호출이 생기고, 그 호출은 예외 없이 조용히
미래를 본다. 불변식 1과 9(모든 조회·API 는 as_of 를 받는다)가 여기 걸린다.
"""

from __future__ import annotations

import inspect
from datetime import datetime

import pytest

from lattice import store
from lattice.store.errors import NaiveTimestamp

pytestmark = pytest.mark.invariant

GATES = (store.get, store.Store.get, store.config, store.Store.config)


@pytest.mark.parametrize("gate", GATES, ids=lambda f: f.__qualname__)
def test_as_of_is_keyword_only(gate) -> None:  # type: ignore[no-untyped-def]
    parameter = inspect.signature(gate).parameters["as_of"]

    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is inspect.Parameter.empty, "as_of 에 기본값이 있으면 안 된다"


def test_positional_as_of_is_a_type_error(store, ts) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(TypeError):
        store.get("prices", ts(2024, 3, 4))  # type: ignore[misc]


def test_naive_as_of_is_rejected(store) -> None:  # type: ignore[no-untyped-def]
    """타임존 없는 as_of 는 어느 시장의 자정인지 모른다. 추측하지 않고 거부한다."""
    with pytest.raises(NaiveTimestamp):
        store.get("prices", as_of=datetime(2024, 3, 4))


def test_public_surface_is_small() -> None:
    """게이트가 넓어지면 우회로가 생긴다. 공개 이름을 의도적으로 좁게 유지한다."""
    public = {name for name in store.__all__ if not name.endswith("Error")}

    assert {"get", "append", "config", "tables"} <= public
    assert "reader" not in store.__all__
    assert "writer" not in store.__all__
