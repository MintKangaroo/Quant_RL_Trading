#!/usr/bin/env python
"""한국 일별 시장금리(콜·CD91·국고 3·10년)를 ECOS 에서 시계열로 창고 `indices` 에 넣는다.

    .venv/bin/python tools/backfill_ecos_daily_rates.py \
        --start 2020-01-02 --end 2026-10-06 [--dry-run]

`backfill_kr_base_rate.py` 와 같은 자리(`indices`, `board="rate"`, `RATE` 접두어 —
벤치마크 후보에 안 섞이게)다. 일정 표(`macro_releases`)가 아니라 여기인 이유도 같다 —
과거 as_of 로 시계열을 읽어야 한다.

## 관측시각

ECOS 일별 계열의 공표 시각을 확인하지 못했다. 그래서 **d 값은 다음 세션 마감
(+ 국장 공표 지연 설정)에 알았다**고 찍는다(`PublicationPolicy.for_session(d,
extra_lag_days=1)`). 늦게 아는 쪽으로 틀리면 백테스트가 비관적일 뿐이지만, 일찍 아는
쪽으로 틀리면 미래를 본다. 아직 공표 전인 세션(`NotYetPublished`)과 거래일이 아닌
날짜는 건너뛰고 개수를 적는다.

## 재실행

run_id 에 구간이 박힌다 — 같은 구간을 두 번 넣으려 하면 창고가 막는다
(append-only, 정정은 revision 으로).
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime, time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.collectors.macro_source import ECOS_DAILY_RATES, EcosSource  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market  # noqa: E402
from quant_rl_trading.collectors.publication import (  # noqa: E402
    NotATradingDay,
    NotYetPublished,
    publication_policy,
)
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from tools.backfill import build_store  # noqa: E402

SOURCE = "ecos"
#: ECOS StatisticSearch 한 번에 받을 끝 행. 일별 6년이면 1,500행 안팎 — 넉넉히
#: (기본 10 은 일일 수집용이라 조용히 잘린다).
ROW_LIMIT = 100000


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--start", default="2020-01-02", help="YYYY-MM-DD")
    parser.add_argument(
        "--end",
        required=True,
        help="YYYY-MM-DD — 조회 구간의 끝(창고 시각은 응답의 날짜에서만 온다)",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    load_env()
    key = (os.environ.get("ECOS_API_KEY") or "").strip()
    if not key:
        print("ECOS_API_KEY 가 없다.", file=sys.stderr)
        return 2
    source = EcosSource(key)
    store = build_store(None)
    policy = publication_policy(store, Market.KR, clock=LiveClock())
    start, end = args.start.replace("-", ""), args.end.replace("-", "")

    rows: list[dict[str, object]] = []
    for name, (entity, spec) in ECOS_DAILY_RATES.items():
        raw = source.search(spec, start=start, end=end, limit=ROW_LIMIT)
        kept = pending = holiday = 0
        for item in raw:
            period, value = str(item.get("TIME") or ""), item.get("DATA_VALUE")
            if len(period) != 8 or value in (None, ""):
                continue
            day = datetime.strptime(period, "%Y%m%d").date()
            try:
                observed = policy.for_session(day, extra_lag_days=1)
            except NotYetPublished:
                pending += 1
                continue
            except NotATradingDay:
                holiday += 1
                continue
            rows.append(
                {
                    "entity_id": entity,
                    "valid_from": datetime.combine(
                        day, time(0), tzinfo=UTC
                    ),  # 다른 국장 일별 행과 같은 자정 UTC
                    "observed_at": observed,
                    "source": SOURCE,
                    "market": "KR",
                    "board": "rate",
                    "open": None,
                    "high": None,
                    "low": None,
                    "close": float(value),
                    "volume": None,
                    "value": None,
                }
            )
            kept += 1
        print(
            f"{name} {entity}: 응답 {len(raw)} · 적재 대상 {kept} · "
            f"공표 전 {pending} · 비거래일 {holiday}"
        )
        if not raw:
            print(f"{name}: 0행 — 구간·코드를 확인할 것", file=sys.stderr)
            return 1
    if args.dry_run:
        for row in rows[:2] + rows[-2:]:
            print(
                f"  {row['entity_id']} {row['valid_from']} obs {row['observed_at']} {row['close']}"
            )
        print("(dry-run — 적재하지 않았다)")
        return 0
    run_id = f"ecos-daily-rates-{args.start}-{args.end}"
    if store.ingest_run_recorded("indices", run_id):
        print("이미 적재된 구간이다 — 건너뛴다.")
        return 0
    written = store.append("indices", rows, ingest_run_id=run_id, source=SOURCE)
    print(f"indices 적재: {written}행 (run {run_id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
