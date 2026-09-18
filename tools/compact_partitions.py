"""파티션 압축 CLI — 본체는 `quant_rl_trading/store/compaction.py` 에 있다.

    .venv/bin/python tools/compact_partitions.py --all
    .venv/bin/python tools/compact_partitions.py --all --apply

**장 중·수집 중에는 돌리지 않는다.** 원본을 지우는 순간이 읽는 쪽과의 경합 지점이다.
종료코드: 0 정상 · 1 검증 실패로 건너뛴 파티션이 있다.
"""
from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.store import paths  # noqa: E402
from quant_rl_trading.store.compaction import candidates, compact_partition, connect  # noqa: E402

#: 기본 유예. 최근 파티션은 오늘도 쓰인다 — 합치면 쓰는 쪽과 부딪힌다.
DEFAULT_OLDER_THAN = 3


def _files(directory: Path) -> int:
    return len(list(directory.glob(f"*{paths.PARQUET_SUFFIX}")))


def _bytes(directory: Path) -> int:
    return sum(f.stat().st_size for f in directory.glob(f"*{paths.PARQUET_SUFFIX}"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="data")
    parser.add_argument("--table", action="append", default=[], help="여러 번 줄 수 있다")
    parser.add_argument("--all", action="store_true", help="창고의 모든 표 — 새 표가 생겨도 따라간다")
    parser.add_argument("--older-than-days", type=int, default=DEFAULT_OLDER_THAN)
    parser.add_argument("--limit", type=int, default=0, help="파티션 N 개만")
    parser.add_argument("--apply", action="store_true", help="실제로 합친다 (기본은 미리보기)")
    args = parser.parse_args(argv)

    root = Path(args.root)
    tables = list(args.table)
    if args.all:
        # **표를 손으로 적지 않는다.** 새 표가 생기면 그것만 조용히 파편화된 채 남는다.
        curated = root / paths.CURATED
        tables = sorted(
            d.name for d in curated.iterdir()
            if d.is_dir() and not d.name.startswith("_")
        )
    if not tables:
        parser.error("--table 또는 --all 이 필요하다")
    now = datetime.now(UTC)  # invariant-allow: wallclock — 파일 정리 유예 계산, 도메인 시각 아님
    cutoff = now.date() - timedelta(days=int(args.older_than_days))
    staging = root / paths.CURATED / paths.STAGING
    connection = connect()
    touched = skipped = 0

    for table in tables:
        found = candidates(root, table, cutoff=cutoff)
        if args.limit:
            found = found[: args.limit]
        files_before = sum(n for _, n, _ in found)
        bytes_before = sum(b for _, _, b in found)
        print(f"{table}: 대상 파티션 {len(found)}개 · 파일 {files_before:,}개 "
              f"· {bytes_before / 1e6:.1f}MB (기준일 {cutoff} 이전)", flush=True)
        if not args.apply or not found:
            continue
        for directory, _, _ in found:
            ok, reason = compact_partition(connection, directory, staging)
            if ok:
                touched += 1
            else:
                skipped += 1
                print(f"  {directory.name}: 건너뜀 — {reason}", file=sys.stderr)
        after_files = sum(_files(d) for d, _, _ in found)
        after_bytes = sum(_bytes(d) for d, _, _ in found)
        print(f"  → 파일 {files_before:,} → {after_files:,} · "
              f"{bytes_before / 1e6:.1f}MB → {after_bytes / 1e6:.1f}MB", flush=True)

    if args.apply:
        print(f"\n합친 파티션 {touched} · 건너뜀 {skipped}")
        return 1 if skipped else 0
    print("\n미리보기다. 실제로 합치려면 --apply. **장 중·수집 중에는 돌리지 않는다.**")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
