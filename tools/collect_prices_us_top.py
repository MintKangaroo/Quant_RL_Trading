"""미장 시총 상위·대표 ETF 의 전날 종가를 마감 직후 받는다 (Yahoo) — 브리핑 "시가총액 상위 전일대비".

    .venv/bin/python tools/collect_prices_us_top.py [--top 60] [--dry-run]

06:30 브리핑 시점에 미장 시세는 ETF 대용 4종만 있어(전체 6,600종목은 08:40 수집) 시총 상위
표의 전일대비가 전부 "—" 였다(2026-08-29). 시총 상위 N 종목만 Yahoo 일봉으로 먼저 적는다.
전체 수집(LS)이 나중에 같은 (종목, 날짜)로 오면 정정본이 된다.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.reporting.briefing import market_caps  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from tools.backfill import build_store  # noqa: E402
from tools.collect_indices_us import HEADERS  # noqa: E402

URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=5d&interval=1d"
SOURCE = "yahoo"
ETFS = ("SPY", "QQQ", "DIA", "SOXX", "IWM")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--top", type=int, default=60)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    load_env()
    now = LiveClock().now()
    store = build_store(args.data_root)
    caps, _ = market_caps(store, as_of=now, market="US")
    symbols = [e.split(":", 1)[1] for e in caps.sort_values(ascending=False).head(args.top).index] + list(ETFS)
    # 이미 적재된 (종목, 세션) 키만 본다 — 종가 값은 안 읽으므로 휴장일 0 문제와 무관하다.
    have = store.get(  # invariant-allow: price-read
        "prices", as_of=now, lookback=6, market="US", columns=["entity_id", "valid_from"]
    )
    have_keys = set(zip(have["entity_id"], have["valid_from"].dt.date)) if not have.empty else set()
    rows: list[dict] = []
    with httpx.Client() as client:
        for symbol in dict.fromkeys(symbols):
            try:
                data = client.get(URL.format(symbol=symbol.replace(".", "-")), headers=HEADERS, timeout=20).json()
                r = ((data.get("chart") or {}).get("result") or [None])[0] or {}
                q = ((r.get("indicators") or {}).get("quote") or [{}])[0]
                for i, ts in enumerate(r.get("timestamp") or []):
                    close = (q.get("close") or [None])[i]
                    if close is None:
                        continue
                    day = datetime.fromtimestamp(ts, UTC).date()
                    if day >= now.date() and now.hour < 21:
                        continue
                    entity = f"US:{symbol}"
                    if (entity, day) in have_keys:
                        continue
                    pick = lambda k: (q.get(k) or [None])[i]  # noqa: E731
                    rows.append({
                        "entity_id": entity, "valid_from": datetime(day.year, day.month, day.day, tzinfo=UTC),
                        "observed_at": now, "source": SOURCE, "market": "US",
                        "open": pick("open"), "high": pick("high"), "low": pick("low"), "close": float(close),
                        "volume": pick("volume"), "value": (float(close) * pick("volume")) if pick("volume") else None,
                        "adj_factor": None,
                    })
            except Exception as error:
                print(f"  {symbol}: 실패 {type(error).__name__}", file=sys.stderr)
    print(f"대상 {len(dict.fromkeys(symbols))}종목 · 새 종가 {len(rows)}행")
    if not rows or args.dry_run:
        return 0
    written = store.append("prices", rows, ingest_run_id=f"prices-us-top-yahoo-{now:%Y%m%dT%H%M%S}", source=SOURCE)
    print(f"prices 적재: {written}행 ({SOURCE})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
