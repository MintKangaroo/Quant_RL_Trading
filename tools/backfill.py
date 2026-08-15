"""과거 5년치 백필 실행기.

    uv run python tools/backfill.py --years 5 --symbols 10     # 시험 실행
    uv run python tools/backfill.py --years 5                  # 전체
    uv run python tools/backfill.py --report                   # 검증 리포트

중단해도 된다. 다시 같은 명령을 치면 이미 들어간 세션은 건너뛰고 이어받는다
(``store.ingest_run_recorded`` 가 판단한다). Ctrl-C 로 죽여도 마지막으로 완료된
세션까지는 무결하게 남는다 — 창고가 append-only 이고 세션 단위로 원자적이기
때문이다.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.collectors import us_shares as us_sh  # noqa: E402
from quant_rl_trading.collectors import us_universe_panel as up  # noqa: E402
from quant_rl_trading.collectors.backfill import (  # noqa: E402
    PRICES,
    UNIVERSE,
    Backfiller,
    BackfillReport,
    ProgressLog,
    eta,
)
from quant_rl_trading.collectors.dart_filings import (  # noqa: E402
    FilingsBackfiller,
    FilingsReport,
)
from quant_rl_trading.collectors.dart_source import (  # noqa: E402
    DartBackfiller,
    DartSource,
    FilingPolicy,
    batched,
)
from quant_rl_trading.collectors.krx_openapi import KrxOpenApi  # noqa: E402
from quant_rl_trading.collectors.krx_source import KrxSource, credentials_present  # noqa: E402
from quant_rl_trading.collectors.ls_client import LSClient, LSCredentials  # noqa: E402
from quant_rl_trading.collectors.ls_flow import LSFlowBackfiller, LSFlowSource  # noqa: E402
from quant_rl_trading.collectors.ls_us_source import (  # noqa: E402
    LsUsSource,
    UsPriceBackfiller,
)
from quant_rl_trading.collectors.market_hours import Market, trading_days  # noqa: E402
from quant_rl_trading.collectors.panels import (  # noqa: E402
    OPENAPI_PANELS,
    PANELS,
    PanelBackfiller,
)
from quant_rl_trading.collectors.panels import SHORTING as SHORTING  # noqa: E402
from quant_rl_trading.collectors.publication import publication_policy  # noqa: E402
from quant_rl_trading.collectors.raw import RawArchive  # noqa: E402
from quant_rl_trading.collectors.us_universe import UA_ENV, fetch_listings  # noqa: E402
from quant_rl_trading.dashboard.services import data_quality as dq  # noqa: E402
from quant_rl_trading.replay.clock import Clock, LiveClock  # noqa: E402
from quant_rl_trading.settings import (
    load_env,
)
from quant_rl_trading.store import ConfigNotFound, Store  # noqa: E402
from quant_rl_trading.store.prices import read_prices  # noqa: E402

#: 수급은 종목 축이라 패널과 실행 경로가 다르다.
FLOW_LS = "flows-ls"
#: DART 재무는 분기 × 배치 축이다.
DART = "fundamentals-dart"
#: DART 공시목록은 날짜 축이다. 재무와 소스는 같고 축이 다르다.
DART_FILINGS = "documents-dart"
#: 미장 상장주식수. SEC 벌크 companyfacts 한 벌을 훑는다 (분기 축).
#: 국장 ``shares``(OPENAPI_PANELS, KRX 일별매매)와 이름을 겹치지 않게 둔다 —
#: 같은 테이블을 채우지만 소스도 축도 다르다.
US_SHARES = "shares-sec"
#: 미장 시가총액. 주식수와 시세를 곱해 만든다 (세션 축). 수집이 아니라 유도다.
US_MARKET_CAP = "market-cap"

ENV_FILE = REPO_ROOT / ".env"


def build_store(root: Path | None = None) -> Store:
    store = Store(root=root) if root is not None else Store()
    try:
        store.config("backfill.years", as_of=LiveClock().now())
    except ConfigNotFound:
        store.seed_config_defaults()
    return store


# -----------------------------------------------------------------------------
# 백필
# -----------------------------------------------------------------------------


def run_backfill(
    store: Store,
    clock: Clock,
    *,
    market: Market,
    years: int,
    symbols: int | None,
    sessions_limit: int | None,
    dry_run: bool,
    table: str | None = None,
) -> int:
    if not credentials_present():
        print(
            "KRX_ID / KRX_PW 가 없다. data.krx.co.kr 계정 없이는 과거 시세를 받을 수 "
            "없고, pykrx 는 예외 없이 빈 결과를 돌려준다.",
            file=sys.stderr,
        )
        return 2

    now = clock.now()
    end = now.astimezone(UTC).date()
    start = end - timedelta(days=365 * years)

    source = KrxSource()
    policy = publication_policy(store, market, clock=clock)
    archive = RawArchive(root=store.root)
    backfiller: Backfiller | PanelBackfiller

    if table in OPENAPI_PANELS:
        # 정식 Open API 경로. 인증키만 있으면 되고 pykrx 자격증명은 필요 없다.
        panel = OPENAPI_PANELS[table]
        backfiller = PanelBackfiller(
            store=store, source=KrxOpenApi(), clock=clock, archive=archive,
            policy=policy, panel=panel, market=market,
        )
    elif table is not None:
        panel = PANELS[table]
        if panel.table == SHORTING:
            # 실제 지연은 설정에서 온다. 코드의 기본값을 믿고 넘어가면
            # 언젠가 설정만 바꾸고 관측시각은 그대로인 상태가 된다 (불변식 10).
            panel = replace(
                panel, lag_days=int(store.config("backfill.shorting_lag_days", as_of=now))
            )
        backfiller = PanelBackfiller(
            store=store, source=source, clock=clock, archive=archive,
            policy=policy, panel=panel, market=market,
        )
    else:
        backfiller = Backfiller(
            store=store, source=source, clock=clock, archive=archive,
            policy=policy, market=market,
        )

    sessions = backfiller.plan(start, end)
    if sessions_limit is not None:
        sessions = sessions[-sessions_limit:]

    if symbols is not None:
        backfiller.only_codes = probe_codes(source, sessions, symbols)
        print(f"시험 실행 — 종목 {len(backfiller.only_codes)}개: "
              f"{', '.join(sorted(backfiller.only_codes))}")

    pending = backfiller.pending(sessions)
    print(
        f"{market} {start} ~ {end}  거래일 {len(sessions)}개 "
        f"(이미 적재 {len(sessions) - len(pending)}개, 남은 {len(pending)}개)"
    )
    if dry_run:
        return 0
    if not pending:
        print("할 일이 없다.")
        return 0

    progress = ProgressLog(root=store.root, plan_id=f"{market}-{start}-{end}")
    report = BackfillReport(market=market)
    started = time.monotonic()  # invariant-allow: wallclock
    pause = float(store.config("backfill.session_pause_ms", as_of=now)) / 1000.0

    for index, day in enumerate(pending, start=1):
        if index > 1:
            time.sleep(pause)  # 소스를 두들겨 차단당하지 않기 위한 예의
        result = backfiller.run_session(day)
        report.absorb(result)
        progress.record(result, at=clock.now())

        elapsed = timedelta(seconds=time.monotonic() - started)  # invariant-allow: wallclock
        remaining = eta(index, len(pending), elapsed)
        if result.skipped:
            status = "SKIP"
        elif getattr(result, "deferred", False):
            status = "WAIT"  # 아직 공표되지 않았다. 실패가 아니다
        else:
            status = "FAIL" if result.error else "ok"
        tail = f"  남은시간 ~{_short(remaining)}" if remaining else ""
        counts = " ".join(f"{name}={rows}" for name, rows in sorted(result.counts.items()))
        print(
            f"[{index}/{len(pending)}] {day} {status}  {counts}{tail}"
            + (f"  {result.error}" if result.error else "")
        )

    print(
        f"\n완료 — 세션 {report.sessions}개 "
        f"(건너뜀 {report.skipped}, 미공표 대기 {report.deferred}), "
        f"{report.render_counts()}, 실패 {len(report.failures)}건"
    )
    for unit, message in report.failures[:20]:
        print(f"  실패 {unit}: {message}")
    return 1 if report.failures else 0


def flow_symbols(store: Store, clock: Clock, market: Market) -> list[str]:
    """수급을 받을 종목 순서. **유동성 높은 것부터.**

    12시간짜리 실행이라 중간에 멈출 수 있다. 그때 이미 들어온 것이 쓸모
    있으려면 매매 후보가 될 종목이 먼저 와야 한다. 알파벳순으로 돌면
    12시간 뒤에도 000020 부터 000660 까지밖에 없다.

    상장폐지 종목은 뒤로 보내되 **빼지는 않는다** — 생존편향 때문이다.
    """
    now = clock.now()
    prefix = f"{market}:"

    universe = store.get(UNIVERSE, as_of=now, lookback=REPORT_SPAN_DAYS)
    everything = (
        {str(value) for value in universe["entity_id"]} if not universe.empty else set()
    )

    # 휴장일 행은 거래대금도 0 이라 유동성 중앙값을 끌어내린다.
    prices = read_prices(store, as_of=now, lookback=90)
    if prices.empty:
        ranked: list[str] = []
    else:
        liquidity = prices.groupby("entity_id")["value"].median().sort_values(ascending=False)
        ranked = [str(entity) for entity in liquidity.index]

    tail = sorted(everything - set(ranked))
    return [
        entity[len(prefix):]
        for entity in [*ranked, *tail]
        if entity.startswith(prefix)
    ]


def run_us_price_backfill(
    store: Store,
    clock: Clock,
    *,
    years: int,
    limit: int | None,
    dry_run: bool = False,
) -> int:
    """미장 일봉 백필. LS 해외주식 g3204, **종목 축**.

    국장처럼 세션 축으로 돌 수가 없다 — 이유와 그 대가(생존편향)는
    ``ls_us_source`` 모듈 docstring 에 적었다.
    """
    market = Market.US
    source = LsUsSource.from_env(clock=clock)
    if not source.usable():
        print("LS_US_APPKEY / LS_US_APPSECRET 이 없다.", file=sys.stderr)
        return 2

    user_agent = os.environ.get(UA_ENV, "")
    if not user_agent:
        print(f"{UA_ENV} 가 없다. 미장 종목 명단을 SEC 에서 받지 못한다.", file=sys.stderr)
        return 2

    now = clock.now()
    end = now.astimezone(UTC).date()
    start = end - timedelta(days=365 * years)

    listings = fetch_listings(user_agent)
    if limit is not None:
        listings = listings[:limit]

    backfiller = UsPriceBackfiller(
        store=store,
        source=source,
        clock=clock,
        archive=RawArchive(root=store.root),
        policy=publication_policy(store, market, clock=clock),
        market=market,
        # SEC 가 거래소를 주므로 LS 에 되묻지 않는다 — 종목당 1~2 호출이 준다.
        exchanges={listing.ticker: listing.exchange for listing in listings},
    )

    symbols = [listing.ticker for listing in listings]
    pending = backfiller.pending(symbols)
    total_batches = len(backfiller.batches(symbols))
    print(
        f"{market} 일봉 {start} ~ {end}  종목 {len(symbols)}개 / "
        f"배치 {total_batches}개 (이미 적재 {total_batches - len(pending)}, "
        f"남은 {len(pending)})"
    )
    if dry_run or not pending:
        if not pending:
            print("할 일이 없다.")
        return 0

    progress = ProgressLog(root=store.root, plan_id=f"{market}-prices-{start}-{end}")
    report = BackfillReport(market=market)
    started = time.monotonic()  # invariant-allow: wallclock
    done = 0
    pending_symbols = sum(len(group) for _, group in pending)

    def tick(symbol: str, rows: int) -> None:
        # 배치가 900종목이라 배치 단위로만 찍으면 20분 동안 아무것도 안 보인다.
        nonlocal done
        done += 1
        if done % 50:
            return
        elapsed = timedelta(seconds=time.monotonic() - started)  # invariant-allow: wallclock
        remaining = eta(done, pending_symbols, elapsed)
        tail = f"  남은시간 ~{_short(remaining)}" if remaining else ""
        print(f"  [{done}/{pending_symbols}] {symbol} rows={rows}{tail}", flush=True)

    backfiller.on_symbol = tick

    for batch, group in pending:
        print(f"\n=== 배치 {batch:03d} — {len(group)}종목 ({group[0]}~{group[-1]}) ===", flush=True)
        result = backfiller.run_batch(batch, group, start=start, end=end)
        report.absorb(result)
        progress.record(result, at=clock.now())
        print(
            f"배치 {batch:03d} 완료 — rows={result.rows:,} "
            f"미취급 {len(result.missing)}종목",
            flush=True,
        )

    print(
        f"\n완료 — 배치 {report.sessions}개 (건너뜀 {report.skipped}), "
        f"행 {report.counts.get('prices', 0):,}"
    )
    if report.failures:
        print(f"실패 {len(report.failures)}건 (앞 10건):")
        for unit, error in report.failures[:10]:
            print(f"  {unit}: {error}")
    return 0


def run_us_universe_backfill(
    store: Store, clock: Clock, *, years: int, dry_run: bool = False
) -> int:
    """미장 명단 — 이미 들어온 시세에서 유도해 적재한다.

    시세만 있고 명단이 없으면 Analyst 는 대상이 0개다. 실측에서 미장
    chart·risk 가 "300세션 측정, 표본 0일 / 0행, IC nan" 으로 끝났다.
    유도 규칙과 그 한계(생존편향)는 ``us_universe_panel`` 모듈 docstring 에
    있다.

    시세를 **한 번만** 훑는다. ``prices`` 는 관측지연을 선언하지 않아 창을
    옮겨 가며 읽으면 매번 전체 파티션을 다시 연다. 컬럼은 자연키 + 관측시각
    셋만 가져온다 — 나머지는 여기서 쓸 일이 없고 문자열 컬럼이 무게의
    대부분이다.
    """
    market = Market.US
    now = clock.now()

    print(f"{market} 명단 — 시세에서 유도 (최근 {years}년)", flush=True)
    # 여기서 묻는 것은 "그 세션에 봉이 있었나" 하나이고 종가는 읽지도 않는다.
    # 거르려고 close 를 얹으면 5년 스캔에 컬럼 하나가 통째로 더 붙는다.
    # 미장이라 KRX 휴장일 0 세션 자체가 없다.
    frame = store.get(  # invariant-allow: price-read
        PRICES,
        as_of=now,
        lookback=365 * years + 10,
        market=str(market),
        columns=["observed_at"],
    )
    if frame.empty:
        print("시세가 없다. 먼저 --market US 로 일봉을 백필해야 한다.", file=sys.stderr)
        return 2

    report = up.BuildReport()
    sessions: list[date] = []
    last_seen: dict[str, tuple[date, object, object]] = {}
    started = time.monotonic()  # invariant-allow: wallclock

    for day, records in up.group_sessions(frame):
        sessions.append(day)
        rows = up.session_rows(records, market=market)
        for row in rows:
            last_seen[str(row["entity_id"])] = (
                day, row["valid_from"], row["observed_at"]
            )
            report.entities.add(str(row["entity_id"]))

        run_id = up.run_id_for(market, day)
        if store.ingest_run_recorded(UNIVERSE, run_id):
            report.skipped += 1
            continue
        if dry_run:
            report.sessions += 1
            report.rows += len(rows)
            continue

        report.rows += store.append(
            UNIVERSE, rows, ingest_run_id=run_id, source=up.SOURCE
        )
        report.sessions += 1

        if report.sessions % 50 == 0:
            elapsed = timedelta(seconds=time.monotonic() - started)  # invariant-allow: wallclock
            remaining = eta(report.sessions, len(sessions), elapsed)
            tail = f"  남은시간 ~{_short(remaining)}" if remaining else ""
            print(f"  [{report.sessions}] {day} rows={len(rows)}{tail}", flush=True)

    # 상폐는 마지막에 한 번. 마지막 봉이 언제였는지는 전 세션을 다 봐야 안다.
    dead = up.delisting_rows(last_seen, sessions, market=market)
    if dead and not dry_run:
        run_id = up.delisting_run_id(market, sessions[-1])
        if not store.ingest_run_recorded(UNIVERSE, run_id):
            report.delisted = store.append(
                UNIVERSE, dead, ingest_run_id=run_id, source=up.SOURCE
            )
    elif dead:
        report.delisted = len(dead)

    print(f"\n완료 — {report.render()}")
    return 0


def run_us_shares_backfill(
    store: Store, clock: Clock, *, years: int, sessions: int | None = None,
    dry_run: bool = False,
) -> int:
    """미장 상장주식수 — SEC 벌크 companyfacts 한 벌.

    소스를 왜 이것으로 골랐는지(그리고 LS·companyconcept·frames 를 왜 버렸는지)
    는 ``us_shares`` 모듈 docstring 에 실측과 함께 있다.

    **한 번의 append 로 끝낸다.** 종목마다 넣으면 종목당 공시가 서로 다른
    파티션으로 흩어져 파일이 십수만 개가 된다.
    """
    market = Market.US
    now = clock.now()
    user_agent = os.environ.get(UA_ENV, "")
    if not user_agent:
        print(f"{UA_ENV} 가 없다. SEC 는 신원 없는 요청을 막는다.", file=sys.stderr)
        return 2

    listings = fetch_listings(user_agent)
    # 시세가 있는 종목만 대상이다. 명단 전체(7,112)를 넣으면 시가총액을 만들
    # 수 없는 종목의 주식수만 창고에 쌓인다.
    traded = store.get(
        UNIVERSE, as_of=now, lookback=30, market=str(market), columns=["market"]
    )
    if traded.empty:
        print(
            "미장 명단이 비어 있다. 먼저 --market US --table universe 를 돌린다.",
            file=sys.stderr,
        )
        return 2
    tickers = {str(entity).partition(":")[2] for entity in traded["entity_id"].unique()}
    listings = [listing for listing in listings if listing.ticker in tickers]

    facts = us_sh.SecBulkFacts(
        path=store.root / "raw" / us_sh.SOURCE / "companyfacts.zip",
        user_agent=user_agent,
    )
    stamp = us_sh.refresh_stamp(now)
    run_id = us_sh.shares_run_id(market, stamp)
    print(
        f"{market} 상장주식수 — SEC companyfacts {stamp}  "
        f"종목 {len(listings):,}개 (시세 있는 것만)"
    )
    if store.ingest_run_recorded(us_sh.MARKET_STATS, run_id):
        print(f"{stamp} 은 이미 적재됐다. 할 일이 없다.")
        return 0
    if dry_run:
        return 0

    print(
        f"  벌크 파일 확인 (묵은 정도 {facts.age(now=now)}, "
        f"{facts.max_age_days}일 넘으면 1.4GB 다시 받는다 — 실측 ~3분)…",
        flush=True,
    )
    facts.download(now=now)

    # 이미 들어간 것을 다시 넣지 않는다 — 같은 값을 정정본으로 쌓으면 창고가
    # "정정이 있었다" 는 거짓을 기록한다.
    stored = store.get(
        us_sh.MARKET_STATS,
        as_of=now,
        lookback=365 * years + 400,
        market=str(market),
        columns=["metric", "value", "valid_from"],
    )
    if not stored.empty:
        stored = stored[stored["metric"] == us_sh.SHARES]
    known = us_sh.existing_shares(stored.to_dict(orient="records")) if not stored.empty else {}
    print(f"  이미 적재된 주식수 {len(stored):,}행", flush=True)

    backfiller = us_sh.UsSharesBackfiller(
        store=store,
        facts=facts,
        market=market,
        cutoff_hour=int(store.config(us_sh.CONFIG_CUTOFF_KEY, as_of=now)),
        since=now.astimezone(UTC).date() - timedelta(days=365 * years),
        known=known,
    )
    started = time.monotonic()  # invariant-allow: wallclock
    report = backfiller.run(listings, run_id=run_id)
    facts.close()

    elapsed = timedelta(seconds=time.monotonic() - started)  # invariant-allow: wallclock
    print(f"\n완료 ({_short(elapsed)}) — {report.render()}")
    return 0


def run_us_market_cap_backfill(
    store: Store, clock: Clock, *, years: int, sessions: int | None = None,
    dry_run: bool = False,
) -> int:
    """미장 시가총액 — **수집이 아니라 유도다.**

    상장주식수는 분기 공시라 매일 바뀌지 않는다. 그래서 그날 시가총액은
    "그날 종가 × **그날까지 알려진 마지막** 주식수" 다. 어느 것이 '그날까지
    알려진' 것인지는 ``SharesTimeline.known_at`` 이 봉의 관측시각으로 자른다 —
    분기 말 공시를 그 분기 첫날에 쓰면 미래를 보는 것이다.

    세션마다 한 번 append 한다. 관측시각이 그 세션 봉의 것이라 한 세션이 한
    파티션에 들어가고, 파티션당 파일이 하나로 끝난다.
    """
    market = Market.US
    now = clock.now()
    end = now.astimezone(UTC).date()
    # 일일 실행은 최근 며칠만 본다. 매일 5년을 훑으면 ``prices`` 가 관측지연을
    # 선언하지 않아 창을 좁혀도 파티션을 전부 열고, 그 비용이 매일 붙는다.
    # 백필(sessions 없음)만 전 구간을 돈다.
    start = (
        end - timedelta(days=sessions * 2 + 5)
        if sessions is not None
        else end - timedelta(days=365 * years)
    )

    shares = store.get(
        us_sh.MARKET_STATS,
        as_of=now,
        lookback=365 * years + 400,
        market=str(market),
        columns=["metric", "value", "revision", "valid_from", "observed_at"],
    )
    if not shares.empty:
        shares = shares[shares["metric"] == us_sh.SHARES]
    if shares.empty:
        print(
            "미장 상장주식수가 없다. 먼저 --market US --table shares-sec 를 돌린다.",
            file=sys.stderr,
        )
        return 2
    timelines = us_sh.build_timelines(shares.to_dict(orient="records"))
    print(
        f"{market} 시가총액 {start} ~ {end} — 주식수 {len(shares):,}행 / "
        f"종목 {len(timelines):,}개"
    )

    sessions = 0
    rows = 0
    skipped = 0
    started = time.monotonic()  # invariant-allow: wallclock

    # 창을 옮기며 읽는다. as_of 를 옮기면 뒤늦게 온 정정본을 놓친다.
    #
    # **``lookback`` 은 ``until`` 이 아니라 ``as_of`` 에서 거슬러 센다**
    # (store/reader.py ``_valid_from_floor``). 창 길이를 그대로 넘기면 옛
    # 창이 통째로 빈다 — 실측에서 5년을 돌렸는데 마지막 1년(250세션)만
    # 채워졌다. 하한은 언제나 ``as_of`` 기준으로 환산한다.
    today = now.astimezone(UTC).date()
    for window_start, window_end in us_sh.year_windows(start, end):
        # **``until`` 은 열린 끝이다** (store/reader.py: ``valid_from < ?``)
        # 반면 ``year_windows`` 의 ``window_end`` 는 닫힌 끝이고 다음 창은
        # 그 다음날부터 시작한다. 그래서 ``window_end`` 를 그대로 넘기면 창
        # 경계의 세션 하나가 어느 창에도 안 걸려 통째로 사라진다 — 실측:
        # 5년 적재에서 2023-08-14 · 2024-08-13 · 2025-08-13 세 세션만
        # 시가총액이 없었다(1,252 거래일 중 1,249). 하루를 더해 닫힌 끝을
        # 열린 끝으로 옮긴다.
        stop = window_end + timedelta(days=1)
        frame = read_prices(
            store,
            as_of=now,
            lookback=(today - window_start).days,
            until=datetime(stop.year, stop.month, stop.day, tzinfo=UTC),
            market=str(market),
            columns=["close", "observed_at"],
        )
        for day, bars in us_sh.session_bars(frame):
            run_id = us_sh.market_cap_run_id(market, day)
            if store.ingest_run_recorded(us_sh.MARKET_STATS, run_id):
                skipped += 1
                continue
            batch = us_sh.market_cap_rows(bars, timelines, market=market)
            if not batch:
                # 빈 것을 완료로 기록하면 주식수가 나중에 들어와도 영영 건너뛴다.
                continue
            if dry_run:
                sessions += 1
                rows += len(batch)
                continue
            rows += store.append(us_sh.MARKET_STATS, batch, ingest_run_id=run_id)
            sessions += 1
            if sessions % 50 == 0:
                spent = time.monotonic() - started  # invariant-allow: wallclock
                elapsed = timedelta(seconds=spent)
                print(
                    f"  [{sessions}] {day} rows={len(batch):,}  경과 {_short(elapsed)}",
                    flush=True,
                )

    print(f"\n완료 — 세션 {sessions:,}개 (건너뜀 {skipped:,}), 시가총액 {rows:,}행")
    return 0


def run_dart_filings_backfill(
    store: Store,
    clock: Clock,
    *,
    market: Market,
    years: int,
    sessions: int | None = None,
    dry_run: bool = False,
) -> int:
    """DART 공시 목록 — `event` Analyst 의 입력.

    **날짜 축으로 돈다.** 회사 축이면 2,700종목 × 5년이라 콜이 폭발한다.
    휴장일도 도는 이유는 공시가 휴일에도 접수되기 때문이다 — 거래일로 자르면
    그 공시를 영영 못 받는다.

    ``sessions`` 는 **거래일이 아니라 달력일**이다. 이 표의 축이 달력일이라
    그렇다. 일일 수집이 이 인자를 준다.

    **일일 수집이 창을 좁혀야 하는 이유가 다른 표와 다르다.** 여기서는 읽기
    비용이 아니라 **API 콜**이 문제다. 공시가 없는 날(주말·연휴)은 창고에
    남길 배치가 없어 매니페스트에 기록되지 않고, 그래서 영원히 "남은" 상태로
    있는다 — ``--years 1`` 로 매일 돌리면 그 244일을 매일 다시 물어본다.
    """
    source = DartSource()
    if not source.api_key:
        print("OPENDART_API_KEY 가 없다.", file=sys.stderr)
        return 2

    now = clock.now()
    end = now.astimezone(UTC).date()
    span = sessions - 1 if sessions is not None else 365 * years
    start = end - timedelta(days=max(span, 0))

    backfiller = FilingsBackfiller(
        store=store,
        source=source,
        policy=FilingPolicy(
            hour_kst=int(store.config("backfill.dart_publication_hour_kst", as_of=now)),
            clock=clock,
        ),
        market=str(market),
    )

    days = backfiller.plan(start, end)
    pending = backfiller.pending(days)
    print(
        f"{market} 공시 {start} ~ {end}  날짜 {len(days)}개 × 시장 2개 "
        f"(이미 적재 {len(days) * 2 - len(pending)}, 남은 {len(pending)})"
    )
    if dry_run or not pending:
        if not pending:
            print("할 일이 없다.")
        return 0

    report = FilingsReport()
    started = time.monotonic()  # invariant-allow: wallclock

    for index, (day, corp_class) in enumerate(pending, start=1):
        result = backfiller.run_day(day, corp_class)
        report.absorb(result)
        report.days += 1
        if index % 50 == 0 or index == len(pending):
            elapsed = timedelta(seconds=time.monotonic() - started)  # invariant-allow: wallclock
            remaining = eta(index, len(pending), elapsed)
            tail = f"  남은시간 ~{_short(remaining)}" if remaining else ""
            print(
                f"  [{index}/{len(pending)}] {day} {corp_class} "
                f"누적 {report.rows:,}행{tail}",
                flush=True,
            )

    source.close()
    print(f"\n완료 — {report.render()}")
    for unit, message in report.failures[:10]:
        print(f"  실패 {unit}: {message}")
    return 1 if report.failures else 0


def run_flow_backfill(
    store: Store, clock: Clock, *, market: Market, years: int, limit: int | None
) -> int:
    """LS t1717 수급 백필. 레포에서 유일하게 **종목 축**으로 도는 수집이다."""
    credentials = LSCredentials.from_env()
    if not credentials.usable():
        print("LS_APPKEY / LS_APPSECRET 이 없다.", file=sys.stderr)
        return 2

    now = clock.now()
    end = now.astimezone(UTC).date()
    start = end - timedelta(days=365 * years)

    client = LSClient(credentials=credentials, clock=clock, live_trading=False)
    backfiller = LSFlowBackfiller(
        store=store,
        source=LSFlowSource(client=client),
        clock=clock,
        archive=RawArchive(root=store.root),
        observed_at_for=publication_policy(store, market, clock=clock).for_session,
        market=str(market),
    )

    symbols = flow_symbols(store, clock, market)
    if limit is not None:
        symbols = symbols[:limit]
    pending = backfiller.pending(symbols)
    print(
        f"{market} 수급 {start} ~ {end}  종목 {len(symbols)}개 "
        f"(이미 적재 {len(symbols) - len(pending)}개, 남은 {len(pending)}개)"
    )
    if not pending:
        print("할 일이 없다.")
        return 0

    progress = ProgressLog(root=store.root, plan_id=f"{market}-flows-{start}-{end}")
    report = BackfillReport(market=market)
    started = time.monotonic()  # invariant-allow: wallclock

    for index, symbol in enumerate(pending, start=1):
        result = backfiller.run_symbol(symbol, start, end)
        report.absorb(result)
        progress.record(result, at=clock.now())

        elapsed = timedelta(seconds=time.monotonic() - started)  # invariant-allow: wallclock
        remaining = eta(index, len(pending), elapsed)
        status = "SKIP" if result.skipped else ("FAIL" if result.error else "ok")
        tail = f"  남은시간 ~{_short(remaining)}" if remaining else ""
        print(
            f"[{index}/{len(pending)}] {symbol} {status}  flows={result.rows}{tail}"
            + (f"  {result.error}" if result.error else ""),
            flush=True,
        )

    print(
        f"\n완료 — 종목 {report.sessions}개 (건너뜀 {report.skipped}), "
        f"{report.render_counts()}, 실패 {len(report.failures)}건"
    )
    return 1 if report.failures else 0


def run_dart_backfill(store: Store, clock: Clock, *, market: Market, years: int) -> int:
    """DART 재무 백필. 분기 × 100개 배치 축으로 돈다.

    종목당 개별 호출이면 5년치가 58,000콜이라 DART 일 한도(20,000)에 걸려
    3일이 걸린다. 다중회사 조회로 배치당 100개씩 묶으면 수백 콜이면 끝난다.
    """
    now = clock.now()
    source = DartSource()
    if not source.api_key:
        print("OPENDART_API_KEY 가 없다.", file=sys.stderr)
        return 2

    backfiller = DartBackfiller(
        store=store, source=source, clock=clock,
        archive=RawArchive(root=store.root),
        policy=FilingPolicy(
            hour_kst=int(store.config("backfill.dart_publication_hour_kst", as_of=now)),
            clock=clock,
        ),
        market=str(market),
    )

    # 유니버스에 한 번이라도 있었던 종목만. 상폐 종목도 포함한다 —
    # 재무만 빠지면 그 종목의 과거가 반쪽이 되고 생존편향이 되살아난다.
    universe = store.get(UNIVERSE, as_of=now, lookback=REPORT_SPAN_DAYS)
    prefix = f"{market}:"
    wanted = {
        str(entity)[len(prefix):]
        for entity in universe["entity_id"].unique()
        if str(entity).startswith(prefix)
    }
    mapping = source.corp_codes()
    corps = [mapping[stock] for stock in sorted(wanted) if stock in mapping]
    batches = list(batched(corps))
    print(f"유니버스 {len(wanted)}종목 중 corp_code 매칭 {len(corps)}개 → 배치 {len(batches)}개")

    end_year = now.astimezone(UTC).year
    plan = [
        (year, quarter, index)
        for year in range(end_year - years, end_year + 1)
        for quarter in (1, 2, 3, 4)
        for index in range(len(batches))
    ]
    pending = backfiller.pending(plan)
    print(f"작업 {len(plan)}개 (이미 적재 {len(plan) - len(pending)}, 남은 {len(pending)})")
    if not pending:
        print("할 일이 없다.")
        return 0

    progress = ProgressLog(root=store.root, plan_id=f"{market}-dart-{end_year - years}")
    report = BackfillReport(market=market)
    started = time.monotonic()  # invariant-allow: wallclock

    for index, (year, quarter, batch) in enumerate(pending, start=1):
        result = backfiller.run_batch(
            batches[batch], year=year, quarter=quarter, batch=batch
        )
        report.absorb(result)
        progress.record(result, at=clock.now())

        elapsed = timedelta(seconds=time.monotonic() - started)  # invariant-allow: wallclock
        remaining = eta(index, len(pending), elapsed)
        if result.skipped:
            status = "SKIP"
        elif result.deferred:
            status = "WAIT"   # 아직 공표되지 않은 공시가 섞여 있다
        else:
            status = "FAIL" if result.error else "ok"
        tail = f"  남은시간 ~{_short(remaining)}" if remaining else ""
        print(
            f"[{index}/{len(pending)}] {result.unit} {status}  "
            f"fundamentals={result.rows}{tail}"
            + (f"  {result.error}" if result.error else ""),
            flush=True,
        )

    print(
        f"\n완료 — 작업 {report.sessions}개 "
        f"(건너뜀 {report.skipped}, 미공표 대기 {report.deferred}), "
        f"{report.render_counts()}, 실패 {len(report.failures)}건"
    )
    return 1 if report.failures else 0


def probe_codes(source: KrxSource, sessions: list[date], count: int) -> frozenset[str]:
    """시험 실행용 종목 표본.

    구간 **첫 세션**의 명단에서 뽑는다. 오늘 명단에서 뽑으면 5년을 살아남은
    종목만 고르게 되고, 시험 실행이 생존편향을 검증하지 못한다.
    """
    listed = source.listed_on(sessions[0])
    return frozenset(str(item["code"]) for item in listed[:count])


def _display(path: Path) -> str:
    """레포 안이면 상대경로로, 밖이면 그대로."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _short(delta: timedelta | None) -> str:
    if delta is None:
        return "-"
    total = int(delta.total_seconds())
    hours, rest = divmod(total, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours}시간 {minutes}분"
    if minutes:
        return f"{minutes}분 {seconds}초"
    return f"{seconds}초"


