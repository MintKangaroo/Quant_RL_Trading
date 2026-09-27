"""DART 단일판매ㆍ공급계약 원문 → `supply_contracts` 표. 6차 G13(`docs/protocols/ranker-sources-g13-supply-contracts-2026-10.md`)의 입력.

    .venv/bin/python tools/build_supply_contracts.py --start 2025-01 --end 2026-09 [--dry-run]

**관측 시각은 공시 목록에 처음 올라온 시각이다** — `build_prelim_earnings.py` 와 같은 규칙이고 이유도 같다(원문은 나중에 받았고,
원문을 채운 revision 의 시각을 쓰면 한 달 늦게 안 것이 된다). 달마다 "그 달 말 + 3일" 시점의 목록을 다시 읽어 그 시각을 쓴다.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

from quant_rl_trading.collectors import dart_documents as docs  # noqa: E402
from quant_rl_trading.collectors.dart_supply_contract import kind_of, parse  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402

TABLE = "supply_contracts"
COLUMNS = ["entity_id", "valid_from", "observed_at", "doc_id", "title", "raw_path"]


def month(store: Store, period: pd.Period, *, now: datetime, dry_run: bool, tag: str) -> tuple[int, int, int]:
    start = period.start_time.tz_localize("Asia/Seoul").to_pydatetime()
    end = (period.end_time.tz_localize("Asia/Seoul").ceil("s")).to_pydatetime()
    latest = store.get(docs.DOCUMENTS, as_of=now, lookback=(now - start).days + 1, until=end,
                       market="KR", columns=COLUMNS)
    if latest.empty:
        return 0, 0, 0
    latest = latest[latest["valid_from"] >= start].assign(
        kind=lambda f: f["title"].astype(str).map(kind_of)
    )
    latest = latest[latest["kind"].notna()]
    if latest.empty:
        return 0, 0, 0
    then = min(end + timedelta(days=3), now)
    first = store.get(docs.DOCUMENTS, as_of=then, lookback=(then - start).days + 1, until=end, market="KR",
                      columns=["entity_id", "valid_from", "doc_id", "observed_at"])
    seen = {(r.entity_id, r.valid_from, str(r.doc_id)): r.observed_at for r in first.itertuples()}
    have = store.get(TABLE, as_of=now, lookback=(now - start).days + 1, until=end, market="KR", columns=["doc_id"])
    done = set(have["doc_id"].astype(str)) if not have.empty else set()
    rows, unparsed, missing = [], 0, 0
    for r in latest.itertuples():
        if str(r.doc_id) in done:
            continue
        path = str(r.raw_path or "")
        if len(path) <= 1 or path == "None":
            missing += 1
            continue
        c = parse(docs.read_text(Path(path)), kind=str(r.kind))
        if not c.usable:
            # 대부분 **공시유보**(계약상대방 요청)다 — 금액 칸이 '-' 로 비어 온다. 파서 결함이 아니다.
            unparsed += 1
            continue
        observed = seen.get((r.entity_id, r.valid_from, str(r.doc_id)))
        if observed is None:
            observed = r.observed_at
        rows.append({
            "entity_id": r.entity_id, "valid_from": r.valid_from, "observed_at": observed,
            "source": "dart-supply", "market": "KR", "doc_id": str(r.doc_id), "kind": c.kind,
            "amount": c.amount, "recent_sales": c.recent_sales, "sales_ratio": c.sales_ratio,
            "counterparty": c.counterparty, "relation": c.relation,
            "period_start": "" if c.period_start is None else c.period_start.isoformat(),
            "period_end": "" if c.period_end is None else c.period_end.isoformat(),
            "amended": str(r.title).startswith("[기재정정]"),
        })
    if rows and not dry_run:
        store.append(TABLE, rows, ingest_run_id=f"supply-contracts-{period}-{tag}", source="dart-supply")
    return len(rows), unparsed, missing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default="data")
    parser.add_argument("--start", default="2025-01")
    parser.add_argument("--end", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    now = LiveClock().now()
    end = args.end or f"{now:%Y-%m}"
    store = Store(root=Path(args.root))
    total = [0, 0, 0]
    for period in pd.period_range(args.start, end, freq="M"):
        got = month(store, period, now=now, dry_run=args.dry_run, tag=f"{now:%Y%m%dT%H%M%S}")  # 초까지 — 같은 날(UTC) 재실행 충돌 방지(2026-09-28)
        total = [a + b for a, b in zip(total, got, strict=True)]
        print(f"{period}: 적재 {got[0]} · 금액 없음 {got[1]} · 원문 없음 {got[2]}", flush=True)
    print(f"합계 적재 {total[0]} · 금액 없음 {total[1]} · 원문 없음 {total[2]}" + (" (dry-run)" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
