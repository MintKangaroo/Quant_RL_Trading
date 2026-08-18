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


#: 디렉터리 → (그때의 mtime_ns, 정렬된 항목들). 파일 나열 결과를 기억한다.
#:
#: **무효화 조건: 디렉터리의 mtime_ns 가 바뀌면 버린다.** 파티션에 파일을
#: 하나 넣거나 지우면 그 디렉터리의 mtime 이 바뀌고, 새 파티션이 생기면
#: 테이블 디렉터리의 mtime 이 바뀐다. 그래서 늦게 도착한 입력은 다음 조회에서
#: 보인다 — 이 저장소가 여러 번 낸 "캐시가 새 데이터를 영영 못 받는" 사고의
#: 반대편에 서 있다.
#:
#: **남는 경합 하나는 숨기지 않는다.** 나열하는 도중이 아니라 나열이 끝난 뒤,
#: 같은 mtime 눈금(리눅스에서 보통 1~4ms) 안에 파일이 하나 들어오면 mtime 이
#: 우리가 적어 둔 값과 같아서 무효화가 안 걸린다. 그래서 나열 전후로 mtime 을
#: 두 번 재서 **나열 중에 바뀐 경우는 아예 기억하지 않고**, 그보다 짧은 창은
#: 남겨 둔다. 그 창에 걸려도 **그 파티션에 다음 파일이 들어오는 순간 풀린다** —
#: 수집은 한 파티션에 여러 파일을 쓰므로 영영 못 보는 상태가 되지 않는다.
_LISTING_CACHE: dict[Path, tuple[int, tuple[Path, ...]]] = {}


def _listing(directory: Path, *, dirs_only: bool) -> tuple[Path, ...]:
    """디렉터리 안을 정렬해 돌려준다.

    나열 자체가 조회의 고정비다 — 파티션 1,500개짜리 표를 한 요청이 스무 번
    조회하면 readdir 3만 번이다(실측 ``/api/market`` 응답의 0.71초). stat 한
    번으로 갈음할 수 있으면 그렇게 한다.
    """
    try:
        before = directory.stat().st_mtime_ns
    except OSError:
        return ()
    cached = _LISTING_CACHE.get(directory)
    if cached is not None and cached[0] == before:
        return cached[1]

    if dirs_only:
        entries = tuple(sorted(item for item in directory.iterdir() if item.is_dir()))
    else:
        entries = tuple(sorted(directory.glob(f"*{PARQUET_SUFFIX}")))

    try:
        after = directory.stat().st_mtime_ns
    except OSError:
        return entries
    if after == before:
        _LISTING_CACHE[directory] = (before, entries)
    else:
        # 나열하는 사이에 누가 썼다. 이 목록은 반쪽일 수 있으므로 안 남긴다.
        _LISTING_CACHE.pop(directory, None)
    return entries


def forget_listings() -> None:
    """나열 캐시를 통째로 버린다. 테스트가 tmp 경로를 갈아 끼울 때 쓴다."""
    _LISTING_CACHE.clear()


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
    for directory in _listing(table_dir, dirs_only=True):
        moment = _partition_date(directory)
        if moment is None or moment > upper:
            continue
        if lower is not None and moment < lower:
            continue
        yield from _listing(directory, dirs_only=False)


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
