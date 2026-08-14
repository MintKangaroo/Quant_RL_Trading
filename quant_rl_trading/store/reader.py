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
import pyarrow as pa

from quant_rl_trading.store import paths
from quant_rl_trading.store.errors import NaiveTimestamp, SchemaViolation
from quant_rl_trading.store.schema import ROW_HASH, TableSpec
from quant_rl_trading.store.tables import get_spec


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


#: pyarrow 타입 → DuckDB 캐스트 타입. 없는 컬럼을 NULL 로 세울 때만 쓴다.
_DUCKDB_TYPES: list[tuple[object, str]] = [
    (pa.types.is_timestamp, "TIMESTAMPTZ"),
    (pa.types.is_floating, "DOUBLE"),
    (pa.types.is_integer, "BIGINT"),
    (pa.types.is_boolean, "BOOLEAN"),
]


def _duckdb_type(dtype: pa.DataType) -> str:
    for predicate, name in _DUCKDB_TYPES:
        if predicate(dtype):  # type: ignore[operator]
            return name
    return "VARCHAR"


def _scan_columns(
    connection: duckdb.DuckDBPyConnection,
    spec: TableSpec,
    files: Sequence[str],
    names: Sequence[str],
) -> list[str]:
    """스캔 절. **파일에 없는 컬럼은 NULL 로 세운다.**

    스키마에 컬럼을 하나 추가하면, 그 전에 쓰인 parquet 에는 그 컬럼이 없다.
    이름을 그대로 SELECT 하면 DuckDB 가 ``Binder Error`` 로 죽는다 —
    ``union_by_name`` 은 **주어진 파일들 사이**를 맞출 뿐, 아무 파일에도 없는
    컬럼을 만들어 주지는 않기 때문이다.

    그래서 컬럼 추가가 **과거 데이터 읽기를 깨뜨린다.** 실제로 그랬다:
    ``nav_daily`` 에 ``benchmark_note`` 를 추가한 순간 그 전에 쓰인 창고가
    통째로 안 읽혔다. 새 컬럼이 아직 한 파일에도 없는 창을 조회하면 터지므로,
    빈 창고에서는 멀쩡하고 **오래된 구간을 볼 때만** 터진다 — 가장 늦게,
    가장 나쁜 때 드러나는 모양이다.

    append-only 창고에서 컬럼 추가는 정상적인 진화다. 옛 행에 그 값이 없었다는
    것은 사실이고, 그 사실은 NULL 이다. 예외가 아니다.
    """
    available = {
        str(row[0])
        for row in connection.execute(
            "SELECT column_name FROM "
            "(DESCRIBE SELECT * FROM read_parquet(?, union_by_name = true))",
            [list(files)],
        ).fetchall()
    }
    return [
        name
        if name in available
        else f"CAST(NULL AS {_duckdb_type(spec.all_columns[name])}) AS {name}"
        for name in names
    ]


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


def _scope(
    root: Path,
    spec: TableSpec,
    *,
    as_of: datetime,
    entity: str | Sequence[str] | None,
    valid_floor: datetime | None,
    until: datetime | None,
    market: str | None,
) -> tuple[list[str], list[object]] | None:
    """읽을 파일과 술어. 읽을 파일이 없으면 ``None``.

    ``query`` 와 ``latest_by_entity`` 가 같은 창을 봐야 한다. 두 벌로 두면
    한쪽만 고쳐졌을 때 두 조회가 조용히 다른 데이터를 본다.
    """
    files = list(
        paths.iter_data_files(
            root,
            spec.name,
            upper=paths.observed_date(as_of),
            lower=paths.prune_lower_bound(
                valid_floor.date() if valid_floor else None, spec.observation_lag_days
            ),
        )
    )
    if not files:
        return None

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

    return predicates, params


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

    scoped_to = _scope(
        root,
        spec,
        as_of=as_of,
        entity=entity,
        valid_floor=valid_floor,
        until=until,
        market=market,
    )
    if scoped_to is None:
        return _empty(spec)
    predicates, params = scoped_to

    key = ", ".join(spec.natural_key)
    projected = _projection(spec, columns)
    # 스캔 단계에서부터 좁힌다. ``scoped`` 를 ``SELECT *`` 로 두면 프루닝이
    # 스캔까지 내려가지 못하고, 바로 뒤 ``row_number()`` 윈도우가 전 컬럼을
    # 물고 정렬한다 — 메모리 봉우리는 결과가 아니라 이 정렬에서 생긴다.
    # 순위 계산에 쓰이는 세 컬럼은 호출부가 안 달라고 해도 있어야 한다.
    scan_names = list(dict.fromkeys([*projected, "revision", "observed_at", ROW_HASH]))
    scan = ", ".join(_scan_columns(connection, spec, params[0], scan_names))  # type: ignore[arg-type]
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


