#!/usr/bin/env python
"""국장 일봉 구멍 메우기 — KRX 가 막힌 날의 봉을 LS `t8410`(일봉 이력)로 채운다.

    .venv/bin/python tools/repair_prices_ls.py --day 2026-09-01 [--limit 5]

**정본은 여전히 KRX 다** (market_collector.collect_ohlcv 의 주석). 이 도구는 KRX 가 그날을
못 준 경우(차단·0건)에만, **그 하루의 봉만** 정정본(revision 1)으로 얹는다. 관측시각은
백필과 같은 규약 — 그 세션의 공표 시각(16:00 KST). 그래야 그날 회계·차트가 봉을 본다.

2026-09-01: KRX 22:40 JSONDecodeError(차단), LS 15:52 미실행, 06:30 보충이 개장 전 스텁
(시·고·저·거래량 0)을 2,874행 적음 → 이 도구로 메웠다.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.collectors import market_collector as mc  # noqa: E402
from quant_rl_trading.collectors.ls_client import PATH_CHART  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market  # noqa: E402
from quant_rl_trading.collectors.publication import publication_policy  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from tools.backfill import build_store  # noqa: E402
from tools.collect_prices_ls import universe_codes  # noqa: E402

TABLE = "prices"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", required=True)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--count", type=int, default=5, help="종목당 받을 봉 수(최근 N)")
    args = parser.parse_args(argv)
    load_env()
    clock = LiveClock(); now = clock.now()
    day = date.fromisoformat(args.day)
    store = build_store(args.data_root)
    published = publication_policy(store, Market.KR, clock=clock).for_session(day)
    # 배관 확인(--limit)과 전체 실행의 run id 를 가른다 — 확인이 전체를 막지 않게.
    run_id = f"repair-prices-ls-{day.isoformat()}" + (f"-smoke{args.limit}" if args.limit else "-full")
    if store.ingest_run_recorded(TABLE, run_id):
        print(f"{day} 는 이미 메웠다"); return 0
    codes = universe_codes(store, as_of=now)[: args.limit]
    from quant_rl_trading.collectors.ls_client import LSClient, LSCredentials
    from tools.verify_live_order import resolve_profile
    profile = resolve_profile(store, market="KR", as_of=now)
    client = LSClient(credentials=LSCredentials.from_env(prefix=profile.env_prefix),
                      live_trading=False, min_interval_sec=profile.min_interval_sec)
    rows: list[dict] = []; fails = 0; missing = 0
    for i, code in enumerate(codes, 1):
        try:
            payload = client.request_tr(PATH_CHART, "t8410", {"t8410InBlock": {
                "shcode": code, "gubun": "2", "qrycnt": args.count, "sdate": "", "edate": "99999999",
                "cts_date": "", "comp_yn": "N", "sujung": mc.SUJUNG_RAW}})
            bars = mc.normalize_ohlcv(payload.get("t8410OutBlock1") or [], entity_id=f"KR:{code}",
                                      market=Market.KR, observed_at=published)
        except Exception as error:  # noqa: BLE001 — 한 종목이 전체를 막지 않는다
            fails += 1
            if fails <= 5:
                print(f"  {code}: 실패 {type(error).__name__}: {str(error)[:80]}", file=sys.stderr)
            continue
        hit = [b for b in bars if b["valid_from"].date() == day and (b.get("low") or 0) > 0]
        if not hit:
            missing += 1; continue
        row = dict(hit[0]); row["revision"] = 1; rows.append(row)
        if i % 200 == 0:
            print(f"  [{i}/{len(codes)}] 봉 {len(rows)} · 없음 {missing} · 실패 {fails}", flush=True)
    written = store.append(TABLE, rows, ingest_run_id=run_id, source=mc.SOURCE) if rows else 0
    print(f"완료 — {day} 적재 {written}행 (revision 1) · 봉 없음 {missing} · 실패 {fails} / {len(codes)}")
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
