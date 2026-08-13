"""data/ 경로와 파티션 규칙.

레포에서 Parquet 경로 문자열이 등장해도 되는 유일한 파일이다.
(store/ 밖에서 쓰면 tools/invariant_guard.py 가 잡는다.)

파티션 키는 **observed_at 의 UTC 날짜**다. 게이트의 핵심 술어가
``observed_at <= as_of`` 이므로, 이래야 미래 파티션을 파일을 열기 전에 잘라낸다.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

PARQUET_SUFFIX = ".parquet"
PARTITION_KEY = "observed_date"
CURATED = "curated"
MANIFESTS = "_manifests"
STAGING = "_staging"


def curated_dir(root: Path, table: str) -> Path:
    return root / CURATED / table


def partition_dir(root: Path, table: str, observed: date) -> Path:
    return curated_dir(root, table) / f"{PARTITION_KEY}={observed.isoformat()}"


def data_file(root: Path, table: str, observed: date, ingest_run_id: str) -> Path:
    return partition_dir(root, table, observed) / f"{ingest_run_id}{PARQUET_SUFFIX}"


def manifest_path(root: Path, table: str, ingest_run_id: str) -> Path:
    return root / CURATED / MANIFESTS / table / f"{ingest_run_id}.json"


def staging_dir(root: Path) -> Path:
    return root / CURATED / STAGING


def observed_date(moment: datetime) -> date:
    """파티션이 걸리는 날짜. 항상 UTC 기준으로 자른다.

    지역 시각으로 자르면 서머타임 전환일에 파티션이 겹치거나 빈다.
    """
    return moment.astimezone(UTC).date()


def _partition_date(directory: Path) -> date | None:
    prefix = f"{PARTITION_KEY}="
    if not directory.name.startswith(prefix):
        return None
    try:
        return date.fromisoformat(directory.name[len(prefix) :])
    except ValueError:
        return None


def iter_data_files(
    root: Path,
    table: str,
    *,
    upper: date,
    lower: date | None = None,
) -> Iterator[Path]:
    """``lower <= observed_date <= upper`` 인 파티션의 Parquet 파일.

    상한은 언제나 적용한다 — 이것이 미래 훔쳐보기를 구조로 막는 지점이다.
    하한은 호출자가 명시할 때만 적용한다. 잘못된 하한은 조용히 행을 지운다.
    """
    table_dir = curated_dir(root, table)
    if not table_dir.is_dir():
        return
    for directory in sorted(table_dir.iterdir()):
        if not directory.is_dir():
            continue
        moment = _partition_date(directory)
        if moment is None or moment > upper:
            continue
        if lower is not None and moment < lower:
            continue
        yield from sorted(directory.glob(f"*{PARQUET_SUFFIX}"))


def prune_lower_bound(
    valid_from_floor: date | None,
    observation_lag_days: int | None,
) -> date | None:
    """하한 프루닝 날짜. 테이블이 관측 지연을 선언한 경우에만 값이 나온다.

    ``valid_from`` 기준 창을 ``observed_at`` 파티션으로 옮기는 계산이라,
    선언된 지연만큼 더 과거로 넉넉히 연다.
    """
    if valid_from_floor is None or observation_lag_days is None:
        return None
    return valid_from_floor - timedelta(days=observation_lag_days)
