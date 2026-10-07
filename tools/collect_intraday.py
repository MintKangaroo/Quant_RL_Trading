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

## 국장 1m·5m 는 거래대금 상위 300 까지 넓힌다 (2026-10-05)

``--phase live|close`` 로 부르면(크론의 ``scripts/collect_intraday.sh``) 국장의
``collectors.intraday.wide_intervals`` 구간은 위 목록 **뒤에** 그날의 거래대금 상위
``kr_top_n`` 을 붙인다(``intraday_collector.daily_targets`` — 그날 첫 회차가 골라
``intraday_targets`` 에 적는다). 호출 간격·시간 예산·장중 호출당 봉 수·실패 문턱은
전부 ``collectors.intraday.*`` 설정이다. 근거와 추정은 ``docs/design/ls-api.md`` §0-14.

**rc**: 일부 종목 실패는 적재하고 넘어가되, 실패 비율이 ``max_fail_ratio`` 를 넘거나
시간 예산·연속 실패로 못 받은 종목이 있으면 rc=1 — 조용히 덜 받은 회차가 rc=0 으로
남지 않는다.

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

from quant_rl_trading.collectors.errors import CollectorError  # noqa: E402
from quant_rl_trading.collectors.intraday_collector import (  # noqa: E402
    INTERVAL_NCNT,
    MAX_ROWS_PER_CALL,
    TABLE,
    IntradayCollector,
    daily_targets,
    ingest_run_id,
)
from quant_rl_trading.collectors.ls_client import (  # noqa: E402
    MIN_INTERVAL_SEC_KR,
    MIN_INTERVAL_SEC_US,
    LSClient,
    LSCredentials,
)
from quant_rl_trading.collectors.ls_us_source import LsUsSource  # noqa: E402
from quant_rl_trading.collectors.market_hours import SPECS, Market, local_time  # noqa: E402
from quant_rl_trading.collectors.raw import RawArchive  # noqa: E402
from quant_rl_trading.dashboard.services.trading import build_context, watchlist  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from quant_rl_trading.store.errors import ConfigNotFound  # noqa: E402

#: 분봉 대상을 고를 때 **같이 보는 창고**. 실전 창고에만 물으면 지금은
#: 대상이 거의 안 나온다 — `execution.live_trading` 이 꺼져 있어 실계좌 보유가
#: 비어 있고, 실제로 굴러가는 포트폴리오는 shadow 쪽이기 때문이다.
#:
#: 실측 2026-08-19: 실전 기준 13종목만 받혀서, shadow 가 들고 있는 22종목 중
#: 대부분이 차트에서 분봉 버튼이 꺼진 채로 있었다. 화면은 "안 받는 종목" 과
#: "수집 실패" 를 구분해 주지 않으므로 고장으로 읽힌다.
#:
#: **없으면 조용히 건너뛴다.** shadow 창고가 없는 배포도 있다.
COMPANION_ROOTS = ("data/_shadow",)


def _companion_positions(market: str) -> list[str]:
    """딸린 창고들의 보유 종목. 못 열면 빈 목록 — 여기서 죽지 않는다."""
    from quant_rl_trading.accounting.book import Book  # 지역 import: 선택 경로다

    out: list[str] = []
    prefix = f"{market}:"
    for rel in COMPANION_ROOTS:
        root = REPO_ROOT / rel
        if not root.exists():
            continue
        try:
            side = Store(root)
            clock = LiveClock()
            ctx = build_context(side, clock, as_of=clock.now(), market=market)
            out.extend(
                entity
                for entity, position in ctx.book.positions.items()
                if position.quantity > 0 and entity.startswith(prefix)
            )
        except Exception as error:  # 창고가 비었거나 스키마가 다를 수 있다
            print(f"  ({rel} 보유를 못 읽었다: {type(error).__name__}) — 건너뛴다", flush=True)
    return out


