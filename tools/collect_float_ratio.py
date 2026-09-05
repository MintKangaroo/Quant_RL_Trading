"""유동주식비율 수집 — 네이버 종목분석 기업개요 → `float_ratio` (주 1회).

    .venv/bin/python tools/collect_float_ratio.py [--limit 5] [--board kospi]
"""
from __future__ import annotations

import argparse
import sys
import time as time_module
from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.collectors import wisereport_float as wf  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market, trading_days  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402

INTERVAL_SEC = 0.4
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) Quant_RL_Trading/float-ratio"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="data")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--board", choices=["all", "kospi"], default="all", help="kospi = 공매도 표의 KOSPI 판만")
    args = parser.parse_args(argv)
    clock = LiveClock(); now = clock.now()
    store = Store(root=Path(args.root))
    here = now.astimezone(ZoneInfo("Asia/Seoul")).date()
    days = trading_days(Market.KR, here - timedelta(days=14), here); day = days[-1] if days else here
    run_id = wf.run_id_for(day, limit=args.limit)
    if store.ingest_run_recorded(wf.FLOAT_RATIO, run_id):
        print(f"{day} 유동주식비율은 이미 받았다 — 할 일 없음"); return 0
    uni = store.get("universe", as_of=now, lookback=7, market="KR", columns=["entity_id", "valid_from", "is_listed", "is_tradable"])
    latest = uni.sort_values("valid_from").groupby("entity_id").tail(1)
    entities = set(latest[latest["is_listed"].astype(bool) & latest["is_tradable"].astype(bool)]["entity_id"].astype(str))
    if args.board == "kospi":
        sh = store.get("shorting", as_of=now, lookback=400, market="KR", columns=["entity_id", "board"])
        entities &= set(sh[sh["board"] == "KOSPI"]["entity_id"].astype(str))
    codes = sorted(e.split(":", 1)[1] for e in entities)
    if args.limit:
        codes = codes[: args.limit]
    print(f"{day} · 종목 {len(codes)} · 간격 {INTERVAL_SEC}s", flush=True)
    rows = []; fails = 0; empty = 0
    with httpx.Client(timeout=15, headers={"User-Agent": USER_AGENT}, follow_redirects=True) as client:
        for i, code in enumerate(codes, 1):
            try:
                r = client.get(wf.URL.format(code=code)); r.raise_for_status()
                parsed = wf.parse_company_page(r.text)
            except Exception as error:  # noqa: BLE001
                fails += 1
                if fails <= 5:
                    print(f"  {code}: 실패 {type(error).__name__}", file=sys.stderr)
                time_module.sleep(INTERVAL_SEC); continue
            if parsed is None:
                empty += 1
            else:
                rows.append(wf.row_for(code, day=day, observed_at=clock.now(), parsed=parsed))
            if i % 200 == 0:
                print(f"  [{i}/{len(codes)}] 행 {len(rows)} · 없음 {empty} · 실패 {fails}", flush=True)
            time_module.sleep(INTERVAL_SEC)
    written = store.append(wf.FLOAT_RATIO, rows, ingest_run_id=run_id, source=wf.SOURCE) if rows else 0
    print(f"완료 — 적재 {written}행 · 없음 {empty} · 실패 {fails} / {len(codes)}", flush=True)
    return 0 if fails < max(10, len(codes) // 10) else 1


if __name__ == "__main__":
    raise SystemExit(main())
