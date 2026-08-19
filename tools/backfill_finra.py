#!/usr/bin/env python
"""FINRA 공매도 백필 (#50).

    uv run python tools/backfill_finra.py --start 2026-08-01 --end 2026-08-18
    uv run python tools/backfill_finra.py --start 2021-08-19 --end 2026-08-18

하루가 파일 하나라 콜이 하루 1건이다. 5년이면 1,800콜 — EDGAR 보다 가볍다.
날짜별 이력을 남기므로 중단하고 다시 돌리면 안 받은 날만 받는다.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import httpx  # noqa: E402

from quant_rl_trading.collectors.finra_short import (  # noqa: E402
    ShortVolumeBackfiller,
)
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402

USER_AGENT = "Quant_RL_Trading research yjun273@gmail.com"


def make_fetch(client: httpx.Client):  # noqa: ANN201
    def fetch(url: str) -> str:
        response = client.get(url)
        # **없는 파일에 403 이 온다 — 404 가 아니다.**
        #
        # CDN 이 S3 위에 있고, 버킷이 목록 권한을 안 주면 없는 키에 404 대신
        # `AccessDenied` 403 을 낸다(2026-08-19 실측: 주말 20260815·20260816).
        # 이걸 권한 오류로 읽으면 5년 백필에서 주말 520일이 전부 오류로 쌓여
        # **진짜 고장이 그 안에 묻힌다.**
        #
        # 본문으로 가른다. 진짜 권한 문제라면 평일도 같이 막히므로, 그때는
        # 오류 수가 폭증해서 바로 보인다.
        if response.status_code == 404 or (
            response.status_code == 403 and "AccessDenied" in response.text
        ):
            raise FileNotFoundError(url)
        response.raise_for_status()
        return response.text

    return fetch


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--root", default="data")
    args = parser.parse_args(argv)

    store = Store(root=Path(args.root))
    with httpx.Client(
        timeout=30, headers={"User-Agent": USER_AGENT}, follow_redirects=True
    ) as client:
        backfiller = ShortVolumeBackfiller(
            store=store, fetch=make_fetch(client), clock=LiveClock()
        )
        days = backfiller.plan(
            date.fromisoformat(args.start), date.fromisoformat(args.end)
        )
        print(f"FINRA 공매도 · {len(days)}일 ({args.start} ~ {args.end})", flush=True)
        rows = skipped = empty = errors = 0
        for index, day in enumerate(days, start=1):
            result = backfiller.run_day(day)
            if result.skipped:
                skipped += 1
            elif result.error:
                errors += 1
            elif result.rows == 0:
                empty += 1
            else:
                rows += result.rows
            if index % 20 == 0 or index == len(days):
                print(
                    f"[{index}/{len(days)}] {day} · 누적 {rows:,}행 · "
                    f"건너뜀 {skipped} · 휴장 {empty} · 오류 {errors}",
                    flush=True,
                )
    print(f"완료 — {rows:,}행 · 건너뜀 {skipped} · 휴장 {empty} · 오류 {errors}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
