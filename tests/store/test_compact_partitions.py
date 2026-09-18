"""파티션 압축 — **행은 그대로, 파일 수만 준다.**

조회 비용이 행이 아니라 파일 수로 붙어서(indices 9MB 를 파일 2,000개로 들고 있다) 합치는
것인데, 합치다가 한 행이라도 달라지면 창고가 조용히 틀린다. 여기서 고정하는 것은 **압축
전후의 `store.get` 결과가 같다**는 것과, **검증에 실패하면 원본을 안 지운다**는 것이다.
"""

from __future__ import annotations

from datetime import UTC, datetime

import duckdb
import pytest

from quant_rl_trading.store import paths
from quant_rl_trading.store.compaction import MERGED_PREFIX, compact_partition

NOW = datetime(2026, 9, 10, 6, 0, tzinfo=UTC)


def _row(entity: str, close: float, *, revision: int = 0) -> dict:
    return {
        "entity_id": entity, "valid_from": NOW, "observed_at": NOW,
        "source": "test", "market": "KR", "revision": revision,
        "open": close, "high": close, "low": close, "close": close,
        "volume": 1.0, "value": 1.0, "board": "KOSPI",
    }


@pytest.fixture
def partitioned(store):
    """같은 파티션에 파일 셋. 적재마다 파일 하나가 생기는 것이 창고의 성질이다."""
    store.append("indices", [_row("KR:IDX:A", 100.0)], ingest_run_id="run-1")
    store.append("indices", [_row("KR:IDX:B", 200.0)], ingest_run_id="run-2")
    # 정정본 — 압축이 revision 선택을 망치지 않는지 본다.
    store.append("indices", [_row("KR:IDX:A", 111.0, revision=1)], ingest_run_id="run-3")
    directory = next(paths.curated_dir(store.root, "indices").glob("observed_date=*"))
    assert len(list(directory.glob("*.parquet"))) == 3
    return store, directory


def _snapshot(store):
    frame = store.get("indices", as_of=NOW, lookback=30)
    return frame.sort_values(["entity_id", "valid_from"]).to_csv(index=False)


def test_압축해도_조회_결과가_같다(partitioned) -> None:
    store, directory = partitioned
    before = _snapshot(store)

    ok, reason = compact_partition(duckdb.connect(), directory, store.root / "_tmp")

    assert ok, reason
    paths.forget_listings()
    assert len(list(directory.glob("*.parquet"))) == 1
    assert _snapshot(store) == before, "행이 하나라도 달라지면 창고가 조용히 틀린다"


def test_정정본_선택이_살아남는다(partitioned) -> None:
    """합치면 같은 자연키가 한 파일에 모인다 — 최신 revision 을 고르는 규칙이 그대로여야 한다."""
    store, directory = partitioned

    compact_partition(duckdb.connect(), directory, store.root / "_tmp")

    paths.forget_listings()
    frame = store.get("indices", as_of=NOW, lookback=30)
    a = frame[frame["entity_id"] == "KR:IDX:A"]
    assert len(a) == 1 and float(a.iloc[0]["close"]) == 111.0


def test_적재_이력은_손대지_않는다(partitioned) -> None:
    """매니페스트가 사라지면 이미 돈 적재가 '안 돈 것' 이 되어 다시 돈다."""
    store, directory = partitioned
    before = {p.name for p in (store.root / paths.CURATED / "_manifests" / "indices").iterdir()}

    compact_partition(duckdb.connect(), directory, store.root / "_tmp")

    after = {p.name for p in (store.root / paths.CURATED / "_manifests" / "indices").iterdir()}
    assert after == before
    assert all(store.ingest_run_recorded("indices", r) for r in ("run-1", "run-2", "run-3"))


def test_파일이_하나면_건드리지_않는다(store) -> None:
    store.append("indices", [_row("KR:IDX:A", 100.0)], ingest_run_id="only")
    directory = next(paths.curated_dir(store.root, "indices").glob("observed_date=*"))

    ok, reason = compact_partition(duckdb.connect(), directory, store.root / "_tmp")

    assert not ok and "하나" in reason


def test_합친_파일은_알아볼_수_있는_이름이다(partitioned) -> None:
    """나중에 사람이 창고를 뒤질 때 이 파일이 적재본이 아니라 합친 것임을 알아야 한다."""
    store, directory = partitioned

    compact_partition(duckdb.connect(), directory, store.root / "_tmp")

    assert next(directory.glob("*.parquet")).name.startswith(MERGED_PREFIX)
