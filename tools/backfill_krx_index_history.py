"""KRX 통합지수(KRX 300·KRX 100·KRX TMI …) 과거분 — 2022-04-21 앞의 구멍을 메운다.

    .venv/bin/python tools/backfill_krx_index_history.py --start 2020-01-02 --dry-run
    .venv/bin/python tools/backfill_krx_index_history.py --start 2020-01-02

## 구멍이 생긴 이유 (2026-10-03 확인)

`tools/backfill.py --table indices-krx`(KRX Open API `/idx/krx_dd_trd`)와 옛 pykrx 경로 `--table indices`
(업종지수, source=krx)가 **같은 run key `indices`** 를 쓴다 → run id `bf-indices-KR-YYYYMMDD` 가 겹친다.
pykrx 경로가 2021-08-12~2022-04-20 의 168세션을 먼저 적재해 두었기 때문에, Open API 경로는 그 세션을
"이미 적재" 로 건너뛰었다. 그 앞(2021-08-12 이전)은 5년 창 밖이었다. 그래서 창고의 KRX 통합지수 첫날이 2022-04-21 이다.
대표지수 경로(`indices-board`)는 같은 충돌을 겪고 run key 를 갈랐다(panels.py 주석) — 통합지수는 안 갈랐다.

## 이 도구가 하는 것

**같은 수집기**(`PanelBackfiller` + `OPENAPI_PANELS["indices-krx"]` · `KrxOpenApi` · `publication_policy`)를
run key 만 `indices-krx-history` 로 바꿔 돌린다 — observed_at 은 백필 규칙(세션 종료 + 공표 지연, data-contract §백필),
원본은 RawArchive, 쓰기는 store.append. 실전 패널의 run key 는 **건드리지 않는다**: 바꾸면 매일 도는 `--sessions` 실행이
이미 적재한 최근 세션을 새 run id 로 다시 받아 같은 값을 한 벌 더 쌓는다.

중복 방지: `--end` 는 창고에 KRX 300 이 처음 나타나는 날 **전날**로 자동 제한된다. 그 앞 세션에 KRX 통합지수 행이 하나라도
이미 있으면 그 세션은 건너뛴다(append-only 창고에 같은 사실을 두 번 적지 않는다).
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.collectors.krx_openapi import KrxOpenApi  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market  # noqa: E402
from quant_rl_trading.collectors.panels import OPENAPI_PANELS, PanelBackfiller  # noqa: E402
from quant_rl_trading.collectors.publication import publication_policy  # noqa: E402
from quant_rl_trading.collectors.raw import RawArchive  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from tools.backfill import build_store  # noqa: E402

RUN_KEY = "indices-krx-history"
PROBE = "KR:IDX:KRX 300"


def present_days(store, start: date, end: date) -> set[date]:
    """그 구간에 KRX 통합지수(대표로 KRX 300) 행이 이미 있는 세션."""
    now = LiveClock().now()
    frame = store.get("indices", as_of=now, entity=[PROBE], market="KR",
                      lookback=(now.date() - start).days + 2,
                      until=datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=UTC),
                      columns=["entity_id", "valid_from"])
    return set(frame["valid_from"].dt.date) if not frame.empty else set()


def first_probe_day(store) -> date | None:
    now = LiveClock().now()
    frame = store.get("indices", as_of=now, entity=[PROBE], market="KR", lookback=(now.date() - date(2015, 1, 1)).days,
                      columns=["entity_id", "valid_from"])
    return min(frame["valid_from"].dt.date) if not frame.empty else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    load_env()
    clock = LiveClock()
    store = build_store(None)
    first = first_probe_day(store)
    if first is None:
        print(f"{PROBE} 가 창고에 없다 — 이 도구는 구멍 메우기 전용이다. tools/backfill.py --table indices-krx 를 써라.",
              file=sys.stderr)
        return 2
    end = first - timedelta(days=1)
    panel = replace(OPENAPI_PANELS["indices-krx"], run_key=RUN_KEY)
    backfiller = PanelBackfiller(store=store, source=KrxOpenApi(), clock=clock, archive=RawArchive(root=store.root),
                                 policy=publication_policy(store, Market.KR, clock=clock), panel=panel, market=Market.KR)
    sessions = backfiller.plan(args.start, end)
    have = present_days(store, args.start, end)
    pending = [d for d in backfiller.pending(sessions) if d not in have]
    print(f"KR {args.start} ~ {end} (창고의 {PROBE} 첫날 {first} 전날까지) 거래일 {len(sessions)}개 · "
          f"이미 행 있음 {len(have)} · 남은 {len(pending)}", flush=True)
    if args.dry_run or not pending:
        return 0

    pause = float(store.config("backfill.session_pause_ms", as_of=clock.now())) / 1000.0
    failures = 0
    for i, day in enumerate(pending, 1):
        if i > 1:
            time.sleep(pause)  # 소스를 두들겨 차단당하지 않기 위한 예의
        result = backfiller.run_session(day)
        status = "SKIP" if result.skipped else ("FAIL" if result.error else "ok")
        failures += bool(result.error)
        if status != "ok" or i % 50 == 0 or i == len(pending):
            print(f"[{i}/{len(pending)}] {day} {status} rows={result.rows}"
                  + (f"  {result.error}" if result.error else ""), flush=True)
    print(f"완료 — {len(pending)}세션, 실패 {failures}", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
