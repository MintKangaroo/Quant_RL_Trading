"""옛 Yahoo 환율 행을 SMBS 정정본으로 바로잡는다 — 한 번 쓰는 도구 (2026-10-04 사용자 결정 ①).

    .venv/bin/python tools/correct_fx_yahoo_rows.py --dry-run
    .venv/bin/python tools/correct_fx_yahoo_rows.py

Yahoo `KRW=X` 의 d 라벨 값은 d−1 마감에 가까웠다(`docs/design/data-contract.md` §4-2). 창고에서 **최신 행이
Yahoo 인 날**마다 `revision+1` 정정본을 붙인다. 값은 같은 날짜의 SMBS 종가(LS `t3518`), 일요일 라벨이면 직전
거래일 SMBS. 기존 행은 건드리지 않는다(불변식 4). `observed_at` 은 지금이다(불변식 3) — 그래서 과거 세션 시각의
회계 스냅샷은 바뀌지 않는다. 그때 알 수 있던 값은 Yahoo 였다.

두 번 돌려도 안전하다: 정정 뒤엔 최신 행이 Yahoo 가 아니라 할 일이 없다.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.collectors.ls_investinfo import KIND_FX, DailyBar, fetch_daily  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from tools.backfill import build_store  # noqa: E402
from tools.collect_fx_ls import ENTITY, SOURCE, SYMBOL, TABLE, ls_client_for_queries  # noqa: E402

SEOUL = ZoneInfo("Asia/Seoul")
OLD_SOURCE = "yahoo"
#: 볼 창(달력일). Yahoo 수집기는 2026-08 말에 생겼다.
LOOKBACK = 120
#: SMBS 봉 수 — 창을 덮을 만큼.
COUNT = 120


def plan_corrections(have: Any, bars: list[DailyBar], *, now: Any) -> list[dict[str, Any]]:
    """최신 행이 Yahoo 인 날마다 정정본 한 줄. 그날(또는 직전) SMBS 가 없으면 그날은 건너뛴다."""
    if have is None or have.empty:
        return []
    closes = {bar.day: bar.close for bar in bars}
    days = sorted(closes)
    rows: list[dict[str, Any]] = []
    stale = have[(have["entity_id"] == ENTITY) & (have["source"] == OLD_SOURCE)]
    for row in stale.sort_values("valid_from").itertuples(index=False):
        day: date = row.valid_from.astimezone(SEOUL).date()
        prior = [d for d in days if d <= day]
        if not prior or (day - prior[-1]).days > 4:
            continue  # 그날 근처 SMBS 가 없다 — 짐작으로 채우지 않는다
        rows.append({
            "entity_id": ENTITY,
            "valid_from": row.valid_from,
            "observed_at": now,
            "source": SOURCE,
            "revision": int(row.revision) + 1,
            "rate": float(closes[prior[-1]]),
            "_old": float(row.rate),
            "_from": prior[-1],
        })
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    load_env()
    now = LiveClock().now()
    store = build_store(args.data_root)
    have = store.get(TABLE, as_of=now, lookback=LOOKBACK)
    client = ls_client_for_queries(store, as_of=now)
    try:
        bars = fetch_daily(client, KIND_FX, SYMBOL, count=COUNT)
    finally:
        client.close()
    rows = plan_corrections(have, bars, now=now)
    for r in rows:
        bp = (r["rate"] / r["_old"] - 1.0) * 1e4
        print(f"  {r['valid_from'].astimezone(SEOUL).date()} yahoo {r['_old']:,.2f} → SMBS({r['_from']}) "
              f"{r['rate']:,.2f}  {bp:+.1f}bp  rev {r['revision']}")
    if not rows:
        print("정정할 Yahoo 행이 없다.")
        return 0
    if args.dry_run:
        print(f"드라이런 — 정정본 {len(rows)}행 적지 않는다")
        return 0
    clean = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]
    written = store.append(TABLE, clean, ingest_run_id=f"fx-correct-yahoo-{now:%Y%m%dT%H%M%S}", source=SOURCE)
    print(f"fx 정정본 적재: {written}행 ({SOURCE}, revision+1)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
