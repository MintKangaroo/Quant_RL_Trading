"""파티션 안의 작은 Parquet 들을 하나로 합친다 — **행은 그대로, 파일 수만 줄인다.**

    .venv/bin/python tools/compact_partitions.py --table indices --dry-run
    .venv/bin/python tools/compact_partitions.py --table indices --table fx --apply

## 왜

창고 조회 비용은 행 수가 아니라 **파일 수**로 붙는다. `indices` 는 9.0MB 를 파일 2,000개로
들고 있고(하루 파티션이 30개 안팎 — append 마다 파일 하나), 180일 창이면 DuckDB 가 그 footer 를
전부 연다: **122행 얻는 데 280ms.** 같은 창고에서 `prices` 15일은 파일 175개로 63,196행을
45ms 에 준다. 파티션당 ~1.5ms 가 붙는 것이다(2026-09-17 실측).

## 불변식과의 관계

**append-only 를 어기지 않는다.** 논리적으로 아무것도 안 바뀐다 — 같은 행이 같은 값으로 남고,
`revision`·`observed_at`·`row_hash` 도 그대로다. 파일 배치만 바꾸는 물리 연산이다.
``_manifests`` 는 **손대지 않는다** — 적재 이력은 그대로 남아야 재실행 판정이 산다.

## 안전 순서

1. 합친 것을 `_staging` 에 쓴다.
2. **검증**: 행 수와 `row_hash` 정렬 md5 가 원본 묶음과 같은가. 다르면 그 파티션은 건너뛴다.
3. 파티션 안으로 옮긴다. 이 시점에 조회는 **중복 행**을 본다 — 자연키로 접으므로 무해하다
   (같은 revision·observed_at·row_hash 라 어느 쪽을 골라도 같다).
4. 원본을 지운다. **여기가 유일한 경합 지점**이다 — 읽는 쪽이 파일 목록을 먼저 글롭하므로
   (`paths._listing`), 지우는 순간 그 목록을 든 조회가 실패할 수 있다.

그래서 **장 중·수집 중에는 돌리지 않는다.** 그리고 `--older-than-days` 로 최근 파티션은
아예 건드리지 않는다 — 오늘 쓰이는 파티션을 합치면 쓰는 쪽과 부딪힌다.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import duckdb  # noqa: E402

from quant_rl_trading.store import paths  # noqa: E402

#: 합친 파일 이름의 앞머리. 파일명은 유일성에만 쓰이고 아무도 해석하지 않는다(store/paths.py).
MERGED_PREFIX = "_compact-"
#: 기본 유예. 최근 파티션은 오늘도 쓰인다 — 합치면 쓰는 쪽과 부딪힌다.
DEFAULT_OLDER_THAN = 3


def _fingerprint(connection: duckdb.DuckDBPyConnection, files: list[str]) -> tuple[int, str]:
    """(행 수, row_hash 정렬 md5). **이게 같아야 원본을 지운다.**"""
    rows, digest = connection.execute(
        "SELECT count(*), md5(string_agg(row_hash, ',' ORDER BY row_hash)) "
        "FROM read_parquet(?, union_by_name = true)",
        [files],
    ).fetchone()
    return int(rows), str(digest or "")


def compact_partition(
    connection: duckdb.DuckDBPyConnection, directory: Path, staging: Path
) -> tuple[bool, str]:
    """파티션 하나. (합쳤나, 사유)."""
    files = sorted(str(f) for f in directory.glob(f"*{paths.PARQUET_SUFFIX}"))
    if len(files) < 2:
        return False, "파일 하나뿐"
    before_rows, before_hash = _fingerprint(connection, files)
    if not before_hash:
        return False, "row_hash 가 비었다 — 검증할 수 없다"

    staging.mkdir(parents=True, exist_ok=True)
    tmp = staging / f"{MERGED_PREFIX}{directory.name}-{os.getpid()}{paths.PARQUET_SUFFIX}"
    # **`COPY ... TO ?` 는 파라미터를 안 받는다** (DuckDB InternalException: Unsupported
    # parameter type for filename). 경로만 리터럴로 박고 작은따옴표를 이스케이프한다.
    # 읽는 쪽 파일 목록은 그대로 파라미터라 경로가 아무리 많아도 SQL 이 안 길어진다.
    literal = str(tmp).replace("'", "''")
    connection.execute(
        f"COPY (SELECT * FROM read_parquet(?, union_by_name = true)) "
        f"TO '{literal}' (FORMAT PARQUET)",
        [files],
    )
    after_rows, after_hash = _fingerprint(connection, [str(tmp)])
    if (after_rows, after_hash) != (before_rows, before_hash):
        tmp.unlink(missing_ok=True)
        return False, f"검증 실패 — 행 {before_rows}→{after_rows}, 해시 불일치"

    # 합친 것을 먼저 넣는다. 이 순간 조회는 중복을 보지만 자연키로 접으므로 무해하다.
    stamp = hashlib.sha256(before_hash.encode()).hexdigest()[:12]
    target = directory / f"{MERGED_PREFIX}{stamp}{paths.PARQUET_SUFFIX}"
    os.replace(tmp, target)
    for name in files:
        Path(name).unlink(missing_ok=True)
    return True, f"{len(files)}개 → 1개"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="data")
    parser.add_argument("--table", action="append", required=True, help="여러 번 줄 수 있다")
    parser.add_argument("--older-than-days", type=int, default=DEFAULT_OLDER_THAN)
    parser.add_argument("--limit", type=int, default=0, help="파티션 N 개만")
    parser.add_argument("--apply", action="store_true", help="실제로 합친다 (기본은 미리보기)")
    args = parser.parse_args(argv)

    root = Path(args.root)
    today = datetime.now(UTC).date()  # invariant-allow: wallclock — 파일 정리 유예 계산
    cutoff = today - timedelta(days=int(args.older_than_days))
    staging = root / paths.CURATED / paths.STAGING
    connection = duckdb.connect()
    connection.execute("SET memory_limit = '600MB'")
    total_before = total_after = touched = skipped = 0
    started = time.time()

    for table in args.table:
        table_dir = paths.curated_dir(root, table)
        if not table_dir.is_dir():
            print(f"{table}: 창고에 없다 — 건너뜀", file=sys.stderr)
            continue
        partitions = []
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
                partitions.append((directory, len(files), sum(f.stat().st_size for f in files)))
        if args.limit:
            partitions = partitions[: args.limit]
        files_before = sum(n for _, n, _ in partitions)
        bytes_before = sum(b for _, _, b in partitions)
        print(f"{table}: 대상 파티션 {len(partitions)}개 · 파일 {files_before:,}개 "
              f"· {bytes_before / 1e6:.1f}MB (기준일 {cutoff} 이전)", flush=True)
        if not args.apply:
            continue
        for directory, _, _ in partitions:
            ok, reason = compact_partition(connection, directory, staging)
            if ok:
                touched += 1
            else:
                skipped += 1
                print(f"  {directory.name}: 건너뜀 — {reason}", file=sys.stderr)
        after = sum(len(list(d.glob(f"*{paths.PARQUET_SUFFIX}"))) for d, _, _ in partitions)
        bytes_after = sum(
            sum(f.stat().st_size for f in d.glob(f"*{paths.PARQUET_SUFFIX}"))
            for d, _, _ in partitions
        )
        total_before += files_before
        total_after += after
        print(f"  → 파일 {files_before:,} → {after:,} · "
              f"{bytes_before / 1e6:.1f}MB → {bytes_after / 1e6:.1f}MB", flush=True)

    if args.apply:
        print(f"\n합친 파티션 {touched} · 건너뜀 {skipped} · "
              f"파일 {total_before:,} → {total_after:,} · {time.time() - started:.0f}s")
        if skipped:
            return 1
    else:
        print("\n미리보기다. 실제로 합치려면 --apply. **장 중·수집 중에는 돌리지 않는다.**")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
