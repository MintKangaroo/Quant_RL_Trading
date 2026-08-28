"""거시지표 시장 예측치(컨센서스) — ForexFactory 주간 캘린더 JSON → `macro_consensus`.

    .venv/bin/python tools/collect_consensus_ff.py [--dry-run]

FRED 는 실측값만 준다. 브리핑 거시지표에 "예측 대비" 를 적으려면 컨센서스가 필요한데
(사용자 요청 2026-08-29), 무료로 매주 forecast·previous·actual 을 주는 피드가 이것이다.
표기는 피드 그대로 문자열("203K", "0.2%")로 둔다 — 우리 macro_releases 의 단위(persons·index)와
다른 지표가 많아 숫자로 접으면 거짓 비교가 된다. 우리 지표 id 로 매핑되는 것만 발표 행 옆에
붙고, 나머지는 US:FF:<slug> 로 남는다. 이번 주 피드만 있으므로 **매일** 받아 쌓는다.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from tools.backfill import build_store  # noqa: E402

URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
TABLE = "macro_consensus"
SOURCE = "forexfactory"
#: 피드 제목 → 우리 macro_releases 지표 id. 없는 것은 US:FF:<slug>.
TITLE_MAP: dict[str, str] = {
    "Unemployment Claims": "US:JOBLESS_CLAIMS",
    "Federal Funds Rate": "US:FED_FUNDS",
    "CPI m/m": "US:CPI", "Core CPI m/m": "US:CPI", "CPI y/y": "US:CPI",
    "Unemployment Rate": "US:EMPLOYMENT", "Non-Farm Employment Change": "US:EMPLOYMENT",
    "Average Hourly Earnings m/m": "US:EMPLOYMENT",
    "Advance GDP q/q": "US:GDP", "Prelim GDP q/q": "US:GDP", "Final GDP q/q": "US:GDP",
    "Housing Starts": "US:HOUSING_STARTS", "Building Permits": "US:HOUSING_STARTS",
    "Industrial Production m/m": "US:INDUSTRIAL_PRODUCTION",
    "JOLTS Job Openings": "US:JOLTS",
    "Core PCE Price Index m/m": "US:PCE", "Personal Spending m/m": "US:PCE", "Personal Income m/m": "US:PCE",
    "PPI m/m": "US:PPI", "Core PPI m/m": "US:PPI",
    "Retail Sales m/m": "US:RETAIL_ADVANCE", "Core Retail Sales m/m": "US:RETAIL_ADVANCE",
    "Trade Balance": "US:TRADE_BALANCE",
    "Employment Cost Index q/q": "US:EMPLOYMENT_COST",
}


def slug(title: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", title.upper()).strip("_")[:40]


def rows_from_feed(feed: list[dict], *, observed_at: datetime) -> list[dict]:
    out = []
    for item in feed:
        if item.get("country") != "USD":
            continue
        title = str(item.get("title") or "").strip()
        when = item.get("date")
        if not title or not when:
            continue
        forecast = str(item.get("forecast") or "").strip()
        previous = str(item.get("previous") or "").strip()
        if not forecast and not previous:
            continue  # 연설 일정 등 — 예측치가 없는 것은 적을 것이 없다
        scheduled = datetime.fromisoformat(when)
        entity = TITLE_MAP.get(title, f"US:FF:{slug(title)}")
        out.append({
            "entity_id": entity, "valid_from": scheduled, "observed_at": observed_at, "source": SOURCE,
            "market": "US", "title": title, "forecast": forecast, "previous": previous,
            "actual": str(item.get("actual") or "").strip(), "impact": str(item.get("impact") or ""),
        })
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    load_env()
    now = LiveClock().now()
    store = build_store(args.data_root)
    feed = httpx.get(URL, headers={"User-Agent": "Mozilla/5.0 (quant_rl_trading consensus)"}, timeout=20).json()
    rows = rows_from_feed(feed, observed_at=now)
    have = store.get(TABLE, as_of=now, lookback=14)
    if not have.empty:
        seen = set(zip(have["entity_id"], have["valid_from"], have["forecast"].fillna(""), have["actual"].fillna("")))
        rows = [r for r in rows if (r["entity_id"], r["valid_from"], r["forecast"], r["actual"]) not in seen]
    mapped = sum(1 for r in rows if not r["entity_id"].startswith("US:FF:"))
    print(f"피드 {len(feed)}건 → 새 예측치 {len(rows)}행 (우리 지표에 매핑 {mapped})")
    for r in rows[:8]:
        print(f"  {r['valid_from']:%m-%d %H:%M} {r['title']:<32} 예측 {r['forecast'] or '—':>8} 직전 {r['previous'] or '—':>8} → {r['entity_id']}")
    if not rows or args.dry_run:
        return 0
    written = store.append(TABLE, rows, ingest_run_id=f"consensus-ff-{now:%Y%m%dT%H%M%S}", source=SOURCE)
    print(f"{TABLE} 적재: {written}행")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
