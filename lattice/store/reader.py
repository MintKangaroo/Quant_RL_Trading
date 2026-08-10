"""이중시간 조회 — 창고의 유일한 출입구.

에이전트 15개가 각자 데이터를 긁으면 그중 하나는 반드시 미래를 본다.
출입구를 하나로 막는 것이 유일한 해법이다.

여기서 강제하는 것:
- ``observed_at <= as_of`` — 예외 없음
- 자연키마다 as_of 이전의 최신 ``revision`` 하나만
- 결정론적 정렬 — 같은 질의는 언제나 바이트 단위로 같은 결과
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

import duckdb
import pandas as pd

from lattice.store import paths
from lattice.store.errors import NaiveTimestamp
from lattice.store.schema import ROW_HASH, TableSpec
from lattice.store.tables import get_spec


def _require_aware(name: str, moment: datetime) -> datetime:
    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        raise NaiveTimestamp(f"{name} 에 타임존이 없다: {moment!r}")
    return moment


def _valid_from_floor(as_of: datetime, lookback: timedelta | int | None) -> datetime | None:
    """``lookback`` 을 valid_from 하한 시각으로 바꾼다.

    두 의미를 구분한다.
    - ``timedelta`` — 정확히 그만큼 이전의 순간
    - ``int`` — N 달력일 전의 UTC 자정. "지난 20일" 은 보통 이 뜻이고,
      조회를 09시에 돌리든 18시에 돌리든 같은 날짜 집합이 나온다
    """
    if lookback is None:
        return None
    if isinstance(lookback, timedelta):
        return as_of - lookback
    floor_date = as_of.astimezone(UTC).date() - timedelta(days=lookback)
    return datetime.combine(floor_date, time.min, tzinfo=UTC)


def _empty(spec: TableSpec) -> pd.DataFrame:
    return spec.arrow_schema.empty_table().to_pandas()


def query(
    connection: duckdb.DuckDBPyConnection,
    root: Path,
    table: str,
    *,
    as_of: datetime,
    entity: str | Sequence[str] | None = None,
    lookback: timedelta | int | None = None,
) -> pd.DataFrame:
    spec = get_spec(table)
    as_of = _require_aware("as_of", as_of)
    valid_floor = _valid_from_floor(as_of, lookback)

    files = list(
        paths.iter_data_files(
            root,
            table,
            upper=paths.observed_date(as_of),
            lower=paths.prune_lower_bound(
                valid_floor.date() if valid_floor else None, spec.observation_lag_days
            ),
        )
    )
    if not files:
        return _empty(spec)

    predicates = ["observed_at <= ?"]
    params: list[object] = [[str(path) for path in files], as_of]

    if entity is not None:
        entities = [entity] if isinstance(entity, str) else list(entity)
        predicates.append("list_contains(?, entity_id)")
        params.append(entities)

    if valid_floor is not None:
        predicates.append("valid_from >= ?")
        params.append(valid_floor)

    key = ", ".join(spec.natural_key)
    columns = ", ".join(spec.all_columns)
    sql = f"""
        WITH scoped AS (
            SELECT * FROM read_parquet(?, union_by_name = true)
            WHERE {" AND ".join(predicates)}
        ),
        ranked AS (
            SELECT *, row_number() OVER (
                PARTITION BY {key}
                -- 최신 정정본 우선. 마지막 타이브레이커가 있어야 결정론이 성립한다.
                ORDER BY revision DESC, observed_at DESC, {ROW_HASH} ASC
            ) AS _rank
            FROM scoped
        )
        SELECT {columns} FROM ranked
        WHERE _rank = 1
        ORDER BY {key}, {ROW_HASH}
    """
    return connection.execute(sql, params).df()
