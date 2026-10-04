"""미장 시총 상위·대표 ETF 의 전날 종가를 06:00 에 먼저 받는다 — LS `g3204`. Yahoo 대체 (2026-10-04).

    .venv/bin/python tools/collect_prices_us_top_ls.py [--top 60] [--dry-run]

06:30 브리핑 시점에 미장 전체 시세(6,600종목)는 08:40 수집 전이라 시총 상위 표의 전일대비가 "—" 였다
(2026-08-29). 그 자리를 Yahoo 가 메웠는데 Yahoo 는 robots.txt `Disallow: /` 라 걷어냈다
(`docs/design/data-contract.md` §4-2). 미장 시세의 정본 경로(`collect_us_prices`, g3204)를 명단만 좁혀
부른다 — 같은 원천·같은 관측시각 규약이라 08:40 전체 수집과 값이 갈리지 않는다.

명단 지문이 run_id 에 붙는 부분 수집(`collect_us_prices --symbols`)이라 전체 수집을 "적재됨" 으로
잠그지 않는다. 명단이 같으면 같은 세션을 두 번 받지 않는다.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.reporting.briefing import market_caps  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from tools import collect_us_prices  # noqa: E402
from tools.backfill import build_store  # noqa: E402

#: 브리핑 표가 쓰는 대표 ETF. 옛 Yahoo 수집기와 같은 다섯이다.
ETFS = ("SPY", "QQQ", "DIA", "SOXX", "IWM")


def top_symbols(store: Store, *, as_of: datetime, top: int) -> list[str]:
    caps, _ = market_caps(store, as_of=as_of, market="US")
    names = [str(e).split(":", 1)[1] for e in caps.sort_values(ascending=False).head(top).index]
    return list(dict.fromkeys([*names, *ETFS]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--top", type=int, default=60)
    parser.add_argument("--sessions", type=int, default=2, help="최근 N 거래일 (기본 2)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    load_env()
    store = build_store(args.data_root)
    symbols = top_symbols(store, as_of=LiveClock().now(), top=args.top)
    print(f"시총 상위 {args.top} + ETF {len(ETFS)} → {len(symbols)}종목")
    forwarded = ["--symbols", ",".join(symbols), "--sessions", str(args.sessions)]
    if args.data_root:
        forwarded += ["--data-root", str(args.data_root)]
    if args.dry_run:
        forwarded.append("--dry-run")
    return collect_us_prices.main(forwarded)


if __name__ == "__main__":
    raise SystemExit(main())
