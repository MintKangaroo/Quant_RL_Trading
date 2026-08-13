"""append-only 적재.

UPDATE 도 DELETE 도 없다. 정정은 revision 을 올린 새 행이다.
기존 Parquet 파일은 어떤 경우에도 다시 쓰지 않는다 — 한 번 쓰인 파일이
불변이어야 "그때 알던 것" 이 보존된다.
"""

from __future__ import annotations

import json
import os
import shutil
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from quant_rl_trading.store import paths
from quant_rl_trading.store.errors import DuplicateIngestRun, SchemaViolation, StoreError
from quant_rl_trading.store.schema import TableSpec, ValidatedBatch, validate_batch
from quant_rl_trading.store.tables import get_spec


def _manifest(root: Path, table: str, ingest_run_id: str) -> Path:
    return paths.manifest_path(root, table, ingest_run_id)


def run_recorded(root: Path, table: str, ingest_run_id: str) -> bool:
    return _manifest(root, table, ingest_run_id).is_file()


def _partitioned(batch: ValidatedBatch) -> dict[date, list[dict[str, object]]]:
    grouped: dict[date, list[dict[str, object]]] = defaultdict(list)
    for row in batch.rows:
        moment = row["observed_at"]
        if not isinstance(moment, datetime):  # validate_batch 가 보장하지만, 타입을 좁힌다
            raise SchemaViolation(f"observed_at 이 datetime 이 아니다: {moment!r}")
        grouped[paths.observed_date(moment)].append(row)
    return dict(grouped)


def _to_arrow(spec: TableSpec, rows: Sequence[Mapping[str, object]]) -> pa.Table:
    columns = {name: [row.get(name) for row in rows] for name in spec.all_columns}
    return pa.Table.from_pydict(columns, schema=spec.arrow_schema)


def append(
    root: Path,
    table: str,
    records: Sequence[Mapping[str, object]],
    *,
    ingest_run_id: str,
    source: str | None = None,
) -> int:
    """검증 → 스테이징 → 원자적 이동 → 매니페스트 순으로 적재한다.

    검증이 전부 끝난 뒤에야 첫 바이트를 쓴다. 절반만 들어간 배치는
    append-only 창고에서 되돌릴 수 없기 때문이다.
    """
    spec = get_spec(table)

    if run_recorded(root, table, ingest_run_id):
        raise DuplicateIngestRun(
            f"{table}/{ingest_run_id} 는 이미 적재됐다. "
            "append-only 창고에서 중복은 지울 수 없으므로 재적재를 거부한다"
        )

    batch = validate_batch(spec, records, ingest_run_id=ingest_run_id, source=source)
    if not batch.rows:
        return 0

    partitions = _partitioned(batch)
    staging = paths.staging_dir(root) / ingest_run_id
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    written: list[str] = []
    try:
        staged: list[tuple[Path, Path]] = []
        for observed, rows in sorted(partitions.items()):
            target = paths.data_file(root, table, observed, ingest_run_id)
            if target.exists():
                raise StoreError(
                    f"{target} 가 이미 있다. 쓰인 파일은 다시 쓰지 않는다 (append-only)"
                )
            temporary = staging / f"{observed.isoformat()}{paths.PARQUET_SUFFIX}"
            pq.write_table(_to_arrow(spec, rows), temporary)
            staged.append((temporary, target))

        for temporary, target in staged:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(temporary, target)
            written.append(str(target.relative_to(root)))
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    manifest = _manifest(root, table, ingest_run_id)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(
            {
                "table": table,
                "ingest_run_id": ingest_run_id,
                "rows": len(batch.rows),
                "files": written,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return len(batch.rows)
