"""6차 랭커 정보원 — 피처 커버리지 (수익과 무관, 측정 전에 봐도 되는 것).

    .venv/bin/python tools/ranker_source_features.py --coverage [--group G1,G2] [--market KR,US]
        [--as-of 2025-12-31,2026-03-31,2026-06-30]

묶음별·시점별로 행 수와 열마다 결측 비율을 찍는다. IC·수익은 계산하지 않는다 —
그것은 사전등록(docs/protocols/ranker-sources-round6-2026-09.md)대로 2026-10-01 이후 시행 도구의 일이다.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

from quant_rl_trading.analysts.ranker_sources import GROUPS, build  # noqa: E402
from quant_rl_trading.analysts.risk import RiskAnalyst  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market  # noqa: E402
from quant_rl_trading.replay.clock import ReplayClock  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402

DEFAULT_AS_OF = "2025-12-31,2026-03-31,2026-06-30"
#: 세션 개장 전 시각(UTC). 국장 09:00 KST = 00:00 UTC, 미장 09:30 ET ≈ 13:30 UTC — 둘 다 "그 날 개장 직전".
OPEN_UTC = {"KR": timedelta(hours=0), "US": timedelta(hours=13, minutes=30)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="data")
    parser.add_argument("--coverage", action="store_true")
    parser.add_argument("--group", default=",".join(GROUPS))
    parser.add_argument("--market", default="KR,US")
    parser.add_argument("--as-of", default=DEFAULT_AS_OF)
    args = parser.parse_args(argv)
    if not args.coverage:
        parser.error("--coverage 만 있다. IC 는 시행 도구에서 잰다.")
    store = Store(args.root)
    rows = []
    for market in args.market.split(","):
        for day in args.as_of.split(","):
            as_of = datetime.fromisoformat(day).replace(tzinfo=UTC) + OPEN_UTC[market]
            analyst = RiskAnalyst(store, ReplayClock(as_of), market=Market(market))
            for group in args.group.split(","):
                t0 = time.perf_counter()
                raw = build(group, analyst, as_of)
                for column in GROUPS[group]:
                    present = raw[column].notna().sum() if column in raw else 0
                    nonzero = (raw[column].fillna(0.0) != 0).sum() if column in raw else 0
                    rows.append({
                        "market": market, "as_of": day, "group": group, "feature": column,
                        "rows": int(len(raw)), "present": int(present),
                        "missing_share": round(1 - present / len(raw), 3) if len(raw) else 1.0,
                        # 건수 피처는 0 이 "자료 없음" 과 "사건 없음" 을 겸한다 — 0 아닌 비율을 따로 적는다.
                        "nonzero_share": round(nonzero / len(raw), 3) if len(raw) else 0.0,
                        "sec": round(time.perf_counter() - t0, 1),
                    })
                print(f"{market} {day} {group}: {len(raw):,}행 · {time.perf_counter() - t0:.1f}s", flush=True)
    table = pd.DataFrame(rows)
    pd.set_option("display.width", 200)
    print(table.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
