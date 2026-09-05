#!/usr/bin/env python
"""스캔 결과를 ``prices`` 정정본으로 적재한다.

    uv run python tools/scan_corporate_actions.py --from-filings --out scan.json
    uv run python tools/backfill_adj_factor.py --scan scan.json --dry-run
    uv run python tools/backfill_adj_factor.py --scan scan.json

스캔(``scan_corporate_actions.py``)과 쓰기를 나눈 이유: 스캔이 몇 시간짜리라
중간에 죽으면 다시 못 돌리고, 무엇을 쓸지 사람이 먼저 볼 수 있어야 한다.

## 무엇을 쓰나

사건 하나당 **한 행**이다. 그 세션의 기존 행을 통째로 복사하고 ``adj_factor``
하나를 얹은 ``revision+1`` 행이다. 값 컬럼을 비워 두면 게이트가 정정본을 고른
뒤 그 세션 시세가 통째로 null 이 된다 (``corporate_actions.revision_rows``).

관측시각은 **원본 행의 것을 그대로** 쓴다. 조정 기준가는 발효일 아침에
거래소가 공표하므로, 그날 종가를 알 수 있었던 시각이면 계수도 알 수 있었다.

## 이미 들어간 사건은 다시 안 쓴다

``adj_factor`` 가 이미 같은 값으로 차 있으면 건너뛴다. 매일 도는 경로에서
같은 사건에 revision 만 계속 쌓이면, 정정 이력이 정정이 아니라 잡음이 된다.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.collectors.corporate_actions import (  # noqa: E402
    MIN_LOG_FACTOR_CONFIG,
    PRICE_VALUE_COLUMNS,
    CorporateAction,
    revision_rows,
    run_id,
)
from quant_rl_trading.collectors.market_hours import Market  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import DuplicateIngestRun, Store  # noqa: E402
from quant_rl_trading.store.prices import read_prices  # noqa: E402

#: 한 번에 적재할 행 수. 창고는 ``observed_date`` 로 파티션하므로 배치가 작을수록
#: 파일이 잘게 쪼개진다 — 미장 백필이 그렇게 파일 247만 개를 만들었다.
BATCH_ROWS = 2_000

#: 배율이 이 범위를 벗어나면 쓰지 않는다. 1,000배 분할·감자는 실재하지 않는다.
#: 계수를 뒤집어 저장하거나 누적을 저장하면 여기서 걸린다.
MAX_ABS_LOG_FACTOR = math.log(1_000.0)


def _load(path: Path) -> list[CorporateAction]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    actions: list[CorporateAction] = []
    for entity_id, item in payload.items():
        if item.get("status") != "ok":
            continue
        for event in item.get("events") or []:
            actions.append(
                CorporateAction(
                    entity_id=entity_id,
                    effective_on=date.fromisoformat(event["effective_on"]),
                    factor=float(event["factor"]),
                    ratio_before=float(event["ratio_before"]),
                    ratio_after=float(event["ratio_after"]),
                )
            )
    return sorted(actions, key=lambda item: (item.effective_on, item.entity_id))


def _sane(action: CorporateAction, *, min_log_factor: float) -> str | None:
    """쓰기 전 마지막 검사. 이유를 돌려주면 그 사건은 버린다."""
    if not math.isfinite(action.factor) or action.factor <= 0:
        return f"배율이 {action.factor}"
    size = abs(action.log_factor)
    if size <= min_log_factor:
        return f"|log| {size:.5f} — 반올림 잡음 수준이다"
    if size >= MAX_ABS_LOG_FACTOR:
        return f"배율 {action.factor:,.1f} — 계수가 뒤집혔거나 누적이 실렸다"
    return None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan", type=Path, required=True)
    parser.add_argument("--market", default="KR", choices=["KR", "US"])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--daily", action="store_true",
        help="일일 경로. run_id 에 오늘 날짜를 넣는다 — 안 넣으면 첫 적재 뒤로 "
        "매일이 같은 id 가 되어 **영원히 건너뛴다**(2026-08-18 발견)",
    )
    args = parser.parse_args(argv)

    load_env()
    clock = LiveClock()
    now = clock.now()
    store = Store()
    market = Market.KR if args.market == "KR" else Market.US

    # 일일 경로만 날짜를 붙인다. 5년치 백필은 한 번만 도는 작업이라 날짜를
    # 넣으면 재실행이 중복을 못 막는다.
    stamp = now.astimezone().strftime("%Y%m%d") if args.daily else ""
    min_log_factor = float(store.config(MIN_LOG_FACTOR_CONFIG, as_of=now))
    actions = _load(args.scan)
    print(f"스캔 파일에 사건 {len(actions)}건")

    kept: list[CorporateAction] = []
    for action in actions:
        reason = _sane(action, min_log_factor=min_log_factor)
        if reason is None:
            kept.append(action)
        else:
            print(f"  버림 {action.entity_id} {action.effective_on}: {reason}")
    print(f"검사 통과 {len(kept)}건")
    if not kept:
        return 0

    # 사건이 걸린 세션의 **현재 창고 행**을 통째로 읽는다. 정정본은 값 컬럼을
    # 전부 다시 써야 하기 때문이다.
    oldest = min(action.effective_on for action in kept)
    entities = sorted({action.entity_id for action in kept})
    frame = read_prices(
        store,
        as_of=now,
        entity=entities,
        lookback=(now.date() - oldest).days + 5,
        market=str(market),
        columns=[
            "entity_id", "valid_from", "observed_at", "source", "revision",
            "adj_factor", *PRICE_VALUE_COLUMNS,
        ],
    )
    existing = {
        (str(row["entity_id"]), row["valid_from"].date()): row
        for row in frame.to_dict(orient="records")
    }
    print(f"창고에서 읽은 세션 {len(existing):,}개 · {frame['entity_id'].nunique()}종목")

    # 이미 같은 값이 든 사건은 건너뛴다. revision 만 쌓이면 정정 이력이 잡음이 된다.
    fresh = []
    already = 0
    for action in kept:
        row = existing.get((action.entity_id, action.effective_on))
        if row is None:
            continue
        stored = row.get("adj_factor")
        if stored is not None and abs(float(stored) - action.factor) < 1e-9:
            already += 1
            continue
        fresh.append(action)
    missing = len(kept) - len(fresh) - already
    print(f"쓸 사건 {len(fresh)}건 · 이미 같은 값 {already}건 · 그 세션 행이 없음 {missing}건")

    rows = revision_rows(fresh, existing)
    if not rows:
        return 0

    if args.dry_run:
        print(f"\n[dry-run] {len(rows)}행을 쓸 것이다. 앞 10건:")
        for row in rows[:10]:
            print(
                f"  {row['entity_id']} {row['valid_from'].date()} "
                f"rev={row['revision']} factor={row['adj_factor']:.6f} "
                f"close={row['close']}"
            )
        return 0

    written = 0
    for index in range(0, len(rows), BATCH_ROWS):
        batch = rows[index : index + BATCH_ROWS]
        ingest = run_id(market, index // BATCH_ROWS, stamp=stamp)
        try:
            written += store.append("prices", batch, ingest_run_id=ingest)
        except DuplicateIngestRun:
            # 이미 들어간 배치다. 덮어쓰지 않는다 (불변식 4).
            print(f"  {ingest} 는 이미 적재됐다 — 건너뛴다")
    print(f"\n적재 {written}행")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
