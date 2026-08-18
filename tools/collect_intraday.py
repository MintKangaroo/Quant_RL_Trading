#!/usr/bin/env python
"""분봉 수집 실행기.

    uv run python tools/collect_intraday.py --market KR --interval 5m
    uv run python tools/collect_intraday.py --market US --interval 1H --symbols AAPL,TSLA
    uv run python tools/collect_intraday.py --market KR --interval 1m --dry-run

**하루 한 번 돌리는 것을 전제로 한다.** ``ingest_run_id`` 에 오늘 날짜가
박혀 있어서(``intraday_collector.ingest_run_id``), 오늘 같은
``(market, interval)`` 조합을 두 번째로 돌리면 조용히 건너뛴다(멱등성 —
``prices_intraday`` 는 append-only 라 두 번째 실행이 중복으로 거절된다).
장중에 여러 번 갱신하고 싶어지면 ``intraday_collector.py`` 의 run_id 형식에
시·분을 더 넣어야 한다 — 지금 범위 밖이다.

## 심볼을 왜 자동으로 고르나

전 종목이 아니라 **보유 + 오늘의 워치리스트**만 받는다 — 수집 범위를 좁힌
이유는 ``intraday_collector.py`` 모듈독스트링을 보라. 그 목록은
대시보드(``dashboard/services/trading.py``)가 매일 계산하는 것과 **같은
목록**이어야 한다 — 화면이 보여주는 종목과 수집기가 채우는 종목이 어긋나면
화면에서 고른 종목의 분봉만 없는 상황이 생긴다. 그래서 여기서 목록을 새로
정의하지 않고 ``build_context``/``watchlist`` 를 그대로 불러 쓴다.
``--symbols`` 로 직접 지정하면 그 목록을 대신 쓴다(디버깅·특정 종목 확인용).

## 시장은 한 번에 하나

국장·미장은 appkey 도 레이트리밋도 다르다(``ls-api.md`` §0-7). 한 프로세스가
두 시장을 순서대로 돌면 어느 쪽 토큰이 무효화됐을 때 원인을 가르기 어렵다 —
크론에서 ``--market KR`` · ``--market US`` 두 줄로 따로 돌려라.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.collectors.intraday_collector import (  # noqa: E402
    INTERVAL_NCNT,
    TABLE,
    IntradayCollector,
    ingest_run_id,
)
from quant_rl_trading.collectors.ls_client import (  # noqa: E402
    MIN_INTERVAL_SEC_KR,
    MIN_INTERVAL_SEC_US,
    LSClient,
    LSCredentials,
)
from quant_rl_trading.collectors.ls_us_source import LsUsSource  # noqa: E402
from quant_rl_trading.collectors.raw import RawArchive  # noqa: E402
from quant_rl_trading.dashboard.services.trading import build_context, watchlist  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402


def _auto_symbols(store: Store, clock: LiveClock, *, market: str) -> list[str]:
    """보유 + 오늘의 워치리스트. entity_id 그대로("KR:005930") 돌려준다.

    ``IntradayCollector.collect_kr``/``collect_us`` 가 접두어를 알아서
    떼므로 여기서 떼지 않는다 — 두 군데서 같은 파싱을 하면 한쪽만
    바뀌었을 때 조용히 어긋난다.
    """
    as_of = clock.now()
    context = build_context(store, clock, as_of=as_of, market=market)
    prefix = f"{market}:"
    # **book.positions 는 시장으로 안 걸러진다** — 계좌 전체 보유다. 실측:
    # market="KR" 로 불러도 US:SNAP 이 섞여 나왔다. 거기서 안 걸러 collect_kr
    # 에 넘기면 "SNAP" 을 국장 6자리 종목코드로 오인해 엉뚱한 TR 을 친다.
    held = [
        entity
        for entity, position in context.book.positions.items()
        if position.quantity > 0 and entity.startswith(prefix)
    ]
    watched = [
        row["entity_id"] for row in watchlist(store, context) if row["entity_id"].startswith(prefix)
    ]
    # 보유가 먼저, 그다음 워치리스트. dict.fromkeys 로 순서를 지키며 중복 제거.
    return list(dict.fromkeys([*held, *watched]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--market", choices=["KR", "US"], required=True)
    parser.add_argument("--interval", choices=sorted(INTERVAL_NCNT), required=True)
    parser.add_argument(
        "--symbols",
        help="쉼표로 구분한 종목(접두어 있어도 없어도 된다). 없으면 보유+워치리스트.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="받을 종목·run_id 만 찍고 실제 호출은 안 한다"
    )
    args = parser.parse_args(argv)

    load_env()
    clock = LiveClock()
    store = Store()

    if args.symbols:
        symbols = [item.strip() for item in args.symbols.split(",") if item.strip()]
    else:
        symbols = _auto_symbols(store, clock, market=args.market)

    if not symbols:
        print(f"{args.market}: 받을 종목이 없다(보유도 워치리스트도 비었다) — 끝.", flush=True)
        return 0

    run_id = ingest_run_id(args.market, args.interval, observed_at=clock.now())
    print(f"{args.market} {args.interval} · {len(symbols)}종목 · run_id={run_id}", flush=True)
    print("  " + ", ".join(symbols), flush=True)

    if args.dry_run:
        return 0

    if store.ingest_run_recorded(TABLE, run_id):
        # 팀 규칙: 재수집은 그날 두 번째 실행을 조용히 건너뛴다(의도된
        # 멱등성) — 그러나 "왜 아무 일도 안 났나" 가 로그에 남아야 한다.
        print("이미 오늘 실행됐다 (append-only 멱등성) — 끝.", flush=True)
        return 0

    archive = RawArchive(root=store.root)
    collector = IntradayCollector(store=store, clock=clock, archive=archive)

    if args.market == "KR":
        collector.kr_client = LSClient(
            credentials=LSCredentials.from_env(prefix="LS_"),
            clock=clock,
            live_trading=True,
            min_interval_sec=MIN_INTERVAL_SEC_KR,
        )
        written = collector.collect_kr(symbols, interval=args.interval, ingest_run_id=run_id)
    else:
        us_client = LSClient(
            credentials=LSCredentials.from_env(prefix="LS_US_"),
            clock=clock,
            live_trading=True,
            min_interval_sec=MIN_INTERVAL_SEC_US,
        )
        collector.us_source = LsUsSource(client=us_client)
        written = collector.collect_us(symbols, interval=args.interval, ingest_run_id=run_id)
        if collector.last_skipped:
            print(f"  거래소를 못 찾아 건너뛴 종목 {collector.last_skipped}개", flush=True)

    print(f"끝 — {written}행 적재", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
