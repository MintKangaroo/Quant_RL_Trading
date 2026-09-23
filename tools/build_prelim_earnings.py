"""DART 잠정실적 원문 → `prelim_earnings` 표. 6차 G8(`docs/protocols/ranker-sources-g8-prelim-earnings-2026-09.md`)의 입력.

    .venv/bin/python tools/build_prelim_earnings.py --start 2025-01 --end 2026-09 [--dry-run]

**관측 시각은 공시 목록에 처음 올라온 시각이다.** 원문은 나중에(예: 8/12 공시를 9/17 에) 받았고, 원문을 채운 revision 의 `observed_at` 은
그 수집 시각이다 — 그걸 쓰면 한 달 늦게 안 것이 되어 60세션 창에서 거의 다 빠진다. 그래서 달마다 "그 달 말 + 3일" 시점의 목록을 다시 읽어
**그때 알 수 있었던 revision** 의 `observed_at` 을 쓴다(document_embeddings 와 같은 규칙: 파싱은 새 사실이 아니다).
실전에서는 원문이 공시 다음 날 01:20 에 들어오므로 다음 세션 개장 전에 쓸 수 있다 — 이 규칙과 어긋나지 않는다.
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
from quant_rl_trading.collectors.dart_prelim import basis, parse  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402

TABLE = "prelim_earnings"
COLUMNS = ["entity_id", "valid_from", "observed_at", "doc_id", "title", "raw_path"]


def month(store: Store, period: pd.Period, *, now: datetime, dry_run: bool) -> tuple[int, int, int]:
    start = period.start_time.tz_localize("Asia/Seoul").to_pydatetime()
    end = (period.end_time.tz_localize("Asia/Seoul").ceil("s")).to_pydatetime()
    # lookback 은 as_of 에서 거꾸로 센다(until 이 아니다) — 그래서 as_of 마다 따로 잡는다.
    latest = store.get(docs.DOCUMENTS, as_of=now, lookback=(now - start).days + 1, until=end, market="KR", columns=COLUMNS)
    if latest.empty:
        return 0, 0, 0
    latest = latest[(latest["valid_from"] >= start) & latest["title"].astype(str).str.contains("잠정")]
    latest = latest[latest["title"].map(lambda t: basis(str(t)) is not None)]
    if latest.empty:
        return 0, 0, 0
    # 그 달 말 + 3일에 알 수 있었던 목록 — 첫 revision 의 관측 시각
    then = min(end + timedelta(days=3), now)
    first = store.get(docs.DOCUMENTS, as_of=then, lookback=(then - start).days + 1, until=end, market="KR",
                      columns=["entity_id", "valid_from", "doc_id", "observed_at"])
    seen = {(r.entity_id, r.valid_from, str(r.doc_id)): r.observed_at for r in first.itertuples()}
    rows, unparsed, missing = [], 0, 0
    for r in latest.itertuples():
        path = str(r.raw_path or "")
        if len(path) <= 1 or path == "None":
            missing += 1
            continue
        p = parse(docs.read_text(Path(path)))
        if p is None or not p.usable:
            unparsed += 1
            continue
        observed = seen.get((r.entity_id, r.valid_from, str(r.doc_id)))
        if observed is None:  # 그 달 말+3일에 목록에 없던 공시(늦게 들어온 목록) — 실제로 안 시각을 쓴다
            observed = r.observed_at
        rows.append({
            "entity_id": r.entity_id, "valid_from": r.valid_from, "observed_at": observed, "source": "dart-prelim",
            "market": "KR", "doc_id": str(r.doc_id), "basis": basis(str(r.title)),
            "amended": str(r.title).startswith("[기재정정]"),
            "sales_cur": p.sales_cur, "sales_base": p.sales_base, "op_cur": p.op_cur, "op_base": p.op_base,
        })
    if rows and not dry_run:
        store.append(TABLE, rows, ingest_run_id=f"prelim-earnings-{period}", source="dart-prelim")
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
        got = month(store, period, now=now, dry_run=args.dry_run)
        total = [a + b for a, b in zip(total, got, strict=True)]
        print(f"{period}: 적재 {got[0]} · 파싱 실패 {got[1]} · 원문 없음 {got[2]}", flush=True)
    print(f"합계 적재 {total[0]} · 파싱 실패 {total[1]} · 원문 없음 {total[2]}" + (" (dry-run)" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
