"""지수 구성종목(KOSPI200) 스냅샷 → `index_members`. Z2 트랙의 후보 필터 입력(docs/design/portfolio-construction.md).

    .venv/bin/python tools/collect_index_members.py              # 마지막 완성 세션 하나
    .venv/bin/python tools/collect_index_members.py --days 10    # 최근 거래일 10개(처음 한 번)

pykrx(KRX 정보데이터시스템)가 **로그인 세션**을 요구한다 — `.env` 의 KRX_ID/KRX_PW(backfill 과 같다). 장중엔 부르지 않는다(완성 세션만).
관측 시각은 실제 수집 시각이다. 같은 세션을 두 번 받아도 실행 id 가 세션 날짜라 한 번만 적힌다.
"""

from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.collectors.market_hours import (  # noqa: E402
    Market,
    is_trading_day,
    previous_trading_day,
)
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from tools.backfill import load_env  # noqa: E402
from tools.collect_indices_ls import completed_session, session_timestamp  # noqa: E402

TABLE = "index_members"
INDICES = {"KOSPI200": "1028"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default="data")
    parser.add_argument("--days", type=int, default=1)
    parser.add_argument("--step", type=int, default=1, help="거래일 N 개마다 하나(백필은 5 = 주 1회 — 구성은 정기변경 때만 바뀐다)")
    parser.add_argument("--sleep", type=float, default=0.0, help="호출 사이 초 — KRX 가 연속 호출을 막는다(2026-09-25 264회 뒤 빈 응답)")
    parser.add_argument("--backfill", action="store_true",
                        help="과거 채우기 — observed_at = 그 세션 시각(구성종목은 그날 공개된 사실이다; EDGAR·DART 백필과 같은 관행)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    load_env()
    from pykrx import stock

    clock = LiveClock()
    last = completed_session(clock.now())
    if last is None:
        # 장중 — 오늘 세션은 미완성이다. 어제까지의 세션은 끝났으므로 그 전 거래일부터 받는다.
        last = previous_trading_day(Market.KR, clock.now().astimezone(ZoneInfo("Asia/Seoul")).date())
    days, probe = [], last
    while len(days) < args.days:
        if is_trading_day(Market.KR, probe):
            days.append(probe)
        probe -= timedelta(days=1)
    store = Store(root=Path(args.root))
    failed = 0
    import time as _time

    days = sorted(days)[::-1][:: max(1, args.step)][::-1]  # 가장 최근 세션을 기준으로 N 개마다
    for day in days:
        if args.backfill and store.ingest_run_recorded(TABLE, f"index-members-KOSPI200-{day:%Y%m%d}-bf"):
            continue
        if args.sleep:
            _time.sleep(args.sleep)
        for name, code in INDICES.items():
            codes = stock.get_index_portfolio_deposit_file(code, day.strftime("%Y%m%d"))
            if len(codes) < 150:  # K200 은 200종목 — 적게 오면 소스 사고다. 적지 않고 rc 로 알린다.
                print(f"{day} {name}: {len(codes)}종목 — 비정상, 적지 않는다", flush=True)
                failed += 1
                continue
            observed = session_timestamp(day) if args.backfill else clock.now()
            rows = [{"entity_id": f"KR:{c}", "valid_from": session_timestamp(day), "observed_at": observed,
                     "source": "pykrx", "market": "KR", "index_id": name} for c in codes]
            if not args.dry_run:
                store.append(TABLE, rows, ingest_run_id=f"index-members-{name}-{day:%Y%m%d}{'-bf' if args.backfill else ''}", source="pykrx")
            print(f"{day} {name}: {len(rows)}종목" + (" (dry-run)" if args.dry_run else ""), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
