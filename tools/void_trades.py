"""잘못 적힌 체결을 **정정본**으로 무효화한다 — 삭제가 아니다 (불변식 4).

    .venv/bin/python tools/void_trades.py --root data --day 2026-08-28 --source broker --dry-run
    .venv/bin/python tools/void_trades.py --root data/_paper --day 2026-08-27 --source backtest

같은 자연키(entity_id, valid_from, order_id)에 revision 을 올린 행을 quantity 0 · fee 0 ·
tax 0 · source "void" 으로 얹는다. 읽기는 최신 정정본만 보므로 장부에서 그 체결은 사라지되,
원래 행과 "왜 지웠나"(ingest_run_id) 는 남는다.

언제 쓰나 — 체결이 **엉뚱한 장부**에 적혔을 때. 2026-08-28: (1) liquidate 가 모의계좌
청산을 실전 창고에 적었다, (2) 첫 실운용 세션의 워밍업이 계좌에 없는 가상 보유 23종목을
paper 장부에 시뮬레이션했다. 둘 다 계좌는 옳고 장부가 틀린 경우다.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.store import Store  # noqa: E402

TRADES = "trades"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--day", required=True, help="valid_from 날짜 (YYYY-MM-DD)")
    parser.add_argument("--source", required=True, help="무효화할 원래 source (broker|backtest…)")
    parser.add_argument("--entity", nargs="*", help="이 종목만 (기본 전부)")
    parser.add_argument("--reason", default="", help="ingest_run_id 에 남길 한 마디")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    now = datetime.now(UTC)  # invariant-allow: wallclock
    store = Store(root=Path(args.root))
    frame = store.get(TRADES, as_of=now, lookback=60)
    if frame.empty:
        print("trades 가 비었다"); return 1
    day = date.fromisoformat(args.day)
    hit = frame[(pd.to_datetime(frame["valid_from"]).dt.date == day) & (frame["source"] == args.source)]
    if args.entity:
        hit = hit[hit["entity_id"].isin(args.entity)]
    hit = hit[hit["quantity"] > 0]
    if hit.empty:
        print("무효화할 행이 없다 (이미 정정됐거나 조건 불일치)"); return 0
    rows = []
    for r in hit.itertuples(index=False):
        rows.append({
            "entity_id": r.entity_id, "valid_from": r.valid_from, "observed_at": now,
            "source": "void", "revision": int(r.revision) + 1, "market": r.market, "side": r.side,
            "quantity": 0.0, "price": float(r.price), "currency": r.currency, "fee": 0.0, "tax": 0.0,
            "order_id": r.order_id,
        })
        print(f"  void {r.entity_id} {r.side} {r.quantity:g} @ {r.price:g} (rev {r.revision}→{int(r.revision)+1})")
    if args.dry_run:
        print(f"드라이런 — {len(rows)}행 적지 않는다"); return 0
    tag = (args.reason or "void").replace(" ", "-")[:40]
    written = store.append(TRADES, rows, ingest_run_id=f"void-{args.day}-{tag}-{now:%H%M%S}")
    print(f"정정본 {written}행 적재 → {args.root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
