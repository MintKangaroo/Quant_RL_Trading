"""미장 재무 백필 — SEC companyfacts.zip → ``fundamentals``(market=US, source=edgar).

    .venv/bin/python tools/backfill_fundamentals_us.py --dry-run --limit 50
    .venv/bin/python tools/backfill_fundamentals_us.py --since 2021-06-30

국장 fundamental Analyst 가 읽는 **같은 metric 이름·같은 분기 규약**으로 적는다:
    revenue · operating_income · net_income          (흐름) — Q1~Q3 는 3개월 값, Q4 는 연간(10-K) 누적
    total_assets · total_liabilities · total_equity · current_assets · current_liabilities  (시점)
    fiscal_period = f"{fy}Q{n}" (회사 회계연도 기준 — AAPL 처럼 9월 결산도 fp 로 가른다), FY → Q4
    valid_from    = 회계기간 말 (UTC 자정)          observed_at = 공시 접수일 + 18:00 ET 컷오프

## 함정 (data-contract.md §4)
- 같은 (지표, 기간)이 여러 공시에 나온다(비교 표시·정정). **가장 먼저 접수된 공시**만 적는다 —
  시장이 처음 안 값이고, 자연키(entity, valid_from, metric) 중복도 피한다.
- 태그는 회사마다 다르다. 매출은 Revenues / RevenueFromContractWithCustomerExcludingAssessedTax /
  SalesRevenueNet 순으로 있는 것을 쓰고, 총부채가 없으면 Assets − StockholdersEquity 로 만든다.
- 단위 USD 만. 시총(market_stats.market_cap)도 USD 라 earnings_yield 가 바로 선다.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.collectors.us_shares import (  # noqa: E402
    UA_ENV, SecBulkFacts, filing_moment,
)
from quant_rl_trading.collectors.us_universe import fetch_listings  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from tools.backfill import build_store  # noqa: E402

TABLE = "fundamentals"
SOURCE = "edgar"
ZIP_PATH = REPO_ROOT / "data" / "raw" / "sec_edgar" / "companyfacts.zip"
#: metric → 태그 후보(우선순위). 전부 us-gaap.
TAGS: dict[str, tuple[str, ...]] = {
    "revenue": ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
                "RevenueFromContractWithCustomerIncludingAssessedTax"),
    "operating_income": ("OperatingIncomeLoss",),
    "net_income": ("NetIncomeLoss", "ProfitLoss"),
    "total_assets": ("Assets",),
    "total_liabilities": ("Liabilities",),
    "total_equity": ("StockholdersEquity",
                     "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
    "current_assets": ("AssetsCurrent",),
    "current_liabilities": ("LiabilitiesCurrent",),
}
FLOWS = ("revenue", "operating_income", "net_income")
CHAIN = tuple(("us-gaap", tag) for tags in TAGS.values() for tag in tags)
FORMS = {"10-Q", "10-K", "10-Q/A", "10-K/A"}


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _period(fy: Any, fp: Any) -> str | None:
    try:
        year = int(fy)
    except (TypeError, ValueError):
        return None
    fp = str(fp or "").upper()
    if fp == "FY":
        return f"{year}Q4"
    if fp in ("Q1", "Q2", "Q3"):
        return f"{year}{fp}"
    return None


def facts_to_rows(payload: dict[str, Any], *, ticker: str, since: date) -> list[dict[str, Any]]:
    """companyfacts 조각 → fundamentals 행. (지표, 기간말) 마다 **가장 먼저 접수된** 공시 하나."""
    gaap = (payload.get("facts") or {}).get("us-gaap") or {}
    picked: dict[tuple[str, date], dict[str, Any]] = {}
    for metric, tags in TAGS.items():
        for tag in tags:
            units = (gaap.get(tag) or {}).get("units") or {}
            entries = units.get("USD") or []
            if not entries:
                continue
            for e in entries:
                if str(e.get("form", "")) not in FORMS:
                    continue
                end, filed = _date(e.get("end")), _date(e.get("filed"))
                if end is None or filed is None or end < since:
                    continue
                period = _period(e.get("fy"), e.get("fp"))
                if period is None:
                    continue
                if metric in FLOWS:
                    start = _date(e.get("start"))
                    if start is None:
                        continue
                    days = (end - start).days
                    if period.endswith("Q4"):
                        if not 350 <= days <= 380:
                            continue  # 연간(누적)만 — 분기 규약과 맞춘다
                    elif not 80 <= days <= 100:
                        continue  # 3개월 값만 — 누적(YTD)은 버린다
                elif e.get("start") is not None:
                    continue  # 시점 지표에 기간이 붙어 있으면 다른 개념이다
                key = (metric, end)
                cur = picked.get(key)
                if cur is None or filed < cur["filed"]:
                    picked[key] = {"metric": metric, "end": end, "filed": filed, "period": period,
                                   "value": float(e["val"]), "form": str(e["form"]), "tag": tag}
            if any(k[0] == metric for k in picked):
                break  # 첫 후보 태그에서 값이 나오면 다음 후보로 안 내려간다
    # 총부채가 없으면 자산 − 자본
    ends = {k[1] for k in picked}
    for end in ends:
        if ("total_liabilities", end) not in picked and ("total_assets", end) in picked and ("total_equity", end) in picked:
            a, q = picked[("total_assets", end)], picked[("total_equity", end)]
            picked[("total_liabilities", end)] = {**a, "metric": "total_liabilities", "value": a["value"] - q["value"],
                                                  "filed": max(a["filed"], q["filed"]), "tag": "Assets-StockholdersEquity"}
    rows = []
    for (metric, end), p in picked.items():
        rows.append({
            "entity_id": f"US:{ticker}",
            "valid_from": datetime(end.year, end.month, end.day, tzinfo=UTC),
            "observed_at": filing_moment(p["filed"]),
            "source": SOURCE, "market": "US", "metric": metric, "value": p["value"],
            "fiscal_period": p["period"],
            "report_type": "edgar_10k" if p["form"].startswith("10-K") else "edgar_10q",
        })
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", default="2021-06-30")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--batch", type=int, default=400)
    parser.add_argument("--data-root", type=Path)
    args = parser.parse_args(argv)
    load_env()
    now = LiveClock().now()
    since = date.fromisoformat(args.since)
    store = build_store(args.data_root)
    ua = os.environ.get(UA_ENV, "")
    facts = SecBulkFacts(path=ZIP_PATH, user_agent=ua)
    facts.download(now=now)
    listings = fetch_listings(ua)
    universe = store.get("universe", as_of=now, lookback=7, market="US")
    listed = set(universe[universe["is_listed"].astype(bool)]["entity_id"]) if not universe.empty else set()
    targets = [l for l in listings if f"US:{l.ticker}" in listed] if listed else listings
    if args.limit:
        targets = targets[: args.limit]
    print(f"명단 {len(listings)} · 우리 유니버스 {len(targets)} · since {since}")
    total_rows = 0; covered = 0; missing = 0; batch: list[dict] = []; batch_no = 0
    stamp = f"{now:%Y%m%dT%H%M}"
    def flush() -> None:
        nonlocal batch, batch_no, total_rows
        if not batch:
            return
        if not args.dry_run:
            store.append(TABLE, batch, ingest_run_id=f"bf-edgar-US-{stamp}-b{batch_no:02d}", source=SOURCE)
        total_rows += len(batch); batch_no += 1; batch = []
    try:
        for i, listing in enumerate(targets, 1):
            payload = facts.facts_for_tags(int(listing.cik), CHAIN)
            rows = facts_to_rows(payload, ticker=listing.ticker, since=since) if payload else []
            if rows:
                covered += 1; batch.extend(rows)
            else:
                missing += 1
            if len(batch) >= args.batch * 20:
                flush()
            if i % 500 == 0:
                print(f"  [{i}/{len(targets)}] 커버 {covered} · 없음 {missing} · 행 {total_rows + len(batch):,}", flush=True)
        flush()
    finally:
        facts.close()
    print(f"완료 — 종목 {len(targets)} · 재무 있음 {covered} ({covered / max(1, len(targets)):.0%}) · 없음 {missing} · 행 {total_rows:,}"
          + (" (드라이런)" if args.dry_run else ""))
    return 0 if covered else 1


if __name__ == "__main__":
    raise SystemExit(main())
