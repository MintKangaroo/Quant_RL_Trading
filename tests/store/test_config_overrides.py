"""샌드박스 설정 덮어쓰기 — Z2 트랙(docs/design/portfolio-construction.md). 실전 창고에서는 거부, 샌드박스에서는 모든 읽기 길에서 이긴다."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from quant_rl_trading.store import OVERRIDES_FILE, Store, StoreError
from quant_rl_trading.store.memo import MemoStore

NOW = datetime(2026, 9, 23, 7, 0, tzinfo=UTC)


def test_샌드박스_덮어쓰기가_창고_값을_이긴다(store: Store) -> None:
    store.seed_config_defaults()
    base = store.config("allocator.baseline", as_of=NOW)
    (Path(store.root) / OVERRIDES_FILE).write_text("allocator.baseline: float_cap\nuniverse.index_members_kr: KOSPI200\n")
    assert base != "float_cap"
    assert store.config("allocator.baseline", as_of=NOW) == "float_cap"
    assert store.config("universe.index_members_kr", as_of=NOW) == "KOSPI200"
    assert MemoStore(store).config("allocator.baseline", as_of=NOW) == "float_cap"  # 캐시 래퍼도 같은 값


def test_섹션으로_읽어도_덮어쓴_값이다(store: Store) -> None:
    store.seed_config_defaults()
    (Path(store.root) / OVERRIDES_FILE).write_text("allocator.baseline: float_cap\n")
    assert store.config("allocator", as_of=NOW)["baseline"] == "float_cap"


def test_파일이_없으면_아무것도_안_바뀐다(store: Store) -> None:
    store.seed_config_defaults()
    assert store.config("allocator.baseline", as_of=NOW) != "float_cap"


def test_실전_창고에서는_거부한다(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import quant_rl_trading.store as store_module

    monkeypatch.setattr(store_module, "DEFAULT_ROOT", tmp_path / "data")
    real = Store(root=tmp_path / "data")
    real.seed_config_defaults()
    (tmp_path / "data" / OVERRIDES_FILE).write_text("allocator.baseline: float_cap\n")
    with pytest.raises(StoreError):
        real.config("allocator.baseline", as_of=NOW)
