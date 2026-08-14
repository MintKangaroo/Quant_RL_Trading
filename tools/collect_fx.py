"""원/달러 환율 수집 — FRED DEXKOUS.

    uv run python tools/collect_fx.py --start 2021-08-01 --end 2026-08-13

**회계의 전제다.** 환율이 없으면 `accounting` 이 NAV 계산을 거부하고(그게
옳다), 그러면 백테스트도 shadow 도 한 줄도 못 나간다. 창고에 `fx` 가 0행이던
동안 회계는 테스트 위에서만 돌고 있었다.

라이브에서는 하루 한 번 어제까지를 다시 받으면 된다 — 같은 구간 run_id 는
결정론적이라 창고가 중복을 거부한다.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.collectors.macro_source import FxCollector, FredSource  # noqa: E402
from quant_rl_trading.collectors.raw import RawArchive  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from tools.backfill import build_store, load_env  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--start", help="시작일 (기본: 30일 전)")
    parser.add_argument("--end", help="종료일 (기본: 어제)")
    args = parser.parse_args(argv)

    load_env()
    store = build_store(args.data_root)
    clock = LiveClock()
    today = clock.now().date()

    end = date.fromisoformat(args.end) if args.end else today - timedelta(days=1)
    start = (
        date.fromisoformat(args.start) if args.start else end - timedelta(days=30)
    )

    source = FredSource.from_env()
    if not source.usable():
        print("FRED_API_KEY 가 없다. 환율 없이는 NAV 를 계산할 수 없다.", file=sys.stderr)
        return 2

    written = FxCollector(
        store=store,
        source=source,
        clock=clock,
        archive=RawArchive(store.root),
        start=start,
        end=end,
    ).collect()
    print(f"fx {written}행 ({start} ~ {end})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
