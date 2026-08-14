"""파티션 조각 파일 합치기 — 읽기 비용은 데이터 양이 아니라 파일 개수에 붙는다.

    uv run python tools/compact_store.py --table flows              # 세보기만
    uv run python tools/compact_store.py --table flows --apply      # 실제로 합침

``--apply`` 없이는 한 바이트도 쓰지 않는다.

**창고를 읽는 작업이 도는 동안 --apply 를 걸지 마라.** 합치기는 파일을
지운다. 백테스트·IC 측정이 그 파일을 여는 중이면 그쪽이 죽는다.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from quant_rl_trading.store import Store
from quant_rl_trading.store.compact import PartitionResult, compact_table, rewrite_manifests


def _mb(value: int) -> str:
    return f"{value / 1_048_576:,.1f}MB"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table", required=True, help="합칠 테이블 이름")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="실제로 합친다. 없으면 무엇을 하게 될지만 센다",
    )
    parser.add_argument("--limit", type=int, default=None, help="앞에서 N개 파티션만")
    parser.add_argument(
        "--data-root", default=None, help="창고 경로 (기본: 환경변수 설정)"
    )
    args = parser.parse_args()

    root = Path(args.data_root) if args.data_root else Store().root
    print(f"창고 {root} · 테이블 {args.table}")
    print("모드:", "합친다 (--apply)" if args.apply else "세보기만 — 아무것도 쓰지 않는다")

    started = time.perf_counter()
    seen = 0

    def progress(result: PartitionResult) -> None:
        nonlocal seen
        seen += 1
        if seen % 100 == 0:
            elapsed = time.perf_counter() - started
            print(f"  … {seen} 파티션 ({elapsed:,.0f}s)", flush=True)

    report = compact_table(
        root, args.table, apply=args.apply, limit=args.limit, on_progress=progress
    )

    problems = [item for item in report.partitions if item.skipped and "이미" not in item.skipped]
    print()
    print(f"파티션 {len(report.partitions):,}개 · 손댄 것 {len(report.touched):,}개")
    print(f"파일   {report.files_before:,} → {report.files_after:,}")
    if args.apply:
        print(f"크기   {_mb(report.bytes_before)} → {_mb(report.bytes_after)}")
        print(f"행     {report.rows:,}")
        changed = rewrite_manifests(root, args.table, apply=True)
        print(f"매니페스트 {changed:,}개를 실제 파일에 맞춰 고쳤다")
    else:
        print(f"크기   {_mb(report.bytes_before)} (합친 뒤 크기는 실제로 써 봐야 안다)")
        print("행     — 세보기는 파일을 열지 않는다")

    if problems:
        print()
        print(f"!! 건드리지 않은 파티션 {len(problems)}개 — 지문이 안 맞았다:")
        for item in problems[:20]:
            print(f"   {item.partition}: {item.skipped}")
        return 1

    print(f"\n{time.perf_counter() - started:,.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