#: ``latest_by_entity`` 가 덧붙이는 컬럼 — 창 안에서 그 종목이 처음 보인 시각.
FIRST_VALID_FROM = "first_valid_from"


def latest_by_entity(
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
    """창 안에서 **종목마다 마지막 한 행**. ``first_valid_from`` 이 따라온다.

    "긴 창을 훑되 종목당 한 행만 쓴다" 는 질의를 ``query`` 로 하면 창 전체가
    pandas 로 올라온 뒤 거기서 버려진다. 유니버스 400일 × 2,877종목 =
    115만 행을 가져와 2,877 행을 남기는 식이다. 실측으로 그 복사가 세션당
    5.2초·RSS 982MB 였고, 머신이 붐빌 때는 같은 호출이 297초까지 늘어졌다 —
    비용이 행 수에 선형이 아니라 메모리 압력에 붙는다. 접기(fold)를 DuckDB
    안에서 끝내면 같은 조건에서 0.8초·366MB 다.

    **어느 행이 남는지는 ``query`` 와 같다.**
    - 마지막 행 선택은 ``query`` 의 정정본 선택과 같은 타이브레이커를 쓰되
      ``valid_from DESC`` 를 앞에 둔 것뿐이다. 자연키가
      ``(entity_id, valid_from)`` 인 테이블에서는 valid_from 마다 살아남는
      행이 하나이므로, ``query`` 결과를 종목별로 잘라 마지막을 취한 것과
      정확히 같다.
    - ``first_valid_from`` 은 창 안 **원본 행**의 최소 valid_from 이다.
      정정본 선택은 (entity_id, valid_from) 단위라 valid_from 집합 자체를
      바꾸지 않으므로, 접기 전후로 값이 같다.
    """
    spec = get_spec(table)
    if spec.natural_key != ("entity_id", "valid_from"):
        # 키가 더 길면 한 (종목, valid_from) 에 여러 행이 정상이고
        # (``fundamentals`` 의 metric, ``documents`` 의 doc_id), "종목당 마지막
        # 한 행" 은 그중 무엇을 남길지 말하지 않는다. 조용히 하나만 집으면
        # 나머지가 사라진 것을 호출부가 알 방법이 없다.
        raise SchemaViolation(
            f"{spec.name} 의 자연키는 {spec.natural_key} 다. 종목당 한 행으로 "
            "접으면 같은 valid_from 의 다른 행이 조용히 사라진다"
        )
    if FIRST_VALID_FROM in spec.all_columns:
        raise SchemaViolation(f"{spec.name} 에 이미 {FIRST_VALID_FROM} 컬럼이 있다")

    as_of = _require_aware("as_of", as_of)
    valid_floor = _valid_from_floor(as_of, lookback)
    if until is not None:
        until = _require_aware("until", until)

    scoped_to = _scope(
        root,
        spec,
        as_of=as_of,
        entity=entity,
        valid_floor=valid_floor,
        until=until,
        market=market,
    )
    if scoped_to is None:
        empty = _empty(spec)
        empty[FIRST_VALID_FROM] = pd.Series(dtype=empty["valid_from"].dtype)
        return empty
    predicates, params = scoped_to

    projected = _projection(spec, columns)
    scan_names = list(dict.fromkeys([*projected, "revision", "observed_at", ROW_HASH]))
    scan = ", ".join(_scan_columns(connection, spec, params[0], scan_names))  # type: ignore[arg-type]
    sql = f"""
        WITH scoped AS (
            SELECT {scan} FROM read_parquet(?, union_by_name = true)
            WHERE {" AND ".join(predicates)}
        ),
        folded AS (
            SELECT *, row_number() OVER (
                PARTITION BY entity_id
                ORDER BY valid_from DESC, revision DESC, observed_at DESC, {ROW_HASH} ASC
            ) AS _rank,
            min(valid_from) OVER (PARTITION BY entity_id) AS {FIRST_VALID_FROM}
            FROM scoped
        )
        SELECT {", ".join([*projected, FIRST_VALID_FROM])} FROM folded
        WHERE _rank = 1
        ORDER BY entity_id
    """
    return connection.execute(sql, params).df()
