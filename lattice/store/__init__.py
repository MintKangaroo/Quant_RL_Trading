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
DEFAULT_CONFIG_FILE = REPO_ROOT / "config" / "lattice.yaml"
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
        until: datetime | None = None,
    ) -> pd.DataFrame:
        """``as_of`` 시점에 알 수 있었던 것만 돌려준다.

        ``lookback`` 과 ``until`` 은 ``valid_from`` 기준 창이다(lookback 이
        정수면 일). 관측 기준이 아니다 — 둘을 같다고 보면 뒤늦게 도착한
        정정본을 놓친다. 큰 구간을 나눠 읽을 때는 **as_of 를 옮기지 말고
        이 창을 옮겨야 한다.**
        """
        return reader.query(
            self._connect(),
            self.root,
            table,
            as_of=as_of,
            entity=entity,
            lookback=lookback,
            until=until,
        )

    def config(self, name: str, *, as_of: datetime) -> Any:
        """임계치 조회. 하드코딩 금지 — 불변식 10.

        점이 있으면 값 하나(``"reward.w_free"``), 없으면 섹션 전체를 dict 로
        (``"reward"``) 돌려준다. 보상 함수처럼 값 열 개를 한꺼번에 쓰는 곳은
        키를 하나씩 읽으면 키가 늘 때마다 호출부를 고쳐야 한다.
        """
        exact = self.get(_config.CONFIG_TABLE, as_of=as_of, entity=name)
        if "." in name or not exact.empty:
            # 점이 있거나, 그 이름의 값이 실제로 있으면 값 하나다.
            # ``config_version`` 처럼 섹션에 속하지 않는 최상위 값이 여기 걸린다.
            return _config.read_value(exact, name, as_of)

        # 섹션 조회는 entity 로 좁힐 수 없다. 게이트가 접두사 검색을 하지
        # 않으므로 config 테이블 전체를 받아 여기서 고른다 — 설정은 수백 행
        # 규모라 문제되지 않는다.
        frame = self.get(_config.CONFIG_TABLE, as_of=as_of)
        return _config.read_section(frame, name, as_of)

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

    def seed_config_defaults(
        self, path: Path | None = None, *, effective_at: datetime | None = None
    ) -> int:
        """체크인된 기본값을 config 테이블에 심는다.

        이미 있는 값은 건드리지 않고, **바뀐 값만 정정본으로** 넣는다.
        과거 as_of 조회는 옛 값을 그대로 본다 — 오늘 임계치를 바꿨다고 작년
        백테스트 결과가 소급해 바뀌면 재현이 불가능해진다.

        빈 창고에 처음 심을 때는 ``effective_at`` 이 필요 없다. 값이 바뀌어
        정정본을 넣어야 할 때만 필요하며, **store 는 벽시계를 읽지 않는다** —
        시각의 출처는 언제나 호출자의 Clock 이다 (불변식 2).
        """
        source_file = path or DEFAULT_CONFIG_FILE
        probe = effective_at or _config.DEFAULTS_EPOCH
        existing = _config.current_values(self.get(_config.CONFIG_TABLE, as_of=probe))

        if existing and effective_at is None:
            # **바뀐 값**만 발효 시점을 요구한다. 새로 생긴 키는 아니다 —
            # 새 키에는 덮어쓸 과거가 없으므로 소급 변경 위험이 없고,
            # 여기서 함께 막으면 설정을 추가할 때마다 기존 창고가 그 키를
            # 영영 모르는 채로 남는다.
            changed = _config.changed_names(source_file, existing)
            if changed:
                raise SchemaViolation(
                    f"설정 {sorted(changed)} 이 바뀌었다. 정정본을 넣으려면 "
                    "effective_at 을 줘야 한다 — 발효 시점 없이 덮으면 과거 "
                    "as_of 조회까지 소급해 바뀐다"
                )

        rows = _config.defaults_rows(
            source_file, current=existing, effective_at=effective_at
        )
        if not rows:
            return 0
        return self.append(
            _config.CONFIG_TABLE,
            rows,
            ingest_run_id=_config.defaults_run_id(source_file, moment=effective_at),
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
    until: datetime | None = None,
) -> pd.DataFrame:
    return default_store().get(
        table, as_of=as_of, entity=entity, lookback=lookback, until=until
    )


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
