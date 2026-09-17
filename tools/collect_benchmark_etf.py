"""종료 판정 대조군(KODEX200)과 기초지수(K200)를 `indices` 에 적는다.

    .venv/bin/python tools/collect_benchmark_etf.py                    # 최근 10일
    .venv/bin/python tools/collect_benchmark_etf.py --from 2026-08-20  # 백필
    .venv/bin/python tools/collect_benchmark_etf.py --dry-run

`milestones.md` 종료 기준의 대조는 KODEX200 이다. 2026-09-18 확인 시점에 창고에 **0행**
이었다 — 판정일에 계산이 불가능한 상태였다. ETF 는 유니버스에 없어 일상 수집 어디에도
안 걸린다. 종료코드: 0 적재(또는 이미 있음) · 1 소스 실패.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.collectors import benchmark_etf as bench  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market, local_time  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import DuplicateIngestRun  # noqa: E402
from tools.backfill import build_store  # noqa: E402


def fetch(start: date, end: date):
    from pykrx import stock

    return stock.get_etf_ohlcv_by_date(
        start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), bench.TICKER
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="start", help="시작일 (기본: 10일 전)")
    parser.add_argument("--to", dest="end", help="끝일 (기본: 오늘)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    load_env()
    clock = LiveClock()
    now = clock.now()
    today = local_time(Market.KR, now).date()
    end = date.fromisoformat(args.end) if args.end else today
    start = date.fromisoformat(args.start) if args.start else end - timedelta(days=10)
    if start > end:
        print(f"시작일이 끝일보다 늦다: {start} > {end}", file=sys.stderr)
        return 1

    try:
        frame = fetch(start, end)
    except Exception as error:  # noqa: BLE001 — 외부 소스 경계
        print(f"KRX ETF 조회 실패 ({type(error).__name__}) — 적재 없음", file=sys.stderr)
        return 1
    if frame is None or frame.empty:
        print(f"KRX 가 {start}~{end} 에 빈 표를 줬다 — 적재 없음", file=sys.stderr)
        return 1

    rows = bench.rows_from_frame(frame, observed_at=now)
    if not rows:
        print(f"{start}~{end}: 종가 있는 세션이 없다", file=sys.stderr)
        return 1
    etf = sum(1 for r in rows if r["entity_id"] == bench.BENCHMARK_ETF)
    print(f"{start} ~ {end} · ETF {etf}세션 · 기초지수 {len(rows) - etf}세션")
    if args.dry_run:
        for row in rows[-4:]:
            print(f"  {row['entity_id']} {row['valid_from'].date()} close={row['close']:,.2f}")
        return 0

    store = build_store(None)
    try:
        written = store.append(
            bench.TABLE, rows, ingest_run_id=bench.run_id(start, end), source=bench.SOURCE
        )
    except DuplicateIngestRun:
        print("같은 구간을 이미 받았다 — 할 일 없음")
        return 0
    print(f"{written}행 적재")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
