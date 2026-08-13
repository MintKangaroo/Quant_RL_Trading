"""이중시간 조회 — 창고의 유일한 출입구.

에이전트 15개가 각자 데이터를 긁으면 그중 하나는 반드시 미래를 본다.
출입구를 하나로 막는 것이 유일한 해법이다.

여기서 강제하는 것:
- ``observed_at <= as_of`` — 예외 없음
- 자연키마다 as_of 이전의 최신 ``revision`` 하나만
- 결정론적 정렬 — 같은 질의는 언제나 바이트 단위로 같은 결과

``lookback`` 과 ``until`` 은 **``valid_from`` 축**을 자른다. ``as_of`` 축(관측)과
헷갈리면 안 된다. 큰 구간을 나눠 읽을 때 as_of 를 창마다 옮기면, 늦게 도착한
정정본이 어느 창에도 안 걸려 조용히 사라진다. 창은 valid_from 으로 자르고
as_of 는 요청한 시점 하나로 고정한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

import duckdb
import pandas as pd

from lattice.store import paths
from lattice.store.errors import NaiveTimestamp, SchemaViolation
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


def _projection(spec: TableSpec, columns: Sequence[str] | None) -> list[str]:
    """돌려줄 컬럼 목록.

    ``flows`` 처럼 큰 테이블은 문자열 컬럼(source·ingest_run_id·row_hash)이
    행 무게의 대부분이다. 안 쓸 컬럼을 여기서 자르면 pandas 로 넘어오는
    양이 몇 배 줄어든다 — 게이트를 우회하는 것이 아니라 게이트가 덜 퍼내는
    것이다.

    **정정본 선택(``revision``·``observed_at``·``row_hash``)은 프루닝 전에
    이미 끝나 있다.** 그래서 무엇을 자르든 어느 행이 남는지는 바뀌지 않는다.
    """
    if columns is None:
        return list(spec.all_columns)

    unknown = [name for name in columns if name not in spec.all_columns]
    if unknown:
        raise SchemaViolation(f"{spec.name} 에 없는 컬럼: {sorted(unknown)}")
    # 자연키는 항상 남긴다. 없으면 돌려받은 프레임이 무엇에 대한 값인지
    # 알 수 없고, 호출부가 매번 다시 물어보게 된다.
    return list(dict.fromkeys([*spec.natural_key, *columns]))


def query(
    connection: duckdb.DuckDBPyConnection,
    root: Path,
    table: str,
    *,
    as_of: datetime,
    entity: str | Sequence[str] | None = None,
    lookback: timedelta | int | None = None,
    until: datetime | None = None,
    columns: Sequence[str] | None = None,
    market: str | None = None,
) -> pd.DataFrame:
    spec = get_spec(table)
    as_of = _require_aware("as_of", as_of)
    valid_floor = _valid_from_floor(as_of, lookback)
    if until is not None:
        until = _require_aware("until", until)

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

    if until is not None:
        predicates.append("valid_from < ?")
        params.append(until)

    if market is not None:
        # 시장을 pandas 로 거르면 안 쓸 시장을 통째로 퍼온 뒤 버리게 된다.
        # 한 테이블에 여러 시장이 같이 사는 순간(미장 백필) 그 낭비가 국장
        # 질의를 죽인다 — 실제로 그렇게 죽었다.
        if "market" not in spec.all_columns:
            raise SchemaViolation(f"{spec.name} 에는 market 컬럼이 없다")
        predicates.append("market = ?")
        params.append(market)

    key = ", ".join(spec.natural_key)
    projected = _projection(spec, columns)
    # 스캔 단계에서부터 좁힌다. ``scoped`` 를 ``SELECT *`` 로 두면 프루닝이
    # 스캔까지 내려가지 못하고, 바로 뒤 ``row_number()`` 윈도우가 전 컬럼을
    # 물고 정렬한다 — 메모리 봉우리는 결과가 아니라 이 정렬에서 생긴다.
    # 순위 계산에 쓰이는 세 컬럼은 호출부가 안 달라고 해도 있어야 한다.
    scan = ", ".join(dict.fromkeys([*projected, "revision", "observed_at", ROW_HASH]))
    # 술어는 투영 전에 평가되므로, 여기 없는 컬럼(valid_from)으로도 거를 수 있다.
    sql = f"""
        WITH scoped AS (
            SELECT {scan} FROM read_parquet(?, union_by_name = true)
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
        SELECT {", ".join(projected)} FROM ranked
        WHERE _rank = 1
        ORDER BY {key}, {ROW_HASH}
    """
    return connection.execute(sql, params).df()
