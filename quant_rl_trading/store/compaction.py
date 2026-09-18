"""파티션 압축 — 작은 Parquet 들을 하나로 합친다. **행은 그대로, 파일 수만 준다.**

## 왜 store/ 안에 있나

파일 배치를 바꾸는 물리 연산이라 `store.get` 을 경유할 수 없다 — 경유할 대상이 파일 자체다.
불변식 1 이 금하는 것은 "창고 밖에서 드라이버를 잡는 것" 이고, 여기는 창고 안이다.
`tools/compact_partitions.py` 는 이 모듈을 부르는 껍데기다.

## 왜 합치나

조회 비용은 행 수가 아니라 **파일 수**로 붙는다. `indices` 는 9.0MB 를 파일 2,000개로 들고
있고(하루 파티션이 30개 안팎 — append 마다 파일 하나) 180일 창이면 DuckDB 가 그 footer 를
전부 연다: **122행 얻는 데 280ms.** 같은 창고에서 `prices` 15일은 파일 175개로 63,196행을
45ms 에 준다 — 파티션당 ~1.5ms 다(2026-09-17 실측).

## append-only 와의 관계

**어기지 않는다.** 논리적으로 아무것도 안 바뀐다 — 같은 행이 같은 값으로 남고
`revision`·`observed_at`·`row_hash` 도 그대로다. ``_manifests`` 는 **손대지 않는다**:
적재 이력이 사라지면 이미 돈 적재가 "안 돈 것" 이 되어 다시 돈다.

## 안전 순서

1. 합친 것을 ``_staging`` 에 쓴다.
2. **검증** — 행 수와 `row_hash` 정렬 md5 가 원본 묶음과 같은가. 다르면 그 파티션은 건너뛴다.
3. 파티션 안으로 옮긴다. 이 시점 조회는 **중복 행**을 보지만 자연키로 접으므로 무해하다
   (같은 revision·observed_at·row_hash 라 어느 쪽을 골라도 같다).
4. 원본을 지운다. **여기가 유일한 경합 지점이다** — 읽는 쪽이 파일 목록을 먼저 글롭하므로
   (`paths._listing`), 지우는 순간 그 목록을 든 조회가 실패할 수 있다.

그래서 **장 중·수집 중에는 돌리지 않는다.**
"""

from __future__ import annotations

import hashlib
import os
from datetime import date
from pathlib import Path

import duckdb

from quant_rl_trading.store import paths

#: 합친 파일 이름의 앞머리. 파일명은 유일성에만 쓰이고 아무도 해석하지 않는다(store/paths.py).
#: 사람이 창고를 뒤질 때 이것이 적재본이 아니라 합친 것임을 알아보라고 붙인다.
MERGED_PREFIX = "_compact-"


def connect(memory_limit: str = "600MB") -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect()
    connection.execute(f"SET memory_limit = '{memory_limit}'")
    return connection


def fingerprint(connection: duckdb.DuckDBPyConnection, files: list[str]) -> tuple[int, str]:
    """(행 수, row_hash 정렬 md5). **이게 같아야 원본을 지운다.**"""
    rows, digest = connection.execute(
        "SELECT count(*), md5(string_agg(row_hash, ',' ORDER BY row_hash)) "
        "FROM read_parquet(?, union_by_name = true)",
        [files],
    ).fetchone()
    return int(rows), str(digest or "")


def candidates(root: Path, table: str, *, cutoff: date) -> list[tuple[Path, int, int]]:
    """(파티션, 파일 수, 바이트) — ``cutoff`` 이전이고 파일이 둘 이상인 것만."""
    table_dir = paths.curated_dir(root, table)
    if not table_dir.is_dir():
        return []
    out: list[tuple[Path, int, int]] = []
    for directory in sorted(table_dir.iterdir()):
        if not directory.is_dir() or not directory.name.startswith("observed_date="):
            continue
        try:
            stamp = date.fromisoformat(directory.name.split("=", 1)[1])
        except ValueError:
            continue
        if stamp >= cutoff:
            continue
        files = list(directory.glob(f"*{paths.PARQUET_SUFFIX}"))
        if len(files) >= 2:
            out.append((directory, len(files), sum(f.stat().st_size for f in files)))
    return out


def compact_partition(
    connection: duckdb.DuckDBPyConnection, directory: Path, staging: Path
) -> tuple[bool, str]:
    """파티션 하나. (합쳤나, 사유)."""
    files = sorted(str(f) for f in directory.glob(f"*{paths.PARQUET_SUFFIX}"))
    if len(files) < 2:
        return False, "파일 하나뿐"
    before_rows, before_hash = fingerprint(connection, files)
    if not before_hash:
        return False, "row_hash 가 비었다 — 검증할 수 없다"

    staging.mkdir(parents=True, exist_ok=True)
    tmp = staging / f"{MERGED_PREFIX}{directory.name}-{os.getpid()}{paths.PARQUET_SUFFIX}"
    # **`COPY ... TO ?` 는 파라미터를 안 받는다** (DuckDB InternalException: Unsupported
    # parameter type for filename). 경로만 리터럴로 박고 작은따옴표를 이스케이프한다.
    literal = str(tmp).replace("\'", "\'\'")
    connection.execute(
        f"COPY (SELECT * FROM read_parquet(?, union_by_name = true)) "
        f"TO '{literal}' (FORMAT PARQUET)",
        [files],
    )
    after_rows, after_hash = fingerprint(connection, [str(tmp)])
    if (after_rows, after_hash) != (before_rows, before_hash):
        tmp.unlink(missing_ok=True)
        return False, f"검증 실패 — 행 {before_rows}→{after_rows}, 해시 불일치"

    # 합친 것을 먼저 넣는다. 이 순간 조회는 중복을 보지만 자연키로 접으므로 무해하다.
    stamp = hashlib.sha256(before_hash.encode()).hexdigest()[:12]
    os.replace(tmp, directory / f"{MERGED_PREFIX}{stamp}{paths.PARQUET_SUFFIX}")
    for name in files:
        Path(name).unlink(missing_ok=True)
    return True, f"{len(files)}개 → 1개"
