"""N6 백필 — 주식대차·의무보호예수 반환을 거래일 하나씩 (collectors/fsc_lending.py, data-contract.md §4-4).

    .venv/bin/python tools/backfill_fsc_lending.py --what lending --start 2021-08-02 --end 2026-10-13 --dry-run
    .venv/bin/python tools/backfill_fsc_lending.py --what lockup  --start 2021-08-02 --end 2026-10-13      # 장 마감 뒤, 사람이

append-only. 날마다 run id ``bf-<표>-KR-YYYYMMDD`` 매니페스트가 있으면 건너뛴다(재시작 안전). 한도(오류 22·23)면 rc 3 으로 멈춘다 —
다음 날 같은 명령으로 이어 간다. 키는 ``.env`` 의 ``DATA_GO_KR_SERVICE_KEY``(로그·출력에 남기지 않는다). 장중(08:30~15:50)은 거부한다.
"""
from __future__ import annotations

import argparse
import contextlib
import os
import sys
from datetime import date, time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.collectors import fsc_lending as F  # noqa: E402
from quant_rl_trading.collectors.errors import CollectorError  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market, trading_days  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import DuplicateIngestRun, Store  # noqa: E402

KINDS = {"lending": (F.PATH_LENDING, F.TABLE_LENDING, F.lending_rows),
         "lockup": (F.PATH_LOCKUP, F.TABLE_LOCKUP, F.lockup_rows)}


def run_id(table: str, day: date) -> str:
    return f"bf-{table}-KR-{day.strftime('%Y%m%d')}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--what", choices=sorted(KINDS), required=True)
    ap.add_argument("--start", type=date.fromisoformat, required=True)
    ap.add_argument("--end", type=date.fromisoformat, required=True)
    ap.add_argument("--root", default="data")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-calls", type=int, default=9000, help="이번 실행 콜 상한(일 한도 10,000 아래)")
    args = ap.parse_args(argv)
    now = LiveClock().now()
    local = now.astimezone(F.SEOUL).time()
    if not args.dry_run and now.astimezone(F.SEOUL).weekday() < 5 and time(8, 30) <= local < time(15, 50):
        print("장중 적재 거부(08:30~15:50) — --dry-run 만 된다")
        return 2
    load_env()
    key = os.environ.get(F.ENV_KEY, "")
    if not key:
        print(f"{F.ENV_KEY} 가 없다")
        return 2
    path, table, to_rows = KINDS[args.what]
    store = Store(root=Path(args.root))
    client = F.DataGoKrClient(service_key=key)
    days = [d for d in trading_days(Market.KR, args.start, args.end)]
    written = skipped = empty = 0
    for i, day in enumerate(days, 1):
        rid = run_id(table, day)
        if store.ingest_run_recorded(table, rid):
            skipped += 1
            continue
        if client.calls >= args.max_calls:
            print(f"콜 상한 {args.max_calls} — {day} 에서 멈춤(다음 실행이 이어 간다)")
            return 3
        try:
            rows = to_rows(client.day(path, day))
        except F.QuotaExceeded as error:
            print(f"{day}: {error} — 멈춤(rc 3)")
            return 3
        except CollectorError as error:
            print(f"{day}: {error} — 멈춤(rc 4)")
            return 4
        if not rows:
            empty += 1
            continue
        if args.dry_run:
            written += len(rows)
        else:
            with contextlib.suppress(DuplicateIngestRun):
                written += int(store.append(table, rows, ingest_run_id=rid))
        if i % 50 == 0:
            print(f"  {i}/{len(days)} {day} · 누적 {written:,}행 · 콜 {client.calls}", flush=True)
    print(f"끝 — {'(미리보기) ' if args.dry_run else ''}{written:,}행 · 건너뜀 {skipped} · 빈 날 {empty} · 콜 {client.calls}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
