"""데이터 게이트 — Parquet + DuckDB 위의 이중시간 저장·조회.

레포에서 Parquet/DuckDB 를 직접 만질 수 있는 유일한 패키지다.
바깥에서는 아래 넷만 쓴다.

    store.get(table, as_of=..., entity=None, lookback=None)
    store.append(table, records, ingest_run_id=...)
    store.config(name, as_of=...)
    store.tables()

``get`` 은 내부에서 ``observed_at <= as_of`` 를 무조건 적용한다. as_of 는
키워드 필수다 — 위치인자로 두면 언젠가 빠뜨린 호출이 생기고, 그 호출은
조용히 미래를 본다.

명세: docs/design/data-contract.md
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from lattice.store import config as _config
from lattice.store import reader, writer
from lattice.store.errors import (
    ConfigNotFound,
    DuplicateIngestRun,
    MissingObservedAt,
    NaiveTimestamp,
    SchemaViolation,
    StoreError,
    UnknownTable,
)
from lattice.store.tables import table_names

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = REPO_ROOT / "data"
DEFAULT_CONFIG_FILE = REPO_ROOT / "config" / "defaults.toml"
ROOT_ENV = "LATTICE_DATA_ROOT"


class Store:
    """하나의 데이터 루트에 대한 게이트.

    DuckDB 는 단일 라이터다. 쓰기는 워커만 하고, 대시보드는 이 클래스를 통해
    읽기만 한다. 연결은 인메모리이며 Parquet 을 읽기만 할 뿐 어떤 파일도
    이 연결로 쓰지 않는다.
    """

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root is not None else _default_root()
        self._connection: duckdb.DuckDBPyConnection | None = None

    # -- 조회 -----------------------------------------------------------------

    def get(
        self,
        table: str,
        *,
        as_of: datetime,
        entity: str | Sequence[str] | None = None,
        lookback: timedelta | int | None = None,
    ) -> pd.DataFrame:
        """``as_of`` 시점에 알 수 있었던 것만 돌려준다.

        ``lookback`` 은 ``valid_from`` 기준 창이다(정수면 일). 관측 기준이
        아니다 — 둘을 같다고 보면 뒤늦게 도착한 정정본을 놓친다.
        """
        return reader.query(
            self._connect(), self.root, table, as_of=as_of, entity=entity, lookback=lookback
        )

    def config(self, name: str, *, as_of: datetime) -> Any:
        """임계치 조회. 하드코딩 금지 — 불변식 10."""
        frame = self.get(_config.CONFIG_TABLE, as_of=as_of, entity=name)
        return _config.read_value(frame, name, as_of)

    # -- 적재 -----------------------------------------------------------------

    def append(
        self,
        table: str,
        records: Sequence[Mapping[str, object]],
        *,
        ingest_run_id: str,
        source: str | None = None,
    ) -> int:
        """append-only 적재. UPDATE/DELETE 는 존재하지 않는다."""
        return writer.append(
            self.root, table, records, ingest_run_id=ingest_run_id, source=source
        )

    def ingest_run_recorded(self, table: str, ingest_run_id: str) -> bool:
        return writer.run_recorded(self.root, table, ingest_run_id)

    def seed_config_defaults(self, path: Path | None = None) -> int:
        """체크인된 기본값을 config 테이블에 심는다.

        run id 가 파일 내용 해시라, 내용이 그대로면 두 번째 시딩은 거부된다.
        """
        source_file = path or DEFAULT_CONFIG_FILE
        return self.append(
            _config.CONFIG_TABLE,
            _config.defaults_rows(source_file),
            ingest_run_id=_config.defaults_run_id(source_file),
        )

    # -- 내부 -----------------------------------------------------------------

    def _connect(self) -> duckdb.DuckDBPyConnection:
        if self._connection is None:
            self._connection = duckdb.connect(database=":memory:")
        return self._connection

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None


def _default_root() -> Path:
    override = os.environ.get(ROOT_ENV)
    return Path(override) if override else DEFAULT_ROOT


_default: Store | None = None


def default_store() -> Store:
    global _default
    if _default is None:
        _default = Store()
    return _default


def get(
    table: str,
    *,
    as_of: datetime,
    entity: str | Sequence[str] | None = None,
    lookback: timedelta | int | None = None,
) -> pd.DataFrame:
    return default_store().get(table, as_of=as_of, entity=entity, lookback=lookback)


def append(
    table: str,
    records: Sequence[Mapping[str, object]],
    *,
    ingest_run_id: str,
    source: str | None = None,
) -> int:
    return default_store().append(table, records, ingest_run_id=ingest_run_id, source=source)


def config(name: str, *, as_of: datetime) -> Any:
    return default_store().config(name, as_of=as_of)


def tables() -> list[str]:
    return table_names()


__all__ = [
    "ConfigNotFound",
    "DuplicateIngestRun",
    "MissingObservedAt",
    "NaiveTimestamp",
    "SchemaViolation",
    "Store",
    "StoreError",
    "UnknownTable",
    "append",
    "config",
    "default_store",
    "get",
    "tables",
]
