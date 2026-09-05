"""원달러 환율 일봉 — FRED(DEXKOUS)가 일주일씩 늦는 자리 (Yahoo `KRW=X`).

    .venv/bin/python tools/collect_fx_yahoo.py [--dry-run]

FRED 는 주간 묶음으로 내서 창고 환율이 4세션 뒤처졌다(2026-08-28 실측: 최신 08-21).
미장 보유 평가와 브리핑 환율 줄이 그만큼 낡는다. Yahoo 는 매일 준다. entity·valid_from
규약은 FRED 행과 같다(`FX:USDKRW`, 그날 09:00 KST) — FRED 가 나중에 오면 정정본이 된다.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from tools.backfill import build_store  # noqa: E402

ENTITY = "FX:USDKRW"
SOURCE = "yahoo"
URL = "https://query1.finance.yahoo.com/v8/finance/chart/KRW%3DX?range=15d&interval=1d"
SEOUL = ZoneInfo("Asia/Seoul")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    load_env()
    now = LiveClock().now()
    store = build_store(args.data_root)
    have = store.get("fx", as_of=now, lookback=20)
    have_days = set(have["valid_from"].dt.date) if not have.empty else set()
    data = httpx.get(URL, headers={"User-Agent": "Mozilla/5.0 (quant_rl_trading fx)"}, timeout=20).json()
    result = ((data.get("chart") or {}).get("result") or [None])[0] or {}
    stamps = result.get("timestamp") or []
    closes = ((result.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    rows = []
    for ts, close in zip(stamps, closes, strict=False):
        if close is None:
            continue
        # **서울 시각으로 날짜를 만든다.** UTC 로 만들면 하루씩 밀린다 — 아시아
        # 세션 봉은 UTC 로 전날 21:00 에 찍히기 때문이다 (2026-09-01 실측:
        # ts=1788130800 이 UTC 로는 8/30 일요일인데 서울로는 8/31 월요일이다).
        # 그래서 창고에 **일요일 환율**이 들어 있었고, 모든 환율이 하루씩 밀려
        # 저장돼 화면이 늘 "1세션 지연" 을 띄웠다. valid_from 을 SEOUL 로 짓는
        # 아래 줄과 기준이 어긋나 있던 것이 근인이다.
        day = datetime.fromtimestamp(ts, SEOUL).date()
        if day >= now.date() or day in have_days:
            continue  # 오늘 것은 아직 안 끝났을 수 있다 — 어제까지만
        rows.append({
            "entity_id": ENTITY, "valid_from": datetime(day.year, day.month, day.day, 9, 0, tzinfo=SEOUL),
            "observed_at": now, "source": SOURCE, "rate": float(close),
        })
    for r in rows:
        print(f"  {ENTITY} {r['valid_from'].date()} {r['rate']:,.2f}")
    if not rows:
        print("새 환율 없음"); return 0
    if args.dry_run:
        print(f"드라이런 — {len(rows)}행"); return 0
    written = store.append("fx", rows, ingest_run_id=f"fx-yahoo-{now:%Y%m%dT%H%M%S}", source=SOURCE)
    print(f"fx 적재: {written}행 ({SOURCE})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
