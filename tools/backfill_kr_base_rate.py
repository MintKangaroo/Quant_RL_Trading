#!/usr/bin/env python
"""한국은행 기준금리를 **시계열로** 창고에 넣는다 — 한 번만 돌린다.

    uv run python tools/backfill_kr_base_rate.py --start 2023-06

## 왜 `macro_releases` 가 아니라 `indices` 인가

`macro_releases` 에도 `KR:BASE_RATE` 가 있다. 그런데 그 표는 **"언제 무엇이
발표되나"** 를 담는 일정 표이고, `valid_from` 이 설계상 "우리가 그 사실을 안
시각"(=수집 시각)이다(`macro_source` 모듈 독스트링). 그래서 과거 `as_of` 로
조회하면 **한 행도 안 걸린다.**

실측 2026-08-19: RL 환경의 한미 금리차 칸이 200k 스텝 내내 0 이었다.
설계상 빈 칸(섹터·USD)과 달리 이건 **읽는 표가 틀린 것**이었다.

시계열로 읽어야 하는 값은 `indices` 에 온다. 미국 `DFF` 도 같은 이유로
`US:RATE:FED_FUNDS` 로 그리 갔다(`FRED_INDICES`).

## 월별이라 그 달의 첫날로 찍는다

기준금리는 월 단위 계열이다. `valid_from` 을 그 달 1일로 두면 그달 내내
같은 값이 잡힌다 — 금리는 실제로 그렇게 움직인다(금통위 전까지 안 바뀐다).

**공표 지연을 지킨다.** `observed_at` 은 그 달 1일 + `publication_lag_days`.
그래야 그 시점에 알 수 없었던 값을 학습이 못 본다.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.collectors.macro_source import ECOS_STATS, EcosSource  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from tools.backfill import build_store  # noqa: E402

ENTITY = "KR:RATE:BASE"
SOURCE = "ecos"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2023-06", help="YYYY-MM")
    parser.add_argument("--end", default=None, help="YYYY-MM (생략하면 이번 달)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    load_env()
    key = os.environ.get("ECOS_API_KEY") or os.environ.get("BOK_API_KEY")
    if not key:
        print("ECOS_API_KEY 가 없다.", file=sys.stderr)
        return 2

    # 한 번 손으로 돌리는 백필 도구의 CLI 기본값이다 — 창고에 들어가는
    # valid_from·observed_at 은 ECOS 응답의 기간에서만 오고, 이 값은 조회
    # 구간의 끝을 정할 뿐이다 (불변식 2).
    end = args.end or datetime.now(UTC).strftime("%Y-%m")  # invariant-allow: wallclock
    rows_raw = EcosSource(key).search(
        ECOS_STATS["BASE_RATE"],
        start=args.start.replace("-", ""),
        end=end.replace("-", ""),
        # **넉넉히 준다.** 기본 10 은 일일 수집용이고, 그대로 두면 39개월을
        # 물어도 10행만 와서 그게 전부인 것처럼 읽힌다.
        limit=600,
    )
    if not rows_raw:
        # **0행은 실패가 아니라 "그 구간에 발표가 없다" 일 수도 있다.**
        # 무효한 키는 HTML 503 을 내므로 위에서 예외로 터진다.
        print("ECOS 응답이 0행이다 — 구간을 넓혀 볼 것.", file=sys.stderr)
        return 1

    lag = int(ECOS_STATS["BASE_RATE"].get("publication_lag_days", "1"))
    rows: list[dict[str, object]] = []
    for item in rows_raw:
        period = str(item.get("TIME") or "")
        value = item.get("DATA_VALUE")
        if len(period) != 6 or value in (None, ""):
            continue
        year, month = int(period[:4]), int(period[4:])
        day = datetime(year, month, 1, tzinfo=UTC)
        rows.append({
            "entity_id": ENTITY,
            "valid_from": day,
            # 공표 지연을 지킨다 — 그 시점에 알 수 없었던 값을 학습이 보면 안 된다.
            "observed_at": day + timedelta(days=lag),
            "source": SOURCE,
            "market": "KR",
            # `indices` 에 name 컬럼은 없다 — 계열 이름은 entity_id 가 진다.
            "board": "rate",
            "open": None, "high": None, "low": None,
            "close": float(value),
            "volume": None, "value": None,
        })

    print(f"{ENTITY} — {len(rows)}행 ({args.start} ~ {end})")
    if not rows:
        return 1
    for row in rows[:3]:
        print(f"  {row['valid_from']:%Y-%m} {row['close']}%")
    if args.dry_run:
        print("(dry-run — 적재하지 않았다)")
        return 0

    store = build_store(None)
    # run_id 에 구간을 박는다 — 같은 구간을 두 번 넣으려 하면 창고가 막는다.
    run_id = f"kr-base-rate-{args.start}-{end}"
    if store.ingest_run_recorded("indices", run_id):
        print("이미 적재된 구간이다 — 건너뛴다.")
        return 0
    written = store.append("indices", rows, ingest_run_id=run_id, source=SOURCE)
    print(f"indices 적재: {written}행")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
