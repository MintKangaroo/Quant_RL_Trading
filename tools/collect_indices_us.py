"""미장 대표지수 일봉을 마감 직후 받는다 — FRED 가 하루 늦는 자리 (Yahoo Finance chart).

    .venv/bin/python tools/collect_indices_us.py            # 최근 5거래일 중 빠진 것
    .venv/bin/python tools/collect_indices_us.py --dry-run

FRED(collect_macro)는 전날 지수를 미국 오후에 내서 06:30 브리핑은 늘 **이틀 전** 종가였다
(실측 2026-08-28 06:30: S&P 08-26). Yahoo 는 마감(05:00 KST) 몇 분 뒤 그날 종가를 준다.
entity_id 는 FRED 와 같다(`macro_source.INDEX_SERIES`) — 같은 (entity, 날짜)에 FRED 행이
나중에 오면 같은 종가의 정정본이 된다. 이미 있는 (entity, 날짜)는 건너뛴다.

**미장 세션 날짜는 미국 날짜다.** valid_from 은 그 날짜의 UTC 자정(FRED 와 같은 규약).
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from tools.backfill import build_store  # noqa: E402

TABLE = "indices"
SOURCE = "yahoo"
URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=10d&interval=1d"
#: Yahoo 심볼 → entity_id (macro_source.INDEX_SERIES 와 같은 이름).
SYMBOLS: dict[str, str] = {
    "^GSPC": "US:IDX:SP500",
    "^IXIC": "US:IDX:NASDAQ",
    "^NDX": "US:IDX:NASDAQ100",
    "^DJI": "US:IDX:DJIA",
    "^DJT": "US:IDX:DJTA",
    "^DJU": "US:IDX:DJUA",
    "^SOX": "US:IDX:SOX",
    "^VIX": "US:IDX:VIX",
    "^VXN": "US:IDX:VXN",
    "^RVX": "US:IDX:RVX",
}
HEADERS = {"User-Agent": "Mozilla/5.0 (quant_rl_trading index collector)"}


def fetch(symbol: str, client: httpx.Client) -> list[tuple[date, dict[str, float | None]]]:
    data = client.get(URL.format(symbol=symbol), headers=HEADERS, timeout=20).json()
    result = (data.get("chart") or {}).get("result") or []
    if not result:
        return []
    r = result[0]
    stamps = r.get("timestamp") or []
    q = ((r.get("indicators") or {}).get("quote") or [{}])[0]
    out = []
    for i, ts in enumerate(stamps):
        close = (q.get("close") or [None])[i] if i < len(q.get("close") or []) else None
        if close is None:
            continue
        # 미장 거래일은 뉴욕 날짜다. 타임스탬프는 그날 개장 시각(UTC 13:30)이라 UTC 날짜로 충분하다.
        day = datetime.fromtimestamp(ts, UTC).date()
        pick = lambda k: (q.get(k) or [None] * len(stamps))[i]  # noqa: E731
        out.append((day, {"open": pick("open"), "high": pick("high"), "low": pick("low"),
                          "close": float(close), "volume": pick("volume")}))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--days", type=int, default=5, help="최근 N 거래일만")
    args = parser.parse_args(argv)
    load_env()
    now = LiveClock().now()
    store = build_store(args.data_root)
    have = store.get(TABLE, as_of=now, lookback=20)
    have_keys = set(zip(have["entity_id"], have["valid_from"].dt.date)) if not have.empty else set()
    rows: list[dict] = []
    with httpx.Client() as client:
        for symbol, entity in SYMBOLS.items():
            try:
                bars = fetch(symbol, client)
            except Exception as error:  # 한 심볼 실패가 나머지를 막지 않는다
                print(f"  {entity}: 실패 {type(error).__name__}", file=sys.stderr)
                continue
            for day, bar in bars[-args.days:]:
                if day >= now.date():  # 오늘 미국 세션은 아직 안 끝났을 수 있다 — 종가만 적는다
                    if now.hour < 21:  # UTC 21:00 = 뉴욕 17:00 마감 뒤
                        continue
                if (entity, day) in have_keys:
                    continue
                rows.append({
                    "entity_id": entity, "valid_from": datetime(day.year, day.month, day.day, tzinfo=UTC),
                    "observed_at": now, "source": SOURCE, "market": "US", "board": "index",
                    "open": bar["open"], "high": bar["high"], "low": bar["low"], "close": bar["close"],
                    "volume": bar["volume"], "value": None,
                })
    for r in rows:
        print(f"  {r['entity_id']} {r['valid_from'].date()} 종가 {r['close']:,.2f}")
    if not rows:
        print("새로 적을 지수가 없다."); return 0
    if args.dry_run:
        print(f"드라이런 — {len(rows)}행 적지 않는다"); return 0
    written = store.append(TABLE, rows, ingest_run_id=f"indices-us-yahoo-{now:%Y%m%dT%H%M%S}", source=SOURCE)
    print(f"indices 적재: {written}행 ({SOURCE})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
