#!/usr/bin/env python
"""종목별 프로그램매매 하루치 — LS t1636. **장 마감 직후 매일.** 과거는 못 받는다.

    .venv/bin/python tools/collect_program_ls.py [--dry-run]

장중(09:00~15:30)에 부르면 미완성 값이라 **적지 않는다**. 실행 id 가 세션 날짜라 같은 날 두 번 돌려도
한 번만 적재된다. 관측 시각은 실제 수집 시각이다(collectors/ls_program.py).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.collectors import ls_program as program  # noqa: E402
from quant_rl_trading.collectors.ls_client import LSClient, LSCredentials  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from tools.collect_indices_ls import completed_session, session_timestamp  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="data")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    load_env()
    clock = LiveClock()
    now = clock.now()
    day = completed_session(now)
    if day is None:
        print("장중이다 — 미완성 값이라 적지 않는다")
        return 0
    store = Store(root=Path(args.root))
    if not args.dry_run and store.ingest_run_recorded(program.TABLE, program.run_id(day)):
        print(f"{day}: 이미 받았다")
        return 0
    client = LSClient(credentials=LSCredentials.from_env(prefix="LS_"), clock=clock,
                      live_trading=True, min_interval_sec=1.1)
    rows = []
    for gubun, board in program.BOARDS.items():
        items = program.fetch_board(client, gubun)
        rows += program.normalize(items, board=board, valid_from=session_timestamp(day), observed_at=clock.now())
        print(f"  {board}: {len(items):,}종목", flush=True)
    if not rows:
        print("한 행도 못 받았다", file=sys.stderr)
        return 1
    active = sum(1 for r in rows if (r["buy_value"] or 0) + (r["sell_value"] or 0) > 0)
    print(f"{day}: {len(rows):,}행 · 프로그램 거래가 있던 종목 {active:,}")
    if not args.dry_run:
        store.append(program.TABLE, rows, ingest_run_id=program.run_id(day), source=program.SOURCE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