def _auto_symbols(store: Store, clock: LiveClock, *, market: str) -> list[str]:
    """보유 + 오늘의 워치리스트 + **shadow 보유**. entity_id 그대로 돌려준다.

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
    # 보유가 먼저, 그다음 shadow 보유, 마지막이 워치리스트. dict.fromkeys 로
    # 순서를 지키며 중복 제거한다 — 실제로 들고 있는 것이 먼저 받혀야
    # 레이트리밋에 걸려 잘리더라도 중요한 것이 남는다.
    return list(dict.fromkeys([*held, *_companion_positions(market), *watched]))


#: ``collectors.intraday.*`` — 국장 넓히기의 설정. 하나라도 없으면 넓히지 않는다.
INTRADAY_KEYS = (
    "kr_top_n",
    "adv_sessions",
    "wide_intervals",
    "kr_min_interval_sec",
    "run_budget_sec",
    "max_fail_ratio",
    "max_consecutive_failures",
    "qrycnt_live_1m",
    "qrycnt_live_5m",
    "full_fetch_minutes_after_open",
    "kr_wide_when_orders_share_key",
)


def _intraday_params(store: Store, *, as_of) -> dict | None:  # type: ignore[no-untyped-def]
    """설정 묶음. 키가 하나라도 없으면 None — 예전 동작(보유·후보만, 3.1초, 500봉)으로 돈다."""
    try:
        return {key: store.config(f"collectors.intraday.{key}", as_of=as_of) for key in INTRADAY_KEYS}
    except ConfigNotFound as error:
        print(f"  collectors.intraday 설정이 없다({error}) — 넓히지 않는다. seed_config --apply 필요", flush=True)
        return None


def _orders_share_key(store: Store, *, as_of) -> bool:  # type: ignore[no-untyped-def]
    """국장 주문이 이 수집기와 같은 appkey(``LS_``)를 쓰는 모드인가 — 실전 모드면 그렇다.

    모드 → 키는 ``broker_factory.PROFILES`` 하나가 쥔다(여기서 다시 적지 않는다).
    모르면 같다고 본다 — 주문 우선이다.
    """
    from quant_rl_trading.broker import factory as broker_factory  # 지역 import: 선택 경로다

    try:
        mode = str(store.config(broker_factory.ACCOUNT_MODE_KEY, as_of=as_of) or broker_factory.MODE_PAPER)
    except ConfigNotFound:
        mode = broker_factory.MODE_PAPER
    profile = broker_factory.PROFILES.get(("KR", mode.strip().lower()))
    return profile is None or profile.env_prefix == "LS_"


def _live_qrycnt(params: dict, *, interval: str, now) -> int:  # type: ignore[no-untyped-def,type-arg]
    """장중 회차의 호출당 봉 수. 개장 뒤 첫 회차는 500 — 전날 마감 뒤(NXT 애프터)부터 덮는다."""
    here = local_time(Market.KR, now)
    opened = here.replace(
        hour=SPECS[Market.KR].regular_open.hour, minute=SPECS[Market.KR].regular_open.minute,
        second=0, microsecond=0,
    )
    if (here - opened).total_seconds() < float(params["full_fetch_minutes_after_open"]) * 60:
        return MAX_ROWS_PER_CALL
    key = f"qrycnt_live_{interval}"
    return int(params[key]) if key in params else MAX_ROWS_PER_CALL


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
        "--phase",
        choices=["live", "close"],
        help="크론 회차(scripts/collect_intraday.sh). 주면 국장 1m·5m 를 거래대금 상위까지 넓힌다",
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

    # 국장 넓히기 — 크론 회차(--phase)이고 설정이 있을 때만. 손으로 부른 실행은 예전 그대로다.
    params = None
    if args.market == "KR" and args.phase and not args.symbols:
        params = _intraday_params(store, as_of=clock.now())
    base_count = len(symbols)
    #: 넓히기가 실패했다 — 보유·후보는 받되 회차는 rc=1 로 남긴다.
    degraded = False
    if params is not None and args.interval in params["wide_intervals"]:
        if _orders_share_key(store, as_of=clock.now()) and not params["kr_wide_when_orders_share_key"]:
            print("  국장 주문이 분봉과 같은 appkey(LS_) — 넓히지 않는다(주문 우선, ls-api.md §0-14)", flush=True)
        else:
            try:
                symbols, computed = daily_targets(
                    store, clock, market=Market.KR, base=symbols,
                    top_n=int(params["kr_top_n"]), sessions=int(params["adv_sessions"]),
                    record=not args.dry_run,
                )
            except CollectorError as error:
                degraded = True
                print(f"  넓히기 실패 — 보유·후보만 받는다: {error}", flush=True)
            else:
                how = "오늘 처음 계산해 intraday_targets 에 적음" if computed else "오늘 기록을 읽음"
                if args.dry_run:
                    how = "계산만(--dry-run, 안 적음)"
                print(
                    f"  거래대금 상위 {params['kr_top_n']} 를 붙였다 — 보유·후보 {base_count} + 추가 "
                    f"{len(symbols) - base_count} ({how})",
                    flush=True,
                )

    if not symbols:
        print(f"{args.market}: 받을 종목이 없다(보유도 워치리스트도 비었다) — 끝.", flush=True)
        return 0

    qrycnt = MAX_ROWS_PER_CALL
    if params is not None and args.phase == "live":
        qrycnt = _live_qrycnt(params, interval=args.interval, now=clock.now())

    run_id = ingest_run_id(args.market, args.interval, observed_at=clock.now())
    print(f"{args.market} {args.interval} · {len(symbols)}종목 · qrycnt {qrycnt} · run_id={run_id}", flush=True)
    shown = symbols[:30]
    print("  " + ", ".join(shown) + (f" … 외 {len(symbols) - len(shown)}" if len(symbols) > len(shown) else ""), flush=True)

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
            # t8412 카탈로그 한도는 초당 1(TR 별). 설정이 없으면 예전 3.1초(LS_KR "3초당 1건 권장").
            min_interval_sec=(
                float(params["kr_min_interval_sec"]) if params is not None else MIN_INTERVAL_SEC_KR
            ),
        )
        started = clock.now()
        written = collector.collect_kr(
            symbols,
            interval=args.interval,
            ingest_run_id=run_id,
            qrycnt=qrycnt,
            budget_sec=float(params["run_budget_sec"]) if params is not None else None,
            max_consecutive_failures=(
                int(params["max_consecutive_failures"]) if params is not None else None
            ),
        )
        elapsed = (clock.now() - started).total_seconds()
        failed, unfetched = collector.last_failed, collector.last_unfetched
        print(
            f"끝 — {written}행 적재 · 대상 {len(symbols)} · 실패 {len(failed)} · 못 받음 {len(unfetched)}"
            f"{' (연속 실패로 멈춤)' if collector.last_aborted else ''} · 걸린 시간 {elapsed:.0f}초",
            flush=True,
        )
        if failed:
            print("  실패: " + ", ".join(failed[:20]) + (" …" if len(failed) > 20 else ""), flush=True)
        max_fail = float(params["max_fail_ratio"]) if params is not None else 0.0
        if degraded or unfetched or len(failed) > max_fail * len(symbols):
            # 덜 받은 회차를 rc=0 으로 남기지 않는다. 받은 것은 이미 적재했다.
            return 1
        return 0
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
