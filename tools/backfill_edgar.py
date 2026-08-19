#!/usr/bin/env python
"""SEC EDGAR 공시 백필 (#38).

    uv run python tools/backfill_edgar.py --start 2026-08-01 --end 2026-08-18
    uv run python tools/backfill_edgar.py --start 2021-08-01 --end 2026-08-18 --forms "8-K"

## 왜 백필이 중요한가

"과거 뉴스 데이터가 없어서 뉴스는 필터로만 쓴다" 는 결정이 있었다. EDGAR 는
2001년 이후가 검색되므로 **5년 백필이 된다** — 그러면 미장 `news` 를 필터가
아니라 IC 측정 대상으로 올릴 수 있다.

## 중단해도 된다

날짜별로 `ingest_run` 이력을 남기므로 다시 돌리면 안 받은 날만 받는다.
5년이면 1,800일이고 하루 3~5초라 두세 시간짜리 작업이다 — **중단은 예외가
아니라 기본값이다.**

## 폼을 늘리려면 --forms 를 바꾼다

이력 키에 폼이 들어가므로, 8-K 만 받은 날에 10-K 를 추가로 받아도 건너뛰지
않는다. 다만 같은 폼 묶음 문자열을 써야 이력이 맞는다 — "8-K,10-K" 와
"10-K,8-K" 는 다른 키다.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.collectors.edgar_filings import (  # noqa: E402
    USER_AGENT_ENV,
    EdgarBackfiller,
    EdgarPolicy,
    EdgarSource,
)
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--forms", default="8-K")
    parser.add_argument("--root", default="data")
    parser.add_argument("--limit", type=int, default=None, help="이 날짜 수만 처리")
    args = parser.parse_args(argv)

    load_env()
    user_agent = os.environ.get(USER_AGENT_ENV, "")
    if not user_agent:
        print(
            f"{USER_AGENT_ENV} 가 없다. SEC 는 키를 요구하지 않지만 **연락 가능한\n"
            f"신원**을 요구한다. .env 에 다음처럼 넣어라:\n"
            f'  {USER_AGENT_ENV}="ProjectName research your@email.com"',
            file=sys.stderr,
        )
        return 2

    store = Store(root=Path(args.root))
    backfiller = EdgarBackfiller(
        store=store,
        source=EdgarSource(user_agent=user_agent),
        policy=EdgarPolicy(clock=LiveClock()),
        forms=args.forms,
    )
    days = backfiller.plan(
        date.fromisoformat(args.start), date.fromisoformat(args.end)
    )
    if args.limit:
        days = days[: args.limit]

    print(f"EDGAR {args.forms} · {len(days)}일 ({args.start} ~ {args.end})", flush=True)
    rows = skipped = deferred = errors = empty = 0
    for index, result in enumerate(backfiller.run(days), start=1):
        if result.skipped:
            skipped += 1
        elif result.error:
            errors += 1
            print(f"  ! {result.day} {result.error}", flush=True)
        elif result.deferred:
            deferred += 1
        elif result.rows == 0:
            empty += 1
        else:
            rows += result.rows
        if index % 10 == 0 or index == len(days):
            print(
                f"[{index}/{len(days)}] {result.day} · 누적 {rows:,}행 · "
                f"건너뜀 {skipped} · 빈날 {empty} · 보류 {deferred} · 오류 {errors}",
                flush=True,
            )

    print(
        f"완료 — {rows:,}행 적재 · 건너뜀 {skipped} · 빈날 {empty} · "
        f"보류 {deferred} · 오류 {errors}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
