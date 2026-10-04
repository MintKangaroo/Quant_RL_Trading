"""미장 대표지수 일봉을 마감 직후 받는다 — LS `t3518`. Yahoo 대체 (2026-10-04).

    .venv/bin/python tools/collect_indices_us_ls.py            # 최근 5거래일 중 빠진 것
    .venv/bin/python tools/collect_indices_us_ls.py --dry-run

FRED(collect_macro)는 전날 지수를 미국 오후에 내서 06:30 브리핑이 이틀 전 종가였다(2026-08-28 실측).
그 자리를 Yahoo 가 메웠는데 Yahoo 는 robots.txt `Disallow: /` 라 걷어냈다(`docs/design/data-contract.md` §4-2).
LS `t3518` 은 마감 직후 그날 봉을 준다. 값은 FRED 와 같다(77세션 차이 0.00bp, 2026-10-04 대조).

entity_id 는 FRED 와 같다(`macro_source.INDEX_SERIES`) — 같은 (entity, 날짜)에 FRED 행이 나중에 오면 같은
종가의 정정본이 된다. 이미 있는 (entity, 날짜)는 건너뛴다. **VIX·VXN·RVX 는 LS 에 없다** — FRED 만 채운다.

**지수 값의 소수점이 두 자리 밀려 온다**(`ls_investinfo.decimal_scale` 이 `t3521` 로 맞춘다).

관측시각은 벽시계가 아니라 **공표 정책 시각**(ET 16:00 + 지연) — 옛 Yahoo 수집기·LS `t1511` 과 같은 규약.
벽시계(06:00 KST)로 찍으면 미장 시각(16:20 ET) 스냅샷이 그날 지수를 못 본다. 미국 세션 날짜가 valid_from 의
UTC 자정이다(FRED 와 같은 규약).
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.collectors.ls_investinfo import fetch_index_daily  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market  # noqa: E402
from quant_rl_trading.collectors.publication import (  # noqa: E402
    NotATradingDay,
    NotYetPublished,
    publication_policy,
)
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from tools.backfill import build_store  # noqa: E402
from tools.collect_fx_ls import ls_client_for_queries  # noqa: E402

TABLE = "indices"
SOURCE = "ls_t3518"
#: entity_id(macro_source.INDEX_SERIES 와 같은 이름) → LS t3518 kind=S 심볼.
SYMBOLS: dict[str, str] = {
    "US:IDX:SP500": "SPI@SPX",
    "US:IDX:NASDAQ": "NAS@IXIC",
    "US:IDX:NASDAQ100": "NAS@NDX",
    "US:IDX:DJIA": "DJI@DJI",
    "US:IDX:DJTA": "DJI@DJT",
    "US:IDX:DJUA": "DJI@DJU",
    "US:IDX:SOX": "NAS@SOX",
}
#: 한 번에 받는 봉 수 — 배율을 맞출 t3521 기준일이 창 안에 있어야 하고, --days 를 덮어야 한다.
COUNT = 12


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--days", type=int, default=5, help="최근 N 거래일만")
    args = parser.parse_args(argv)
    load_env()
    clock = LiveClock()
    now = clock.now()
    store = build_store(args.data_root)
    policy = publication_policy(store, Market.US, clock=clock)
    have = store.get(TABLE, as_of=now, lookback=20)
    have_keys = set(zip(have["entity_id"], have["valid_from"].dt.date, strict=True)) if not have.empty else set()
    rows: list[dict[str, Any]] = []
    failed = 0
    client = ls_client_for_queries(store, as_of=now)
    try:
        for entity, symbol in SYMBOLS.items():
            try:
                bars = fetch_index_daily(client, symbol, count=COUNT)
            except Exception as error:  # 한 심볼 실패가 나머지를 막지 않는다
                print(f"  {entity}: 실패 {type(error).__name__}: {error}", file=sys.stderr)
                failed += 1
                continue
            if not bars:
                print(f"  {entity}: {symbol} 빈 응답", file=sys.stderr)
                failed += 1
                continue
            for bar in bars[-args.days:]:
                if (entity, bar.day) in have_keys:
                    continue
                try:
                    # 오늘 미국 세션이 아직 안 끝났으면 여기서 걸린다 — 미완성 봉을 적지 않는다.
                    observed_at = policy.for_session(bar.day)
                except (NotYetPublished, NotATradingDay):
                    continue
                rows.append({
                    "entity_id": entity, "valid_from": datetime(bar.day.year, bar.day.month, bar.day.day, tzinfo=UTC),
                    "observed_at": observed_at, "source": SOURCE, "market": "US", "board": "index",
                    "open": bar.open, "high": bar.high, "low": bar.low, "close": bar.close,
                    "volume": None, "value": None,
                })
    finally:
        client.close()
    for r in rows:
        print(f"  {r['entity_id']} {r['valid_from'].date()} 종가 {r['close']:,.2f}")
    if not rows:
        print("새로 적을 지수가 없다.")
    elif args.dry_run:
        print(f"드라이런 — {len(rows)}행 적지 않는다")
    else:
        written = store.append(TABLE, rows, ingest_run_id=f"indices-us-ls-{now:%Y%m%dT%H%M%S}", source=SOURCE)
        print(f"indices 적재: {written}행 ({SOURCE})")
    # 심볼이 전부 실패했으면 경로 고장이다 — rc 로 내보낸다(조용한 실패 금지).
    return 1 if failed == len(SYMBOLS) else 0


if __name__ == "__main__":
    raise SystemExit(main())
