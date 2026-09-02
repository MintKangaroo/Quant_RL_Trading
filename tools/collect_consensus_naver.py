#!/usr/bin/env python
"""국장 컨센서스 일일 수집 (방향 ③, 2026-09-02).

    .venv/bin/python tools/collect_consensus_naver.py            # 오늘 세션, 유니버스 전체
    .venv/bin/python tools/collect_consensus_naver.py --limit 20 # 배관 확인

종목당 요청 1건, 0.25초 간격 — 2,800종목이면 12분 안팎. 받은 날은 건너뛴다(run id).
장 마감 뒤(17:30 크론)에 돈다. 페이지 실패는 세고 계속 간다 — 한 종목이 전체를 막지 않는다.
"""
from __future__ import annotations

import argparse
import sys
import time as time_module
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import httpx  # noqa: E402

from quant_rl_trading.collectors import naver_consensus as nc  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market, trading_days  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
INTERVAL_SEC = 0.25


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="data")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--day", help="YYYY-MM-DD (기본: 오늘 이전 마지막 거래일)")
    args = parser.parse_args(argv)
    clock = LiveClock(); now = clock.now()
    store = Store(root=Path(args.root))
    from datetime import date, timedelta
    if args.day:
        day = date.fromisoformat(args.day)
    else:
        here = now.astimezone(__import__("zoneinfo").ZoneInfo("Asia/Seoul")).date()
        days = trading_days(Market.KR, here - timedelta(days=14), here)
        day = days[-1] if days else here
    run_id = nc.run_id_for(day, limit=args.limit)
    if store.ingest_run_recorded(nc.CONSENSUS, run_id):
        print(f"{day} 컨센서스는 이미 받았다 — 할 일 없음"); return 0
    uni = store.get("universe", as_of=now, lookback=7, market="KR", columns=["entity_id", "valid_from", "is_listed", "is_tradable"])
    if uni.empty:
        print("유니버스가 비었다", file=sys.stderr); return 2
    latest = uni.sort_values("valid_from").groupby("entity_id").tail(1)
    codes = sorted(e.split(":", 1)[1] for e in latest[latest["is_listed"].astype(bool) & latest["is_tradable"].astype(bool)]["entity_id"])
    if args.limit:
        codes = codes[: args.limit]
    print(f"{day} · 종목 {len(codes)} · 간격 {INTERVAL_SEC}s", flush=True)
    rows = []; fails = 0; empty = 0
    with httpx.Client(timeout=15, headers={"User-Agent": USER_AGENT}, follow_redirects=True) as client:
        for i, code in enumerate(codes, 1):
            try:
                r = client.get(nc.URL.format(code=code)); r.raise_for_status()
                parsed = nc.parse_main_page(r.text)
            except Exception as error:  # noqa: BLE001 — 한 종목이 전체를 막지 않는다
                fails += 1
                if fails <= 5:
                    print(f"  {code}: 실패 {type(error).__name__}", file=sys.stderr)
                time_module.sleep(INTERVAL_SEC); continue
            row = nc.row_for(code, day=day, observed_at=clock.now(), parsed=parsed)
            if row is None:
                empty += 1
            else:
                rows.append(row)
            if i % 200 == 0:
                print(f"  [{i}/{len(codes)}] 행 {len(rows)} · 커버리지 없음 {empty} · 실패 {fails}", flush=True)
            time_module.sleep(INTERVAL_SEC)
    written = store.append(nc.CONSENSUS, rows, ingest_run_id=run_id, source=nc.SOURCE) if rows else 0
    print(f"완료 — 적재 {written}행 · 커버리지 없음 {empty} · 실패 {fails} / {len(codes)}", flush=True)
    return 0 if fails < max(10, len(codes) // 10) else 1


if __name__ == "__main__":
    raise SystemExit(main())
