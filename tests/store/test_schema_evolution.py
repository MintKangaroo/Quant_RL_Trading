"""스키마에 컬럼을 추가해도 그 전에 쓰인 데이터가 계속 읽혀야 한다.

**사고를 먼저 재현한다.** `nav_daily` 에 `benchmark_note` 를 추가한 순간
그 전에 쓰인 창고(`data/_backtest`·`data/_shadow`)가 통째로 안 읽혔다:

    BinderException: Referenced column "benchmark_note" not found in FROM clause!

`union_by_name` 은 **주어진 파일들 사이**를 맞출 뿐, 아무 파일에도 없는 컬럼을
만들어 주지 않는다. 그래서 새 컬럼이 아직 한 파일에도 없는 창을 조회할 때만
터진다 — 빈 창고에서는 멀쩡하고 **오래된 구간을 볼 때만** 죽는다. 가장 늦게,
가장 나쁜 때 드러나는 모양이라 회귀 테스트가 필요하다.

append-only 창고에서 컬럼 추가는 정상적인 진화다. 옛 행에 그 값이 없었다는
것은 **사실**이고 그 사실은 NULL 이다. 예외가 아니다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pytest

from quant_rl_trading.store import Store
from quant_rl_trading.store.schema import TableSpec
from quant_rl_trading.store.tables import _SPECS

AS_OF = datetime(2026, 8, 20, tzinfo=UTC)
SESSION = datetime(2026, 8, 14, tzinfo=UTC)


@pytest.fixture
def widened(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """`prices` 에 컬럼을 하나 더 붙인 스펙. 창고를 쓴 **뒤에** 적용한다."""

    def widen() -> None:
        old = _SPECS["prices"]
        monkeypatch.setitem(
            _SPECS,
            "prices",
            TableSpec(
                name=old.name,
                columns={**old.columns, "새컬럼": pa.string()},
                natural_key=old.natural_key,
                observation_lag_days=old.observation_lag_days,
                doc=old.doc,
            ),
        )

    return widen


def _seed(store: Store) -> None:
    store.append(
        "prices",
        [
            {
                "entity_id": "KR:000890",
                "valid_from": SESSION,
                "observed_at": SESSION,
                "source": "test",
                "market": "KR",
                "open": 1000.0,
                "high": 1010.0,
                "low": 990.0,
                "close": 1005.0,
                "volume": 100.0,
                "value": 100_500.0,
                "adj_factor": 1.0,
            }
        ],
        ingest_run_id="prices-old-schema",
        source="test",
    )


def test_컬럼을_추가해도_그_전_데이터가_읽힌다(tmp_path: Path, widened) -> None:  # type: ignore[no-untyped-def]
    store = Store(root=tmp_path / "data")
    _seed(store)  # 옛 스키마로 쓴다
    widened()  # 그 다음 컬럼이 생긴다 — 실제 순서가 이렇다

    frame = store.get("prices", as_of=AS_OF, lookback=30)

    assert len(frame) == 1, "옛 파일이 안 읽혔다 — 컬럼 추가가 과거를 깨뜨렸다"
    assert "새컬럼" in frame.columns
    assert frame["새컬럼"].isna().all(), "없던 값은 NULL 이어야 한다"
    # 기존 컬럼은 멀쩡해야 한다. NULL 세우기가 다른 컬럼을 건드리면 안 된다.
    assert float(frame.iloc[0]["close"]) == 1005.0


def test_새_컬럼만_골라_읽어도_터지지_않는다(tmp_path: Path, widened) -> None:  # type: ignore[no-untyped-def]
    """호출부가 그 컬럼만 달라고 해도 마찬가지다."""
    store = Store(root=tmp_path / "data")
    _seed(store)
    widened()

    frame = store.get("prices", as_of=AS_OF, lookback=30, columns=["새컬럼"])

    assert len(frame) == 1
    assert frame["새컬럼"].isna().all()


def test_latest_by_entity_도_같다(tmp_path: Path, widened) -> None:  # type: ignore[no-untyped-def]
    """접기 조회는 별도 SQL 이다 — 한쪽만 고치면 다른 쪽이 조용히 남는다."""
    store = Store(root=tmp_path / "data")
    _seed(store)
    widened()

    frame = store.latest_by_entity("prices", as_of=AS_OF, lookback=30)

    assert len(frame) == 1
    assert frame["새컬럼"].isna().all()
