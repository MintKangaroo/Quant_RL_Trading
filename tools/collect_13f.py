#!/usr/bin/env python
"""13F 수집 — 기관 분기 보유 명세를 창고에 넣는다.

    uv run python tools/collect_13f.py --dry-run
    uv run python tools/collect_13f.py
    uv run python tools/collect_13f.py --cik 1067983 --quarters 4

## 주기

**분기에 한 번이면 충분하다.** 13F 는 분기말 기준이고 마감이 45일이라,
2·5·8·11월 중순 이후에 한 번 돌리면 그 분기가 다 들어온다. 매일 돌리는
것은 같은 파일을 다시 받는 낭비다 — 크론 예시는 아래.

    # 분기 마감 뒤 (2·5·8·11월 20일 09:30 KST)
    30 9 20 2,5,8,11 * cd <repo> && .venv/bin/python tools/collect_13f.py >> logs/13f.log 2>&1

## 종료코드

    0  적재했다
    1  한 기관도 못 받았다 — 실패다
    2  전부 이미 적재돼 있다 (정상)

**0행을 성공으로 적지 않는다.** 이 저장소는 rc=0 에 0행인 수집이 며칠씩
조용히 도는 사고를 여러 번 냈다.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.collectors.thirteen_f import (  # noqa: E402
    DEFAULT_FILERS,
    MIN_INTERVAL_SEC,
    TABLE,
    EdgarClient,
    ThirteenFError,
    fetch_filing,
    ingest_run_id,
    lag_days,
    recent_filings,
    to_rows,
)
from quant_rl_trading.settings import load_env  # noqa: E402
from tools.backfill import build_store  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cik", action="append", help="이 CIK 만 (여러 번 가능)")
    parser.add_argument("--quarters", type=int, default=2, help="기관당 최근 몇 분기")
    parser.add_argument("--dry-run", action="store_true", help="받아서 보여만 준다")
    parser.add_argument("--data-root", type=Path)
    args = parser.parse_args(argv)

    load_env()
    user_agent = os.environ.get("SEC_EDGAR_USER_AGENT", "")
    store = build_store(args.data_root)
    client = EdgarClient(user_agent)

    filers = (
        {cik: DEFAULT_FILERS.get(cik, cik) for cik in args.cik}
        if args.cik else dict(DEFAULT_FILERS)
    )

    written_total = 0
    skipped = 0
    failed: list[str] = []

    for cik, name in filers.items():
        try:
            metas = recent_filings(client.submissions(cik), limit=args.quarters)
        except ThirteenFError as exc:
            print(f"  ✗ {name}: {exc}")
            failed.append(name)
            continue
        if not metas:
            # **"안 냈다" 와 "못 받았다" 는 다르다.** 여기는 200 을 받고
            # 13F-HR 이 없는 경우 — 진짜로 안 낸 것이다.
            print(f"  · {name}: 13F-HR 신고가 없다")
            continue

        for meta in metas:
            run_id = ingest_run_id(cik, meta["report_date"])
            if store.ingest_run_recorded(TABLE, run_id):
                skipped += 1
                continue
            time.sleep(MIN_INTERVAL_SEC)
            try:
                filing = fetch_filing(client, cik, name, meta)
            except ThirteenFError as exc:
                print(f"  ✗ {name} {meta['report_date']}: {exc}")
                failed.append(f"{name} {meta['report_date']}")
                continue

            rows = to_rows(filing)
            folded = sum(int(h.rows) for h in filing.holdings)
            print(
                f"  {name} {filing.report_date} · {len(filing.holdings)}종목"
                f"(원본 {folded}줄) · ${filing.total_usd / 1e9:,.1f}십억"
                f" · 공개까지 {lag_days(filing)}일"
            )
            for holding in filing.holdings[:5]:
                share = holding.value_usd / filing.total_usd * 100
                print(f"      {holding.issuer[:30]:<32}{share:>6.1f}%  ${holding.value_usd/1e9:>7.1f}십억")

            if args.dry_run or not rows:
                continue
            written_total += store.append(TABLE, rows, ingest_run_id=run_id)

    if args.dry_run:
        print("\n--dry-run 이라 적재하지 않았다.")
        return 0
    print(f"\n적재 {written_total}행 · 이미 있던 신고 {skipped}건 · 실패 {len(failed)}건")
    if failed:
        for item in failed:
            print(f"  실패: {item}")
    if written_total:
        return 0
    if skipped and not failed:
        print("전부 이미 적재돼 있다 — 새 분기가 나오면 들어온다.")
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
