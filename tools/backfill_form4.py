#!/usr/bin/env python
"""SEC Form 4 분기 벌크 적재 — 6차 G7(미장 내부자) 재료.

    .venv/bin/python tools/backfill_form4.py --start 2024q1 --end 2026q2
    .venv/bin/python tools/backfill_form4.py --start 2025q2 --end 2025q2 --dry-run

분기마다 ZIP 하나(약 10MB)를 받아 `form4_trades` 에 넣는다. 실행 id 가 분기라서
**다시 돌리면 이미 받은 분기는 건너뛴다.** 아직 안 나온 분기(404)는 실패가 아니라
대기다 — 분기가 끝나고 몇 주 뒤에 나온다.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.collectors import sec_form4 as form4  # noqa: E402
from quant_rl_trading.collectors.edgar_filings import (  # noqa: E402
    USER_AGENT_ENV,
    EdgarPolicy,
    EdgarSource,
    NotYetKnown,
)
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, help="예: 2024q1")
    parser.add_argument("--end", required=True, help="예: 2026q2")
    parser.add_argument("--root", default="data")
    parser.add_argument("--dry-run", action="store_true", help="받고 세기만, 적재 안 함")
    args = parser.parse_args(argv)

    load_env()
    user_agent = os.environ.get(USER_AGENT_ENV, "")
    if "@" not in user_agent:
        print(f"{USER_AGENT_ENV} 에 연락 가능한 이메일이 있어야 한다(SEC 신원 규칙).", file=sys.stderr)
        return 2
    store = Store(root=Path(args.root))
    policy = EdgarPolicy(clock=LiveClock())
    tickers = EdgarSource(user_agent=user_agent).tickers()
    print(f"CIK→티커 {len(tickers):,}개", flush=True)

    failed = 0
    for year, quarter in form4.quarters(args.start, args.end):
        run_id = form4.run_id(year, quarter)
        if not args.dry_run and store.ingest_run_recorded(form4.TABLE, run_id):
            print(f"{year}q{quarter}: 이미 받았다 — 건너뛴다", flush=True)
            continue
        try:
            content = form4.fetch(year, quarter, user_agent=user_agent)
        except NotYetKnown as error:
            print(f"{year}q{quarter}: {error} — 대기", flush=True)
            continue
        except Exception as error:  # noqa: BLE001 — 분기 하나가 죽어도 나머지는 받는다, rc 로 알린다
            print(f"{year}q{quarter}: 받기 실패 {error}", file=sys.stderr, flush=True)
            failed += 1
            continue
        frame = form4.parse(content)
        rows, stats = form4.to_rows(frame, tickers=tickers, policy=policy)
        codes = frame["TRANS_CODE"].value_counts().head(6).to_dict()
        print(f"{year}q{quarter}: Form 4 비파생 {len(frame):,}행 → 적재 {stats['rows']:,} · 티커 없음 {stats['no_symbol']:,} "
              f"· 날짜 불량 {stats['bad_date']} · 아직 모름 {stats['not_yet']} · 코드 {codes}", flush=True)
        if not args.dry_run and rows:
            store.append(form4.TABLE, rows, ingest_run_id=run_id, source=form4.SOURCE)
    # **실패는 rc 로 낸다** — 셸의 echo 가 조용한 실패를 삼키지 않게(silent-failure 교훈).
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