# -----------------------------------------------------------------------------
# 검증 리포트 — 창고를 store.get(as_of=...) 로만 읽는다 (불변식 1·9)
# -----------------------------------------------------------------------------


#: 리포트가 훑는 구간. 백필 구간보다 넉넉히 잡아 앞뒤를 놓치지 않는다.
REPORT_SPAN_DAYS = 365 * 6


def render_report(store: Store, clock: Clock, *, market: Market, as_of: datetime) -> str:
    """창고를 창 단위로 훑어 집계한다.

    창 나누기와 커버리지 집계는 대시보드와 **같은 함수**를 쓴다
    (``quant_rl_trading/dashboard/services/data_quality.py``). 두 벌로 두면 같은 데이터에서
    서로 다른 커버리지 숫자가 나오고, 어느 쪽이 맞는지 아무도 모르게 된다.
    """
    lines = [
        "# 백필 검증 리포트",
        "",
        f"- 시장: {market}",
        f"- as_of: {as_of.isoformat()}",
        "",
    ]

    coverage = dq.collect_coverage(store, as_of=as_of, lookback=REPORT_SPAN_DAYS)
    if not coverage.rows:
        lines.append("prices 가 비어 있다. 백필이 실행되지 않았거나 전부 실패했다.")
        return "\n".join(lines)

    #: 종목 → (마지막 세션, 상장여부, 이름). 창을 시간순으로 훑으며 갱신한다.
    state: dict[str, tuple[date, bool, str]] = {}
    listed_by_year: dict[int, set[str]] = {}

    for window in dq.iter_windows(store, UNIVERSE, as_of=as_of, lookback=REPORT_SPAN_DAYS):
        if window.empty:
            continue
        for row in window.sort_values("valid_from").to_dict(orient="records"):
            entity = str(row["entity_id"])
            session = row["valid_from"].date()
            is_listed = bool(row["is_listed"])
            previous = state.get(entity)
            if previous is None or session >= previous[0]:
                state[entity] = (session, is_listed, str(row["name"]))
            if is_listed:
                listed_by_year.setdefault(session.year, set()).add(entity)

    sessions = coverage.sessions
    first, last = sessions[0], sessions[-1]
    expected = len(trading_days(market, first, last))
    covered = len(sessions)
    total = coverage.total

    lines += [
        "## 커버리지",
        "",
        f"- 구간: {first} ~ {last}",
        f"- 거래일: 기대 {expected}개 / 적재 {covered}개 ({covered / expected:.1%})",
        f"- 종목(누적): {len(coverage.entities):,}개",
        f"- prices 총 행수: {total:,}",
        f"- 일별 평균 종목수: {total / covered:,.0f}",
        "",
        "## 결측",
        "",
        f"- close 결측률: {sum(coverage.close_null.values()) / total:.4%}",
        f"- volume 결측률: {sum(coverage.volume_null.values()) / total:.4%}",
        "",
    ]

    missing = [day for day in trading_days(market, first, last) if day not in coverage.rows]
    if missing:
        lines += [
            f"- **빠진 거래일 {len(missing)}개**: "
            + ", ".join(day.isoformat() for day in missing[:10])
            + (" …" if len(missing) > 10 else ""),
            "",
        ]

    if state:
        delisted = [name for _, listed, name in state.values() if not listed]
        lines += [
            "## 유니버스",
            "",
            f"- 누적 종목: {len(state):,}개",
            f"- 현재 상장: {len(state) - len(delisted):,}개",
            f"- 상장폐지 감지: **{len(delisted):,}개**",
            "",
            "### 연도별 상장 종목 수",
            "",
            "| 연도 | 종목 수 |",
            "|---|---|",
        ]
        lines += [
            f"| {year} | {len(codes):,} |" for year, codes in sorted(listed_by_year.items())
        ]
        lines.append("")
        if not delisted:
            lines += [
                "> ⚠️ 상장폐지 감지가 0이다. 생존편향이 제거되지 않았다는 뜻이며,",
                "> 이 데이터 위의 백테스트는 실제보다 좋게 나온다.",
                "",
            ]

    return "\n".join(lines)


