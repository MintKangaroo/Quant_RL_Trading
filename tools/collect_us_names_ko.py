"""미장 한국어 회사명 수집 — 네이버 증권 → `names_ko` (주 1회, 또는 --held 로 보유·후보만 즉시).

    .venv/bin/python tools/collect_us_names_ko.py [--limit 5] [--held] [--missing-only]
"""
from __future__ import annotations

import argparse
import sys
import time as time_module
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.collectors import naver_us_names as nn  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402

INTERVAL_SEC = 0.35
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) Quant_RL_Trading/names-ko"


def _held_entities(root: str, now: datetime) -> set[str]:
    out: set[str] = set()
    for sandbox in ("data/_shadow", "data/_paper"):
        try:
            st = Store(root=Path(sandbox))
            o = st.get("orders", as_of=now, lookback=10, market="US", columns=["entity_id"])
            out |= set(o["entity_id"].astype(str)) if not o.empty else set()
        except Exception:  # noqa: BLE001 — 샌드박스가 없으면 건너뛴다
            continue
    return out


def _flush(store: Store, rows: list, run_id: str, upto: int) -> int:
    if not rows:
        return 0
    return int(store.append(nn.NAMES_KO, rows, ingest_run_id=f"{run_id}-p{upto}", source=nn.SOURCE))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="data")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--held", action="store_true", help="shadow·paper 장부의 미장 주문 종목만")
    parser.add_argument("--missing-only", action="store_true", help="이미 이름이 있는 종목은 건너뛴다")
    args = parser.parse_args(argv)
    clock = LiveClock(); now = clock.now(); store = Store(root=Path(args.root))
    day = now.astimezone(ZoneInfo("Asia/Seoul")).date()
    tag = "held" if args.held else ""
    run_id = nn.run_id_for(day, limit=args.limit, tag=tag)
    if store.ingest_run_recorded(nn.NAMES_KO, run_id):
        print(f"{day} 이름은 이미 받았다({run_id}) — 할 일 없음"); return 0
    if args.held:
        entities = _held_entities(args.root, now)
    else:
        uni = store.get("universe", as_of=now, lookback=7, market="US", columns=["entity_id", "valid_from", "is_listed", "is_tradable"])
        latest = uni.sort_values("valid_from").groupby("entity_id").tail(1)
        entities = set(latest[latest["is_listed"].astype(bool) & latest["is_tradable"].astype(bool)]["entity_id"].astype(str))
    if args.missing_only:
        have = store.get(nn.NAMES_KO, as_of=now, lookback=400, market="US", columns=["entity_id"])
        entities -= set(have["entity_id"].astype(str)) if not have.empty else set()
    tickers = sorted(e.split(":", 1)[1] for e in entities)
    if args.limit:
        tickers = tickers[: args.limit]
    print(f"{day} · 종목 {len(tickers)} · 간격 {INTERVAL_SEC}s", flush=True)
    rows = []; fails = 0; empty = 0; written = 0
    with httpx.Client(timeout=15, headers={"User-Agent": USER_AGENT}) as client:
        for i, ticker in enumerate(tickers, 1):
            parsed = None; last_error = None
            for suffix in nn.SUFFIXES:
                try:
                    r = client.get(nn.URL.format(ticker=ticker, suffix=suffix))
                    if r.status_code == 409:
                        time_module.sleep(2.0); r = client.get(nn.URL.format(ticker=ticker, suffix=suffix))
                    if r.status_code != 200:
                        continue
                    parsed = nn.parse_basic(r.text)
                    if parsed:
                        break
                except Exception as error:  # noqa: BLE001
                    last_error = error
                finally:
                    time_module.sleep(INTERVAL_SEC)
            if parsed:
                rows.append(nn.row_for(ticker, day=day, observed_at=clock.now(), parsed=parsed))
            elif last_error is not None:
                fails += 1
                if fails <= 5:
                    print(f"  {ticker}: 실패 {type(last_error).__name__}", file=sys.stderr)
            else:
                empty += 1
            if i % 200 == 0:
                # **200종목마다 적재한다.** 6,575종목 40분짜리를 끝에 한 번 쓰면 재부팅(2026-09-05 12:05, WSL)에
                # 2,600종목이 통째로 날아간다. 조각 run id 로 나눠 쓰고, 다음 실행은 --missing-only 로 이어받는다.
                written += _flush(store, rows, run_id, i)
                rows = []
                print(f"  [{i}/{len(tickers)}] 누적 적재 {written} · 없음 {empty} · 실패 {fails}", flush=True)
    written += _flush(store, rows, run_id, len(tickers))
    print(f"완료 — 적재 {written}행 · 없음 {empty} · 실패 {fails} / {len(tickers)}", flush=True)
    return 0 if fails < max(10, len(tickers) // 10) else 1


if __name__ == "__main__":
    raise SystemExit(main())
