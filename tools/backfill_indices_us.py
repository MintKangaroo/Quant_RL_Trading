"""미장 지수 백필 — FRED 일별 종가.

    uv run python tools/backfill_indices_us.py                 # 창고 시세 범위(설정)
    uv run python tools/backfill_indices_us.py --start 2021-01-01
    uv run python tools/backfill_indices_us.py --series NASDAQCOM --dry-run

``collect_macro.py`` 의 ``IndexCollector`` 는 매일 최근 400 관측을 받는 **라이브**
경로다. 과거 5년을 그 경로로 채우려면 limit 을 키워야 하는데, limit 은 호출
시점에 따라 구간 끝이 달라져 재현이 안 된다. 백필은 날짜로 자른다.

``ingest_run_id`` 를 (시리즈, 연도)로 결정론적으로 만든다. 중단하고 다시 돌리면
이미 넣은 연도는 창고가 거부하고 넘어간다 — 라이브 경로의 시각 기반 run_id 와
달리 같은 구간을 두 번 넣지 않는다.

**받는 것은 가격지수(PR)다. 총수익지수가 아니다** — 배당이 빠져 있어 벤치마크로
쓰면 배당수익률만큼 우리에게 유리하게 나온다.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.collectors.errors import CollectorError  # noqa: E402
from quant_rl_trading.collectors.macro_source import (  # noqa: E402
    FRED_INDICES,
    FredSource,
    index_rows,
)
from quant_rl_trading.collectors.raw import RawArchive  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.store import DuplicateIngestRun  # noqa: E402
from tools.backfill import build_store, load_env  # noqa: E402

TABLE = "indices"


def run_id_for(series_id: str, year: int) -> str:
    """연도 축 결정론 run_id. 재개가 이것으로 성립한다."""
    return f"bf-indices-US-{series_id}-{year}"


def backfill(
    store,
    source: FredSource,
    clock,
    *,
    start: date,
    end: date,
    series: list[str],
    dry_run: bool,
) -> int:
    observed_at = clock.now().astimezone(UTC)
    archive = RawArchive(root=store.root)
    total = 0

    for series_id in series:
        entity_id, label = FRED_INDICES[series_id]
        for year in range(start.year, end.year + 1):
            window_start = max(start, date(year, 1, 1))
            window_end = min(end, date(year, 12, 31))
            if window_start > window_end:
                continue
            run_id = run_id_for(series_id, year)
            if store.ingest_run_recorded(TABLE, run_id):
                print(f"  {entity_id} {year} — 이미 적재")
                continue
            if dry_run:
                print(f"  {entity_id} {year} — 받을 구간 {window_start}~{window_end}")
                continue

            try:
                observations = source.observations(
                    series_id, start=window_start, end=window_end
                )
            except CollectorError as error:
                print(f"  {entity_id} {year} — 실패: {error}", file=sys.stderr)
                continue

            rows = index_rows(series_id, observations, observed_at=observed_at)
            if not rows:
                # 빈 것을 완료로 기록하지 않는다. 나중에 데이터가 생겨도
                # 영영 건너뛰게 된다 (panels.py 와 같은 이유).
                print(f"  {entity_id} {year} — 0행")
                continue

            archive.save(
                source.name, {series_id: observations}, observed_at=observed_at,
                ingest_run_id=run_id, label=f"idx-US-{series_id}-{year}",
            )
            try:
                written = int(store.append(TABLE, rows, ingest_run_id=run_id))
            except DuplicateIngestRun:
                written = 0
            total += written
            print(f"  {entity_id} ({label}) {year} — {written}행")

    return total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--start", help="ISO 날짜. 생략하면 store.config 의 backfill.years")
    parser.add_argument("--end", help="ISO 날짜. 생략하면 어제")
    parser.add_argument(
        "--series", action="append", choices=sorted(FRED_INDICES),
        help="여러 번 줄 수 있다. 생략하면 전부",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    load_env()
    store = build_store(args.data_root)
    clock = LiveClock()
    now = clock.now()

    source = FredSource.from_env()
    if not source.usable():
        print("FRED_API_KEY 가 없다.", file=sys.stderr)
        return 2

    yesterday = now.astimezone(UTC).date() - timedelta(days=1)
    end = date.fromisoformat(args.end) if args.end else yesterday
    if args.start:
        start = date.fromisoformat(args.start)
    else:
        years = int(store.config("backfill.years", as_of=now))
        start = end - timedelta(days=365 * years)

    series = args.series or sorted(FRED_INDICES)
    print(f"US 지수 백필 {start} ~ {end} — {', '.join(series)}")
    total = backfill(
        store, source, clock, start=start, end=end, series=series, dry_run=args.dry_run
    )
    print(f"indices 적재: {total}행 (US · 가격지수 — 배당 미반영)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