# -----------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", default="KR", choices=[m.value for m in Market])
    # 기본값은 store.config 의 backfill.years 다. 여기 숫자를 따로 들면
    # 두 값이 갈라져도 아무도 모른다 (불변식 10).
    parser.add_argument("--years", type=int)
    parser.add_argument("--symbols", type=int, help="시험 실행: 이 개수만큼만 백필")
    parser.add_argument("--sessions", type=int, help="최근 N 거래일만")
    parser.add_argument(
        "--table",
        choices=[
            *sorted(PANELS), *sorted(OPENAPI_PANELS),
            FLOW_LS, DART, DART_FILINGS, UNIVERSE,
            US_SHARES, US_MARKET_CAP,
        ],
        help="패널 테이블 하나만 백필. 생략하면 prices+universe",
    )
    parser.add_argument("--dry-run", action="store_true", help="계획만 출력")
    parser.add_argument(
        "--data-root",
        type=Path,
        help=(
            "창고 위치. 시험 실행은 반드시 별도 루트로 돌린다 — 10종목만 든 세션이 "
            "본 창고에 매니페스트로 박히면 전체 실행이 그 날짜를 영영 건너뛴다"
        ),
    )
    parser.add_argument("--report", action="store_true", help="검증 리포트만 출력")
    parser.add_argument("--as-of", help="리포트 기준 시각 (ISO8601, 기본 지금)")
    parser.add_argument(
        "--out", type=Path, default=REPO_ROOT / "docs" / "backfill-report.md"
    )
    args = parser.parse_args(argv)

    load_env()
    market = Market(args.market)
    clock = LiveClock()
    store = build_store(args.data_root)

    if args.report:
        as_of = (
            datetime.fromisoformat(args.as_of).astimezone(UTC)
            if args.as_of
            else clock.now()
        )
        text = render_report(store, clock, market=market, as_of=as_of)
        print(text)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
        print(f"\n(저장: {_display(args.out)})")
        return 0

    if args.table == DART:
        return run_dart_backfill(
            store, clock, market=market,
            years=args.years or int(store.config("backfill.years", as_of=clock.now())),
        )

    if args.table == DART_FILINGS:
        return run_dart_filings_backfill(
            store, clock, market=market,
            years=args.years or int(store.config("backfill.years", as_of=clock.now())),
            sessions=args.sessions,
            dry_run=args.dry_run,
        )

    if args.table == FLOW_LS:
        return run_flow_backfill(
            store, clock, market=market,
            years=args.years or int(store.config("backfill.years", as_of=clock.now())),
            limit=args.symbols,
        )

    if args.table in (US_SHARES, US_MARKET_CAP):
        if market is not Market.US:
            print(f"{args.table} 는 미장 전용이다 (--market US).", file=sys.stderr)
            return 2
        runner = (
            run_us_shares_backfill if args.table == US_SHARES else run_us_market_cap_backfill
        )
        return runner(
            store,
            clock,
            years=args.years or int(store.config("backfill.years", as_of=clock.now())),
            sessions=args.sessions,
            dry_run=args.dry_run,
        )

    if market is Market.US and args.table == UNIVERSE:
        # 미장 명단은 수집이 아니라 유도다 — 이미 받은 시세에서 만든다.
        return run_us_universe_backfill(
            store,
            clock,
            years=args.years or int(store.config("backfill.years", as_of=clock.now())),
            dry_run=args.dry_run,
        )

    if market is Market.US:
        return run_us_price_backfill(
            store,
            clock,
            years=args.years or int(store.config("backfill.years", as_of=clock.now())),
            limit=args.symbols,
            dry_run=args.dry_run,
        )

    if market is not Market.KR:
        print(f"{market} 백필 소스가 아직 없다.", file=sys.stderr)
        return 2

    return run_backfill(
        store,
        clock,
        market=market,
        years=args.years or int(store.config("backfill.years", as_of=clock.now())),
        symbols=args.symbols,
        sessions_limit=args.sessions,
        dry_run=args.dry_run,
        table=args.table,
    )


if __name__ == "__main__":
    raise SystemExit(main())
