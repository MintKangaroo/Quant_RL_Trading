"""KRX 개별주식선물(N3) 백필 — ``stock_futures`` 표 (docs/design/data-contract.md §4-3).

    QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB QUANT_RL_DUCKDB_THREADS=2 nice -n 10 \\
        .venv/bin/python tools/backfill_stock_futures.py --start 2015-08-03 --dry-run
    ... tools/backfill_stock_futures.py --start 2015-08-03            # 끝은 기본 어제

**같은 수집 뼈대**(``PanelBackfiller`` · ``KrxOpenApi`` · ``publication_policy`` · RawArchive · store.append)를 쓴다 —
observed_at 은 다른 KRX Open API 패널 백필과 같은 규칙(세션 마감 + 공표 지연). 하루 2콜(유가·코스닥).

- **재시작**: 세션마다 매니페스트(run id ``bf-stock-futures-KR-YYYYMMDD``)가 남아, 다시 돌리면 이미 적재한 세션을 건너뛴다.
  0행 세션은 매니페스트를 남기지 않으므로 다시 받는다.
- **멈춤**: 호출 실패(인증·HTTP 오류·재시도 소진)면 그 자리에서 멈추고 rc=3. 거래일에 두 시장이 다 0행인 날이 연달아
  ``--max-empty`` 번이면 한도·차단을 의심해 멈춘다(rc=4) — 빈 응답이 휴장일로 위장되지 않게.
- **매핑 정정**(``--remap``): 별칭을 늘린 뒤, 창고의 ``map_method='none'`` 행을 다시 매핑해 **revision 을 올린 새 행**으로 적는다
  (불변식 4). observed_at 은 원래 행 그대로 둔다 — 시세 사실은 같고 바뀌는 것은 식별자(참조)뿐이라, 지금 시각으로 찍으면
  과거 as_of 조회가 정정을 못 본다. ``map_method`` 앞에 ``remap:`` 을 붙여 정정 행임을 남긴다.
- **매핑 실패 목록**: 기초자산을 못 찾은 상품명을 첫날·끝날·행 수와 함께 ``logs/stock-futures-unmapped.csv`` 에 적는다.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.collectors.krx_openapi import KrxOpenApi  # noqa: E402
from quant_rl_trading.collectors.krx_stock_futures import (  # noqa: E402
    UnderlyingResolver,
    stock_futures_panel,
)
from quant_rl_trading.collectors.market_hours import Market  # noqa: E402
from quant_rl_trading.collectors.outcome import Verdict  # noqa: E402
from quant_rl_trading.collectors.panels import PanelBackfiller  # noqa: E402
from quant_rl_trading.collectors.publication import publication_policy  # noqa: E402
from quant_rl_trading.collectors.raw import RawArchive  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from tools.backfill import build_store  # noqa: E402

UNMAPPED_CSV = Path("logs/stock-futures-unmapped.csv")
#: 지연 기록을 이만큼 세션마다 한 파일로 —
#: 세션마다 쓰면 ingest_latency 에 파일이 수천 개 생긴다(10/11).
LATENCY_BATCH = 100


class RecordingResolver(UnderlyingResolver):
    """매핑 결과를 세는 매핑기. 실패한 상품명은 첫날·끝날·건수를 남긴다."""

    def __init__(self, inner: UnderlyingResolver) -> None:
        super().__init__(spans=inner.spans, aliases=inner.aliases)
        self.methods: dict[str, int] = {}
        self.failed: dict[str, list[Any]] = {}

    def resolve(self, prod_nm: str, day: date) -> tuple[str | None, str]:
        entity, method = super().resolve(prod_nm, day)
        self.methods[method] = self.methods.get(method, 0) + 1
        if entity is None:
            rec = self.failed.setdefault(prod_nm, [day, day, 0])
            rec[0], rec[1], rec[2] = min(rec[0], day), max(rec[1], day), rec[2] + 1
        return entity, method

    def dump(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["prod_nm", "first_day", "last_day", "rows"])
            for name, (a, b, n) in sorted(self.failed.items(), key=lambda kv: -kv[1][2]):
                w.writerow([name, a.isoformat(), b.isoformat(), n])


def remap(store: Any, resolver: UnderlyingResolver, now: Any) -> int:
    from quant_rl_trading.store.tables import get_spec

    frame = store.get("stock_futures", as_of=now, market="KR", lookback=(now.date() - date(2009, 1, 1)).days)
    todo = frame[frame["underlying_id"].isna()]
    keep = [c for c in get_spec("stock_futures").all_columns if c in todo.columns and c not in ("ingest_run_id", "row_hash")]
    rows = []
    for rec in todo[keep].to_dict("records"):
        day = rec["valid_from"].date()
        entity, method = resolver.resolve(rec["prod_nm"], day)
        if entity is None:
            continue
        rec = {k: (None if isinstance(v, float) and v != v else v) for k, v in rec.items()}
        for col in ("valid_from", "observed_at"):
            rec[col] = rec[col].to_pydatetime() if hasattr(rec[col], "to_pydatetime") else rec[col]
        rec.update(underlying_id=entity, map_method=f"remap:{method}", revision=int(rec["revision"]) + 1)
        rows.append(rec)
    print(f"매핑 없는 행 {len(todo):,} · 이번에 매핑됨 {len(rows):,}", flush=True)
    if rows:
        written = store.append("stock_futures", rows, ingest_run_id=f"remap-stock-futures-{now.strftime('%Y%m%dT%H%M%S')}")
        print(f"정정 행 {written:,} 적재", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, default=None, help="기본: 어제(KST 기준 날짜)")
    parser.add_argument("--pause", type=float, default=1.0, help="세션 사이 최소 간격(초). 설정값보다 작으면 설정값")
    parser.add_argument("--max-empty", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--remap", action="store_true", help="매핑 실패 행을 다시 매핑해 revision+1 로 적고 끝낸다")
    args = parser.parse_args(argv)

    load_env()
    clock = LiveClock()
    now = clock.now()
    end = args.end or (now.date() - timedelta(days=1))
    store = build_store(None)
    resolver = RecordingResolver(UnderlyingResolver.from_store(store, as_of=now))
    print(f"매핑기: 이름 {len(resolver.spans):,}개(universe KR 전 이력)", flush=True)
    if args.remap:
        return remap(store, resolver, now)

    backfiller = PanelBackfiller(
        store=store, source=KrxOpenApi(), clock=clock, archive=RawArchive(root=store.root),
        policy=publication_policy(store, Market.KR, clock=clock), panel=stock_futures_panel(resolver), market=Market.KR,
        latency_batch=LATENCY_BATCH,
    )
    sessions = backfiller.plan(args.start, end)
    pending = backfiller.pending(sessions)
    print(f"KR {args.start} ~ {end} 거래일 {len(sessions)}개 · 이미 적재 {len(sessions) - len(pending)} · 남은 {len(pending)}",
          flush=True)
    if args.dry_run or not pending:
        return 0

    pause = max(float(store.config("backfill.session_pause_ms", as_of=now)) / 1000.0, args.pause)
    try:
        return _run(backfiller, pending, pause, args.max_empty, resolver)
    finally:
        backfiller.flush_latency()


def _run(backfiller: PanelBackfiller, pending: list[date], pause: float, max_empty: int,
         resolver: RecordingResolver) -> int:
    total, empty_run, rc = 0, 0, 0
    for i, day in enumerate(pending, 1):
        if i > 1:
            time.sleep(pause)  # 소스를 두들겨 차단당하지 않기 위한 예의
        result = backfiller.run_session(day)
        total += result.rows
        if result.verdict is Verdict.FETCH_FAILED:
            print(f"[{i}/{len(pending)}] {day} FAIL — 멈춘다: {result.error}", flush=True)
            rc = 3
            break
        if not result.skipped and result.rows == 0:
            empty_run += 1
            print(f"[{i}/{len(pending)}] {day} 0행 [{result.verdict}] (연속 {empty_run})", flush=True)
            if empty_run >= max_empty:
                print(f"거래일 0행이 {empty_run}번 연달아 — 한도·차단 의심, 멈춘다", flush=True)
                rc = 4
                break
            continue
        empty_run = 0
        if i % 50 == 0 or i == len(pending):
            print(f"[{i}/{len(pending)}] {day} ok rows={result.rows} 누적 {total:,} 매핑 {resolver.methods}", flush=True)
            resolver.dump(UNMAPPED_CSV)
    resolver.dump(UNMAPPED_CSV)
    print(f"끝 — 적재 {total:,}행 · 매핑 {resolver.methods} · 실패 상품 {len(resolver.failed)}개 → {UNMAPPED_CSV}", flush=True)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
