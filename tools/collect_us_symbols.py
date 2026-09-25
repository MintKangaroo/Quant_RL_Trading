"""미장 증권 종류 스냅샷 → `instrument_types`. collectors/us_symbols.py 참조.

    .venv/bin/python tools/collect_us_symbols.py [--dry-run]

하루 한 번(미장 수집 단계). valid_from = 수집일 00:00 UTC, observed_at = 수집 시각. 실행 id 가 날짜라 같은 날 두 번 받아도 한 번만 적힌다.
두 파일 중 하나라도 비거나 머리가 바뀌면 **적지 않고 rc=1** — 반쪽 스냅샷이 필터를 잘못 돌리면 멀쩡한 종목이 명단에서 빠진다.
"""

from __future__ import annotations

import argparse
import collections
import sys
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import httpx  # noqa: E402

from quant_rl_trading.collectors import us_symbols  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402

#: 두 파일을 합쳐 이보다 적으면 소스 사고로 본다(2026-09-25 실측 13,283).
MIN_SYMBOLS = 8000


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default="data")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    now = LiveClock().now()
    parts = []
    with httpx.Client(timeout=60, headers={"User-Agent": "Mozilla/5.0 quant-rl-trading"}) as client:
        for url in us_symbols.URLS:
            response = client.get(url)
            response.raise_for_status()
            parts.append(us_symbols.parse(response.text))
    symbols = us_symbols.merge(parts)
    if any(len(p) == 0 for p in parts) or len(symbols) < MIN_SYMBOLS:
        print(f"심볼 {len(symbols):,}개(파일별 {[len(p) for p in parts]}) — 비정상, 적지 않는다", file=sys.stderr)
        return 1
    day = now.astimezone(ZoneInfo("UTC")).date()
    valid_from = datetime.combine(day, time(0), tzinfo=ZoneInfo("UTC"))
    rows = [{"entity_id": f"US:{s.ticker}", "valid_from": valid_from, "observed_at": now, "source": us_symbols.SOURCE,
             "market": "US", "name": s.name, "instrument": s.instrument, "test_issue": s.test_issue}
            for s in symbols.values()]
    counts = collections.Counter(s.instrument for s in symbols.values())
    print(f"{day} 심볼 {len(rows):,}개 · " + " · ".join(f"{k} {v:,}" for k, v in counts.most_common()), flush=True)
    if not args.dry_run:
        Store(root=Path(args.root)).append(us_symbols.TABLE, rows, ingest_run_id=f"us-symbols-{day:%Y%m%d}",
                                           source=us_symbols.SOURCE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
