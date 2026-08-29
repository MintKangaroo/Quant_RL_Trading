"""실적 발표 일정 — 미장은 Nasdaq 캘린더(확정·시각), 국장은 DART 작년 공시일 기준 추정.

    .venv/bin/python tools/collect_earnings_calendar.py [--days 45] [--dry-run]

뉴스·일정 탭 달력이 읽는 `earnings_calendar` 를 채운다. 매일 07:25 크론.

- 미장: https://api.nasdaq.com/api/calendar/earnings?date=YYYY-MM-DD (하루 한 콜). 시총
  `--min-cap`(기본 100억$) 이상 또는 보유 종목만. 시각은 pre(장 전 08:00 ET)/post(장 후
  16:30 ET)/unknown(정오) 로 적는다.
- 국장: 공개된 사전 일정이 없다. `documents` 의 작년 같은 분기 "영업(잠정)실적" 공시일을
  1년 뒤로 미룬 **추정**이다(status=estimated, timing=estimate). 시총 상위 `--kr-top` +
  보유 종목만. 추정을 확정처럼 보이게 하지 않는다 — 화면이 "예상" 을 붙인다.
"""
from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.accounting import ledger  # noqa: E402
from quant_rl_trading.accounting.rates import Rates  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.reporting.briefing import market_caps  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from tools.backfill import build_store  # noqa: E402

TABLE = "earnings_calendar"
NASDAQ_URL = "https://api.nasdaq.com/api/calendar/earnings?date={day}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}
NY = ZoneInfo("America/New_York")
SEOUL = ZoneInfo("Asia/Seoul")
TIMING = {"time-pre-market": "pre", "time-after-hours": "post", "time-not-supplied": "unknown"}
TIMING_CLOCK = {"pre": time(8, 0), "post": time(16, 30), "unknown": time(12, 0)}


def _cap(text: str) -> float:
    digits = "".join(ch for ch in str(text or "") if ch.isdigit() or ch == ".")
    return float(digits) if digits else 0.0


def us_rows(
    client: httpx.Client, days: list[date], *, observed_at: datetime, min_cap: float, keep: set[str]
) -> list[dict]:
    out: list[dict] = []
    for day in days:
        try:
            payload = client.get(NASDAQ_URL.format(day=day.isoformat()), headers=HEADERS, timeout=20).json()
        except Exception as exc:  # 하루 실패가 전체를 막지 않는다
            print(f"  {day} 실패: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        rows = ((payload or {}).get("data") or {}).get("rows") or []
        for item in rows:
            symbol = str(item.get("symbol") or "").strip().upper()
            if not symbol:
                continue
            entity = f"US:{symbol}"
            cap = _cap(item.get("marketCap"))
            if cap < min_cap and entity not in keep:
                continue
            timing = TIMING.get(str(item.get("time") or ""), "unknown")
            scheduled = datetime.combine(day, TIMING_CLOCK[timing], tzinfo=NY).astimezone(UTC)
            out.append({
                "entity_id": entity, "valid_from": scheduled, "observed_at": observed_at,
                "source": "nasdaq", "market": "US", "name": str(item.get("name") or symbol),
                "timing": timing, "fiscal_quarter": str(item.get("fiscalQuarterEnding") or ""),
                "eps_forecast": str(item.get("epsForecast") or ""), "market_cap": cap,
                "status": "scheduled",
            })
    return out


def _shift_year(day: date) -> date:
    try:
        moved = day.replace(year=day.year + 1)
    except ValueError:  # 2/29
        moved = day.replace(year=day.year + 1, day=28)
    while moved.weekday() >= 5:
        moved += timedelta(days=1)
    return moved


def kr_rows(
    store: Store, *, as_of: datetime, keep: set[str], horizon_days: int, observed_at: datetime
) -> list[dict]:
    docs = store.get(
        "documents", as_of=as_of, lookback=400,
        columns=["entity_id", "valid_from", "doc_type", "title", "filer"],
    )
    if docs.empty:
        return []
    docs = docs[
        docs["entity_id"].str.startswith("KR:")
        & (docs["doc_type"] == "earnings")
        & docs["title"].str.contains("잠정", na=False)
        & ~docs["title"].str.contains("기재정정", na=False)
        & docs["entity_id"].isin(keep)
    ]
    today = as_of.astimezone(SEOUL).date()
    limit = today + timedelta(days=horizon_days)
    out: list[dict] = []
    seen: set[tuple[str, date]] = set()
    for row in docs.itertuples(index=False):
        base = pd.Timestamp(row.valid_from).tz_convert(SEOUL).date()
        est = _shift_year(base)
        if not (today <= est <= limit) or (row.entity_id, est) in seen:
            continue
        seen.add((row.entity_id, est))
        quarter = (base.month - 1) // 3 + 1
        out.append({
            "entity_id": str(row.entity_id),
            "valid_from": datetime.combine(est, time(9, 0), tzinfo=SEOUL).astimezone(UTC),
            "observed_at": observed_at, "source": "dart-estimate", "market": "KR",
            "name": str(row.filer or row.entity_id), "timing": "estimate",
            "fiscal_quarter": f"작년 {base.isoformat()} 공시(Q{quarter}) 기준",
            "eps_forecast": "", "market_cap": 0.0, "status": "estimated",
        })
    return out


def _holdings(root: Path | None, *, as_of: datetime) -> set[str]:
    if root is None or not root.exists():
        return set()
    store = Store(root=root)
    book = ledger.build_book(store, as_of=as_of, rates=Rates.from_store(store, as_of=as_of))
    return {entity for entity, position in book.positions.items() if position.quantity > 0}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--holdings-root", type=Path, default=Path("data/_paper"))
    parser.add_argument("--days", type=int, default=45, help="미장 앞으로 며칠")
    parser.add_argument("--min-cap", type=float, default=10e9, help="미장 시총 하한(USD)")
    parser.add_argument("--kr-top", type=int, default=60, help="국장 시총 상위 N")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    load_env()
    now = LiveClock().now()
    store = build_store(args.data_root)
    keep = _holdings(args.holdings_root, as_of=now)
    caps_kr, _ = market_caps(store, as_of=now, market="KR")
    kr_keep = set(caps_kr.sort_values(ascending=False).head(args.kr_top).index) | {
        e for e in keep if e.startswith("KR:")
    }
    days = [now.astimezone(NY).date() + timedelta(days=i) for i in range(args.days)]
    days = [d for d in days if d.weekday() < 5]
    with httpx.Client() as client:
        us = us_rows(client, days, observed_at=now, min_cap=args.min_cap,
                     keep={e for e in keep if e.startswith("US:")})
    kr = kr_rows(store, as_of=now, keep=kr_keep, horizon_days=args.days + 20, observed_at=now)
    rows = us + kr
    print(f"미장 {len(us)}건 ({len(days)}일 조회) · 국장 추정 {len(kr)}건 (시총 상위 {args.kr_top} + 보유)")
    for row in sorted(rows, key=lambda r: r["valid_from"])[:8]:
        print(f"  {row['valid_from'].astimezone(SEOUL):%m-%d %H:%M} {row['entity_id']:<10} {row['name'][:24]:<24} {row['timing']}")
    if args.dry_run or not rows:
        return 0
    store.append(TABLE, rows, ingest_run_id=f"earnings-calendar-{now:%Y%m%dT%H%M%S}")
    print(f"{TABLE} 적재: {len(rows)}행")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
