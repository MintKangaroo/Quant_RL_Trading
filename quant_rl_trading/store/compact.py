"""파티션 안의 조각 파일을 하나로 합친다.

**이것은 append-only 예외다.** writer.py 는 "한 번 쓰인 파일은 다시 쓰지
않는다" 고 못 박았고, 그 규칙의 목적은 *그때 알던 것이 나중에 바뀌지 않는
것* 이다. 합치기는 **행을 하나도 바꾸지 않는다** — 같은 행을 같은 파티션
안에서 다른 파일에 옮겨 담을 뿐이다. 그리고 출처는 파일 이름이 아니라
행의 ``ingest_run_id`` 컬럼에 들어 있으므로, 합쳐도 어느 적재가 그 행을
넣었는지는 그대로 남는다.

그래서 이 모듈은 **내용이 같다는 것을 증명한 뒤에만** 원본을 지운다.
파티션마다 ``row_hash`` 다중집합의 지문을 합치기 전후로 비교하고, 하나라도
어긋나면 그 파티션을 건드리지 않고 넘어간다.

## 왜 필요한가

백필이 **종목마다 적재를 따로** 돌면 파티션 하나에 종목 수만큼 파일이 생긴다.
``flows`` 가 그렇게 됐다 — 1,224 파티션 × 약 890 종목 = **파일 109만 개, 4.3GB**.
파일 하나가 평균 4KB 다. Parquet 은 파일마다 푸터를 읽어야 해서, 이 상태의
읽기 비용은 데이터 양이 아니라 **파일 개수**에 붙는다. 60일 창을 열면
5만 5천 개를 연다.

## 크래시 안전

원본을 지우기 전에 합친 파일을 먼저 제자리에 놓는다. 그 사이에 죽으면
같은 행이 두 파일에 있게 되는데, 읽기가 자연키마다
``revision DESC, observed_at DESC, row_hash ASC`` 로 하나만 고르므로
(reader.py) 똑같은 행의 중복은 조용히 접힌다. 반대 순서로 하면 그 창에서
행이 **사라진다** — 중복은 접히지만 결손은 접히지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from quant_rl_trading.store import paths
from quant_rl_trading.store.errors import StoreError
from quant_rl_trading.store.schema import ROW_HASH
from quant_rl_trading.store.tables import get_spec

#: 합친 파일의 이름 앞머리. ``ingest_run_id`` 는 이 글자로 시작하지 않으므로
#: (적재가 만드는 이름은 수집기·백필이 정한다) 이름이 부딪히지 않는다.
PACKED_PREFIX = "_packed-"

#: 제자리에 만드는 임시 파일. ``.parquet`` 으로 끝나면 안 된다 —
#: ``iter_data_files`` 가 ``*.parquet`` 를 훑으므로, 검증도 끝나지 않은
#: 파일이 조회에 섞여 들어간다.
TEMP_SUFFIX = ".parquet.tmp"


@dataclass(frozen=True)
class PartitionResult:
    """파티션 하나의 결과."""

    partition: date
    files_before: int
    files_after: int
    rows: int
    bytes_before: int
    bytes_after: int
    skipped: str | None = None


@dataclass(frozen=True)
class CompactReport:
    table: str
    partitions: tuple[PartitionResult, ...]

    @property
    def touched(self) -> tuple[PartitionResult, ...]:
        return tuple(item for item in self.partitions if item.skipped is None)

    @property
    def files_before(self) -> int:
        return sum(item.files_before for item in self.partitions)

    @property
    def files_after(self) -> int:
        return sum(item.files_after for item in self.partitions)

    @property
    def bytes_before(self) -> int:
        return sum(item.bytes_before for item in self.partitions)

    @property
    def bytes_after(self) -> int:
        return sum(item.bytes_after for item in self.partitions)

    @property
    def rows(self) -> int:
        return sum(item.rows for item in self.partitions)


def _fingerprint(table: pa.Table) -> tuple[int, str]:
    """행 다중집합의 지문. **순서에 의존하지 않는다.**

    합치면 행 순서가 바뀐다. 순서를 포함해 재면 정상적인 합치기가 전부
    불일치로 나오고, 그러면 검증을 끄고 싶어진다. 끈 검증은 없느니만 못하다.
    """
    if ROW_HASH not in table.column_names:
        raise StoreError(f"{ROW_HASH} 컬럼이 없다. 이 창고는 합칠 수 없다")
    values = sorted(value.as_py() for value in table.column(ROW_HASH))
    digest = hashlib.sha256()
    for value in values:
        digest.update(b"\x00" if value is None else str(value).encode("utf-8"))
        digest.update(b"\x1f")
    return table.num_rows, digest.hexdigest()


def _partition_dirs(root: Path, table: str) -> Iterator[tuple[date, Path]]:
    table_dir = paths.curated_dir(root, table)
    if not table_dir.is_dir():
        return
    for directory in sorted(table_dir.iterdir()):
        if not directory.is_dir():
            continue
        moment = paths._partition_date(directory)
        if moment is not None:
            yield moment, directory


def _packed_name(directory: Path) -> str:
    """아직 쓰이지 않은 합친-파일 이름. 재실행이 이전 결과를 덮지 않게 한다."""
    index = 0
    while (directory / f"{PACKED_PREFIX}{index:04d}{paths.PARQUET_SUFFIX}").exists():
        index += 1
    return f"{PACKED_PREFIX}{index:04d}{paths.PARQUET_SUFFIX}"


def compact_partition(
    root: Path, table: str, directory: Path, moment: date, *, apply: bool
) -> PartitionResult:
    spec = get_spec(table)
    sources = sorted(directory.glob(f"*{paths.PARQUET_SUFFIX}"))
    bytes_before = sum(path.stat().st_size for path in sources)

    if len(sources) <= 1:
        return PartitionResult(
            partition=moment,
            files_before=len(sources),
            files_after=len(sources),
            rows=0,
            bytes_before=bytes_before,
            bytes_after=bytes_before,
            skipped="이미 한 파일",
        )

    if not apply:
        # **세보기는 파일을 열지 않는다.** 지문을 내려면 전부 읽어야 하는데,
        # 합치려는 이유가 바로 그 읽기가 비싸다는 것이다. 세보기가 본 작업만큼
        # 비싸면 아무도 세보지 않고 바로 --apply 를 건다.
        return PartitionResult(
            partition=moment,
            files_before=len(sources),
            files_after=1,
            rows=0,
            bytes_before=bytes_before,
            bytes_after=0,
            skipped=None,
        )

    merged = pq.read_table(sources, schema=spec.arrow_schema)
    rows, digest = _fingerprint(merged)

    temporary = directory / f"{PACKED_PREFIX}{TEMP_SUFFIX}"
    pq.write_table(merged, temporary, compression="zstd")

    # 디스크에 실제로 쓰인 것을 **다시 읽어** 검증한다. 메모리의 테이블을
    # 다시 재면 쓰기가 잘못돼도 통과한다.
    verify_rows, verify_digest = _fingerprint(pq.read_table(temporary))
    if (verify_rows, verify_digest) != (rows, digest):
        temporary.unlink(missing_ok=True)
        return PartitionResult(
            partition=moment,
            files_before=len(sources),
            files_after=len(sources),
            rows=rows,
            bytes_before=bytes_before,
            bytes_after=bytes_before,
            skipped=f"지문 불일치({verify_rows}행 vs {rows}행) — 건드리지 않았다",
        )

    # 합친 파일을 **먼저** 제자리에 놓고, 그다음에 원본을 지운다.
    # 순서가 반대면 그 사이에 죽었을 때 행이 사라진다 (모듈 docstring).
    target = directory / _packed_name(directory)
    os.replace(temporary, target)
    for path in sources:
        path.unlink(missing_ok=True)

    return PartitionResult(
        partition=moment,
        files_before=len(sources),
        files_after=1,
        rows=rows,
        bytes_before=bytes_before,
        bytes_after=target.stat().st_size,
    )


def compact_table(
    root: Path,
    table: str,
    *,
    apply: bool = False,
    limit: int | None = None,
    on_progress: object = None,
) -> CompactReport:
    """테이블 하나를 파티션마다 한 파일로 합친다.

    ``apply=False`` 면 아무것도 쓰지 않고 무엇을 하게 될지만 센다.
    """
    get_spec(table)  # 모르는 테이블이면 여기서 멈춘다
    results: list[PartitionResult] = []
    for index, (moment, directory) in enumerate(_partition_dirs(root, table)):
        if limit is not None and index >= limit:
            break
        result = compact_partition(root, table, directory, moment, apply=apply)
        results.append(result)
        if callable(on_progress):
            on_progress(result)
    return CompactReport(table=table, partitions=tuple(results))


def rewrite_manifests(root: Path, table: str, *, apply: bool = False) -> int:
    """매니페스트의 ``files`` 를 실제로 남아 있는 파일로 고쳐 쓴다.

    매니페스트는 적재 멱등성에만 쓰이고(``run_recorded`` 는 파일 존재만 본다)
    ``files`` 목록을 읽는 코드는 없다. 그래도 고쳐 둔다 — 지워진 경로를
    가리키는 기록은, 나중에 그것을 믿고 읽는 코드가 생기는 순간 조용히 틀린다.

    합친 뒤에는 어느 적재가 어느 파일에 들어갔는지가 파일 단위로는 남지 않는다.
    그 출처는 행의 ``ingest_run_id`` 컬럼에 그대로 있다. 그래서 여기서는
    "그 적재의 행들은 이 파티션의 이 파일들 안에 있다" 로 고쳐 적는다.
    """
    directory = root / paths.CURATED / paths.MANIFESTS / table
    if not directory.is_dir():
        return 0

    changed = 0
    for manifest in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        listed = payload.get("files")
        if not isinstance(listed, list):
            continue

        replacement: list[str] = []
        for entry in listed:
            path = root / str(entry)
            if path.exists():
                replacement.append(str(entry))
                continue
            # 그 파티션에서 살아남은 파일로 갈음한다.
            for survivor in sorted(path.parent.glob(f"*{paths.PARQUET_SUFFIX}")):
                relative = str(survivor.relative_to(root))
                if relative not in replacement:
                    replacement.append(relative)

        if replacement != listed:
            changed += 1
            if apply:
                payload["files"] = replacement
                manifest.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
                    encoding="utf-8",
                )
    return changed
