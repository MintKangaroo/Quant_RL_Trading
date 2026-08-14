"""공통 필수 컬럼과 저장 전 검증.

이중시간의 핵심은 두 시각을 헷갈리지 않는 것이다.

    valid_from   그 사실이 실제로 유효해진 시점
    observed_at  내가 그것을 알 수 있었던 시점

``valid_from > observed_at`` 은 정상이다 (실적발표 예정일, 지수 리밸런싱 발효일).
반대로 ``observed_at`` 이 없는 행은 저장 자체를 거부한다 — 불변식 3.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pyarrow as pa

from quant_rl_trading.store.errors import MissingObservedAt, NaiveTimestamp, SchemaViolation

#: 모든 테이블이 갖는 컬럼. docs/design/data-contract.md §1
REQUIRED_COLUMNS: dict[str, pa.DataType] = {
    "entity_id": pa.string(),
    "valid_from": pa.timestamp("us", tz="UTC"),
    "observed_at": pa.timestamp("us", tz="UTC"),
    "source": pa.string(),
    "revision": pa.int32(),
    "ingest_run_id": pa.string(),
}

#: 결정론적 타이브레이커. 파일 나열 순서에 결과가 의존하면 안 된다.
ROW_HASH = "row_hash"

TIMESTAMP_COLUMNS = ("valid_from", "observed_at")

#: revision 은 생략하면 원본(0)으로 본다. ingest_run_id 는 append() 인자에서 채운다.
_ROW_DEFAULTS: dict[str, object] = {"revision": 0}


@dataclass(frozen=True)
class TableSpec:
    """테이블 하나의 계약."""

    name: str
    columns: dict[str, pa.DataType]
    #: 정정본을 고를 때 묶는 단위. 이 키마다 최신 revision 하나만 살아남는다.
    natural_key: tuple[str, ...] = ("entity_id", "valid_from")
    #: 관측이 유효시점보다 이만큼 늦게까지 도착한다고 선언된 테이블만
    #: 하한 파티션 프루닝을 허용한다. None 이면 하한을 자르지 않는다(느리지만 안전).
    observation_lag_days: int | None = None
    #: ``market`` 컬럼이 있는 테이블은 기본적으로 ``entity_id`` 의 첫 세그먼트가
    #: 그 값과 같아야 한다("KR:005930"·"KR:IDX:KOSPI" 는 KR, "US:AA" 는 US).
    #: 실제로 이게 깨져서 KR 백필이 미장 종목 6,648개를 market="KR" 로
    #: 잘못 찍은 사고가 났다(2026-08-15, backfill.py 의
    #: ``Backfiller._known_listed()``). append-only 창고에서는 이런 값은
    #: 쓰기 시점에 막는 것 말고 되돌릴 방법이 없다 — 정정본을 얹어도
    #: ``market`` 으로 거르는 조회에서는 그 필터가 정정본 선택보다 먼저
    #: 걸려서 잘못된 행이 계속 살아남는다(reader.py ``_scope`` 참고).
    #:
    #: 예외를 두는 이유: ``analyst_weights`` 처럼 ``entity_id`` 가 종목이
    #: 아니라 analyst 이름인 테이블은 이 규칙이 아예 안 맞는다. 그런
    #: 테이블만 명시적으로 ``False`` 를 준다 — 조용히 넘어가면 다음 사람이
    #: "이 테이블은 검증 안 하나?" 를 코드에서 못 찾는다.
    market_prefixed_entity: bool = True
    doc: str = ""

    @property
    def all_columns(self) -> dict[str, pa.DataType]:
        return {**REQUIRED_COLUMNS, **self.columns, ROW_HASH: pa.string()}

    @property
    def arrow_schema(self) -> pa.Schema:
        return pa.schema([pa.field(name, dtype) for name, dtype in self.all_columns.items()])


@dataclass
class ValidatedBatch:
    """검증을 통과해 저장 준비가 끝난 행들."""

    spec: TableSpec
    rows: list[dict[str, object]] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.rows)


def _canonical(value: object) -> str:
    if isinstance(value, datetime):
        # 같은 순간은 어떤 타임존으로 표기됐든 같은 해시를 내야 한다.
        return value.astimezone(UTC).isoformat()
    if value is None:
        return "\x00"
    return repr(value)


def compute_row_hash(row: Mapping[str, object]) -> str:
    """행 내용만으로 결정되는 해시. 같은 내용은 언제 어디서 계산해도 같다."""
    payload = "\x1f".join(
        f"{key}={_canonical(row[key])}" for key in sorted(row) if key != ROW_HASH
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _check_timestamp(column: str, value: object) -> datetime:
    if not isinstance(value, datetime):
        raise SchemaViolation(f"{column} 은 datetime 이어야 한다: {value!r}")
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise NaiveTimestamp(
            f"{column} 에 타임존이 없다: {value!r}. UTC 로 명시된 시각만 저장한다"
        )
    return value


def _check_market_prefix(spec: TableSpec, index: int, row: dict[str, object]) -> None:
    """``entity_id`` 의 시장 접두어가 ``market`` 값과 같은지 확인한다.

    **쓰기 시점에만 막을 수 있다.** ``market`` 은 조회의 WHERE 절에서 정정본
    선택보다 먼저 걸리므로(reader.py ``_scope``), 한 번 잘못 들어간 값은
    나중에 정정 행을 더 넣어도 그 필터로는 절대 가려지지 않는다 — 2026-08-15
    사고(KR 백필이 US 종목 6,648개를 market="KR" 로 찍은 것)가 그렇게 났다.

    ``entity_id`` 가 ``"KR:IDX:KOSPI"`` 처럼 세그먼트가 셋인 것도 있어서
    **첫 세그먼트만** 본다. ``market`` 이 비어 있으면(일부 테이블은 항상
    채우지 않는다) 비교할 게 없으므로 건너뛴다.
    """
    market = row.get("market")
    if market is None or market == "":
        return
    entity_id = str(row.get("entity_id") or "")
    prefix = entity_id.split(":", 1)[0]
    if prefix != str(market):
        raise SchemaViolation(
            f"{spec.name}[{index}] entity_id={entity_id!r} 의 시장 접두어"
            f"({prefix!r})가 market={market!r} 과 다르다. entity_id 는 "
            f"'{market}:...' 이어야 한다 — 다른 시장 데이터가 이 market 으로 "
            "찍히면 정정본으로도 되돌릴 수 없다 (TableSpec.market_prefixed_entity 참고)"
        )


def validate_batch(
    spec: TableSpec,
    records: Sequence[Mapping[str, object]],
    *,
    ingest_run_id: str,
    source: str | None = None,
) -> ValidatedBatch:
    """행 전체를 검증한다. 한 행이라도 틀리면 아무것도 저장하지 않는다.

    부분 저장은 append-only 창고에서 되돌릴 수 없으므로, 검증은 쓰기 전에
    전부 끝낸다.
    """
    if not ingest_run_id or "/" in ingest_run_id or "\\" in ingest_run_id:
        raise SchemaViolation(
            f"ingest_run_id 가 비었거나 경로 구분자를 포함한다: {ingest_run_id!r}"
        )

    known = set(spec.all_columns)
    batch = ValidatedBatch(spec=spec)

    for index, record in enumerate(records):
        row: dict[str, object] = {**_ROW_DEFAULTS, **dict(record)}
        row["ingest_run_id"] = ingest_run_id
        if source is not None:
            row.setdefault("source", source)
        row.pop(ROW_HASH, None)

        if "observed_at" not in row or row["observed_at"] is None:
            raise MissingObservedAt(
                f"{spec.name}[{index}] 에 observed_at 이 없다. "
                "언제 알 수 있었는지 모르는 사실은 저장하지 않는다 (불변식 3)"
            )

        unknown = set(row) - known
        if unknown:
            raise SchemaViolation(f"{spec.name}[{index}] 미등록 컬럼: {sorted(unknown)}")

        # 필수는 이중시간 공통 컬럼뿐이다. 값 컬럼은 결측이 정상이므로
        # (호가 없는 날, 아직 공시되지 않은 지표) 빠지면 null 로 남긴다.
        # 오타는 위의 미등록 컬럼 검사가 잡는다.
        missing = set(REQUIRED_COLUMNS) - set(row)
        if missing:
            raise SchemaViolation(f"{spec.name}[{index}] 필수 컬럼 누락: {sorted(missing)}")

        for column in TIMESTAMP_COLUMNS:
            row[column] = _check_timestamp(column, row[column])

        if spec.market_prefixed_entity and "market" in spec.columns:
            _check_market_prefix(spec, index, row)

        revision = row["revision"]
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
            raise SchemaViolation(f"{spec.name}[{index}] revision 은 0 이상의 정수다: {revision!r}")

        row[ROW_HASH] = compute_row_hash(row)
        batch.rows.append(row)

    return batch


def empty_frame(spec: TableSpec) -> pa.Table:
    """조회 결과가 없을 때도 스키마는 같아야 한다."""
    return spec.arrow_schema.empty_table()
