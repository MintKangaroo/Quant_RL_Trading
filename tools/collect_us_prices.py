"""미장 일봉 **증분** 수집 — LS 해외주식 g3204, 최근 며칠만.

    uv run python tools/collect_us_prices.py --sessions 3
    uv run python tools/collect_us_prices.py --symbols AAPL,MSFT --sessions 5

## 왜 별도 도구인가

``tools/backfill.py --market US --table prices`` 는 **5년을 채우는 물건**이다.
6,648종목 × 8배치, 종목당 4~8호출이라 반나절이 든다. 그래서 미장 일봉은 사람이
백필을 돌릴 때만 들어왔고 그 사이 창고가 며칠씩 밀렸다 — 실측 2026-08-18:

    국장 시세 최신 2026-08-14 (2,879종목)
    미장 시세 최신 2026-08-12 (6,647종목)   ← 3세션 결손

이 도구는 **구간을 줄여** 종목당 1호출로 끝낸다. 자세한 근거(미장에 다중조회
TR 이 없다는 실측 포함)는 ``collectors/ls_us_source`` 의 "일일 증분" 절에 있다.

## 성공을 행 수로 판정하지 않는다

``0행`` 은 "이미 다 받았다" 와 "수집 경로가 고장났다" 를 같은 문구로 말한다.
이 저장소는 그것 때문에 환율이 11일간 조용히 밀린 적이 있다 (``collect_fx``).
그래서 종료코드는 **창고가 지금 쓸 만큼 최신인가** 로 정한다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.collectors.ls_us_source import (  # noqa: E402
    INDEX_PROXY_ETFS,
    LsUsSource,
    UsIncrementalCollector,
    normalize_bars,
)
from quant_rl_trading.collectors.market_hours import Market, trading_days  # noqa: E402
from quant_rl_trading.collectors.publication import (  # noqa: E402
    NotATradingDay,
    NotYetPublished,
    publication_policy,
)
from quant_rl_trading.collectors.raw import RawArchive  # noqa: E402
from quant_rl_trading.collectors.us_universe import UA_ENV, fetch_listings  # noqa: E402
from quant_rl_trading.replay.clock import Clock, LiveClock  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from quant_rl_trading.store.prices import read_prices  # noqa: E402
from tools.backfill import build_store, load_env  # noqa: E402

MARKET = Market.US

#: 창고가 이보다 많은 **거래일** 만큼 뒤처지면 실패로 본다. 1 을 주는 이유:
#: 정상 운영에서는 직전 세션까지 들어와 있어야 하고, 하루치 지연(수집 실행이
#: 공표 전에 돌았다)까지는 정상으로 본다. 이틀이 밀리면 사람이 봐야 한다.
STALE_AFTER_SESSIONS = 1

#: 심볼 명단을 창고에서 유도할 때 볼 구간(달력일). 연휴를 견딜 만큼 둔다 —
#: 짧게 잡으면 창고가 이미 밀려 있을 때 명단이 통째로 비고, 그러면 이 도구가
#: "할 일 없음" 으로 조용히 끝난다.
SYMBOL_LOOKBACK_DAYS = 20


#: 대용 ETF 수집의 표지. 전체 실행과 run_id 가 갈려야 한다 — 같은 칸을 쓰면
#: 아침에 도는 4종목 수집이 그 세션을 "적재됨" 으로 잠가 저녁의 6,647종목이
#: 통째로 건너뛰어진다. (``ls_us_source`` 의 ``incremental_run_id`` 주석)
ETF_SCOPE = "etf"

#: 심볼 → 거래소 코드 캐시. **이게 없으면 매일 두 배로 든다.**
#: 거래소를 모르면 나스닥으로 한 번 치고 0행이면 뉴욕으로 다시 친다(1~2호출).
#: 한 번 맞힌 값을 남겨 두면 다음날부터 항상 1호출이다 — 6,647종목에서
#: 그 차이가 약 45분이다. 창고 표가 아니라 파일인 이유: 되풀이 계산의 결과일
#: 뿐이고 사실의 기록이 아니다(지워도 다시 만들어진다).
EXCHANGE_CACHE = "us_exchanges.json"


def load_exchanges(store: Store) -> dict[str, str]:
    path = store.root / "backfill" / EXCHANGE_CACHE
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # 캐시가 깨졌으면 버린다. 잃는 것은 시간뿐이다.
        return {}
    return {str(key): str(value) for key, value in loaded.items()}


def save_exchanges(store: Store, exchanges: dict[str, str]) -> None:
    path = store.root / "backfill" / EXCHANGE_CACHE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(exchanges, ensure_ascii=False, indent=0, sort_keys=True),
        encoding="utf-8",
    )


def _ticker(entity_id: str) -> str:
    _, _, tail = entity_id.partition(":")
    return tail or entity_id


def store_symbols(store: Store, *, as_of: datetime) -> list[tuple[str, float]]:
    """창고에 이미 있는 미장 종목 — (티커, 최근 평균 거래대금).

    **SEC 명단이 아니라 창고를 본다.** 이 도구는 "이미 추적 중인 종목을 최신으로
    유지" 하는 물건이라 새 명단이 필요 없고, SEC 호출도 안 붙는다. 신규 상장은
    ``--source sec`` 또는 주기적인 전체 백필로 들어온다.
    """
    frame = read_prices(
        store,
        as_of=as_of,
        market=str(MARKET),
        lookback=SYMBOL_LOOKBACK_DAYS,
        columns=["entity_id", "valid_from", "value"],
    )
    if frame.empty:
        return []
    ranked = frame.groupby("entity_id")["value"].mean().fillna(0.0)
    return [(_ticker(str(entity)), float(value)) for entity, value in ranked.items()]


def sec_symbols(user_agent: str) -> tuple[list[tuple[str, float]], dict[str, str]]:
    """SEC 명단 — 신규 상장까지 잡는다. 거래소도 같이 오므로 되묻지 않는다."""
    listings = fetch_listings(user_agent)
    return (
        [(item.ticker, 0.0) for item in listings],
        {item.ticker: item.exchange for item in listings},
    )


def latest_publishable(policy, *, now: datetime) -> date | None:
    """지금 시점에 **이미 공표된** 가장 최근 미장 세션.

    창고의 최신 세션을 이것과 비교해야 판정이 선다. "오늘 날짜" 와 비교하면
    주말·휴장·공표지연이 전부 결손으로 보인다.
    """
    day = now.astimezone(UTC).date()
    for candidate in reversed(trading_days(MARKET, day - timedelta(days=20), day)):
        try:
            policy.for_session(candidate)
        except NotYetPublished:
            continue
        except NotATradingDay:
            continue
        return candidate
    return None


#: 세션이 "채워졌다" 고 볼 최소 종목 비율. 최근 세션들의 중앙값 대비다.
#: **최댓값으로 판정하면 안 된다** — 몇 종목만 받고 끝난 실행이 그 세션을
#: 최신으로 만들어 나머지 6,600종목의 결손을 통째로 가린다. 소량 검증
#: (``--symbols``/``--top``)이 바로 그 모양이라 실제로 겪는다.
COVERAGE_RATIO = 0.5


def warehouse_latest(store: Store, *, as_of: datetime) -> tuple[date | None, dict[date, int]]:
    """창고의 최신 미장 세션 — **종목 수가 찬 세션만** 센다.

    (세션, 세션별 종목 수) 를 돌려준다. 두 번째 값은 판정이 틀렸을 때 사람이
    바로 볼 수 있도록 같이 내보낸다.
    """
    frame = read_prices(
        store,
        as_of=as_of,
        market=str(MARKET),
        lookback=SYMBOL_LOOKBACK_DAYS,
        columns=["entity_id", "valid_from"],
    )
    if frame.empty:
        return None, {}

    counts = {
        day.date(): int(n)
        for day, n in frame.groupby("valid_from")["entity_id"].nunique().items()
    }
    if not counts:
        return None, {}
    ordered = sorted(counts)
    typical = sorted(counts.values())[len(counts) // 2]
    floor = typical * COVERAGE_RATIO
    filled = [day for day in ordered if counts[day] >= floor]
    return (filled[-1] if filled else None), counts


def fresh_enough(store: Store, clock: Clock) -> bool:
    """창고가 쓸 만큼 최신인가. **아니면 크게 말한다.**"""
    now = clock.now()
    policy = publication_policy(store, MARKET, clock=clock)
    expected = latest_publishable(policy, now=now)
    newest, counts = warehouse_latest(store, as_of=now)

    tail = sorted(counts)[-5:]
    if tail:
        print("최근 세션 종목수: " + " · ".join(f"{d} {counts[d]:,}" for d in tail))
    if newest is None:
        print("미장 시세가 창고에 한 행도 없다(또는 전부 소량뿐이다).", file=sys.stderr)
        return False
    if expected is None:
        print(f"창고 최신 미장 세션 {newest} (기준 세션을 못 구했다)")
        return True

    behind = len([d for d in trading_days(MARKET, newest, expected) if d > newest])
    print(f"창고 최신 미장 세션 {newest} · 공표된 최신 세션 {expected} · {behind}세션 뒤")
    if behind > STALE_AFTER_SESSIONS:
        print(
            f"미장 시세가 {behind}세션 밀렸다(허용 {STALE_AFTER_SESSIONS}). "
            "LS 한도 초과로 배치가 끊겼는지, 명단이 비었는지 로그를 볼 것 — "
            "밀린 채로 두면 미장 점수·후보가 낡은 가격 위에서 나온다.",
            file=sys.stderr,
        )
        return False
    return True


def etf_history(
    store: Store, source: LsUsSource, clock: Clock, *, years: int, dry_run: bool
) -> int:
    """대용 ETF 의 **과거** 일봉. 한 번 돌면 끝나는 물건이다.

    ``UsPriceBackfiller`` 를 못 쓴다. 그쪽 run_id 는 ``bf-prices-US-b000`` 처럼
    **배치 번호**로만 걸려 있어서, 6,647종목 백필이 이미 b000 을 기록해 둔
    창고에서는 4종목짜리 명단이 통째로 "적재됨" 으로 건너뛰어진다. 종목 축
    수집에서 명단이 바뀌면 같은 배치 번호가 다른 종목 묶음을 뜻한다.

    그래서 run_id 를 **종목·구간**으로 건다. 같은 ``--history`` 를 다시 돌리면
    같은 칸이라 건너뛰고, 구간을 늘리면 새 칸이라 다시 받는다.
    """
    now = clock.now()
    today = now.astimezone(UTC).date()
    start = today - timedelta(days=365 * years)
    policy = publication_policy(store, MARKET, clock=clock)
    archive = RawArchive(root=store.root)

    # **이미 있는 구간은 다시 받지 않는다.** SPY·QQQ·DIA 는 전체 백필이
    # 이미 넣어 뒀다(2021-08~). 같은 세션을 다시 append 하면 run_id 만 다른
    # 같은 행이 창고에 두 벌 쌓이고, 거래대금 합계 같은 것이 조용히 두 배가
    # 된다. 그래서 종목마다 **창고에 없는 앞쪽**만 받는다.
    have = read_prices(
        store,
        as_of=now,
        market=str(MARKET),
        entity=[f"{MARKET}:{symbol}" for symbol in INDEX_PROXY_ETFS],
        lookback=(today - start).days + 5,
        columns=["entity_id", "valid_from"],
    )
    earliest: dict[str, date] = {}
    if not have.empty:
        for entity, group in have.groupby("entity_id"):
            earliest[_ticker(str(entity))] = group["valid_from"].min().date()

    print(f"US 대용 ETF 과거 {start} ~ {today} · {len(INDEX_PROXY_ETFS)}종목")
    total = 0
    for symbol, exchange in sorted(INDEX_PROXY_ETFS.items()):
        first = earliest.get(symbol)
        end = today if first is None else first - timedelta(days=1)
        if end < start:
            print(f"  {symbol} 이미 {first} 부터 창고에 있다 — 받을 것이 없다")
            continue
        run_id = f"bf-prices-US-etf-{symbol}-{start.isoformat()}-{end.isoformat()}"
        if store.ingest_run_recorded("prices", run_id):
            print(f"  {symbol} 이미 적재 (run_id={run_id})")
            continue
        if dry_run:
            print(f"  {symbol} {start} ~ {end} 받을 예정 (거래소 {exchange})")
            continue

        bars = source.daily_bars(symbol, exchange=exchange, start=start, end=end)
        rows, deferred = normalize_bars(
            bars, symbol=symbol, market=MARKET, observed_at_for=policy
        )
        if not rows:
            # 빈 것을 적재로 기록하면 나중에 데이터가 생겨도 영영 건너뛴다.
            print(f"  {symbol} 0행 — 거래소({exchange})가 틀렸을 수 있다", file=sys.stderr)
            continue
        archive.save(
            source.name,
            {symbol: bars},
            observed_at=now,
            ingest_run_id=run_id,
            label=f"ls_us-etf-{symbol}",
        )
        written = store.append("prices", rows, ingest_run_id=run_id)
        total += written
        print(f"  {symbol} rows={written:,} (미공표 대기 {len(deferred)})", flush=True)
    print(f"완료 — {total:,}행")
    return 0


def etf_fresh_enough(store: Store, clock: Clock) -> bool:
    """대용 ETF 가 **네 종목 모두** 최신 세션까지 들어왔나.

    전체 수집의 ``fresh_enough`` 를 못 쓴다. 그쪽은 "세션에 종목이 얼마나
    찼나" 로 판정해서, 4종목만 받은 실행은 어느 세션도 못 채운다. 여기서는
    반대로 **명단이 작다는 것을 알고** 한 종목이라도 밀리면 말한다.
    """
    now = clock.now()
    expected = latest_publishable(publication_policy(store, MARKET, clock=clock), now=now)
    frame = read_prices(
        store,
        as_of=now,
        market=str(MARKET),
        entity=[f"{MARKET}:{symbol}" for symbol in INDEX_PROXY_ETFS],
        lookback=SYMBOL_LOOKBACK_DAYS,
        columns=["entity_id", "valid_from", "close"],
    )
    latest: dict[str, date | None] = {symbol: None for symbol in INDEX_PROXY_ETFS}
    if not frame.empty:
        live = frame[frame["close"].astype(float) > 0]
        for entity, group in live.groupby("entity_id"):
            latest[_ticker(str(entity))] = group["valid_from"].max().date()

    stale = []
    for symbol in sorted(INDEX_PROXY_ETFS):
        newest = latest.get(symbol)
        mark = newest.isoformat() if newest else "없음"
        print(f"  {MARKET}:{symbol} 최신 {mark}")
        if expected is not None and (newest is None or newest < expected):
            stale.append(symbol)
    if expected is None:
        return True
    if stale:
        print(
            f"대용 ETF {'·'.join(stale)} 가 {expected} 를 못 받았다. "
            "브리핑의 미장 보완 줄이 낡은 채로 나간다.",
            file=sys.stderr,
        )
        return False
    return True


def _short(delta: timedelta) -> str:
    total = int(delta.total_seconds())
    return f"{total // 3600}시간 {total % 3600 // 60}분" if total >= 3600 else f"{total // 60}분"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument(
        "--sessions", type=int, default=3, help="최근 N 거래일을 받는다 (기본 3)"
    )
    parser.add_argument(
        "--source",
        choices=["store", "sec", "etf"],
        default="store",
        help=(
            "종목 명단 출처. store=창고에 이미 있는 종목(기본), "
            "sec=SEC 명단(신규 상장 포함), etf=미장 지수 대용 ETF 4종"
        ),
    )
    parser.add_argument(
        "--history",
        type=int,
        metavar="YEARS",
        help="--source etf 전용. 최근 N년 과거를 채운다(한 번만 돌리면 된다)",
    )
    parser.add_argument(
        "--top",
        type=int,
        help="최근 거래대금 상위 N종목만. **부분 수집이라 run_id 에 표지가 붙는다**",
    )
    parser.add_argument(
        "--symbols", help="쉼표로 구분한 티커. 소량 검증용 — 역시 부분 수집이다"
    )
    parser.add_argument("--batch-size", type=int)
    parser.add_argument("--dry-run", action="store_true", help="계획만 출력")
    args = parser.parse_args(argv)

    load_env()
    store = build_store(args.data_root)
    clock = LiveClock()
    now = clock.now()

    source = LsUsSource.from_env(clock=clock)
    if not source.usable():
        print("LS_US_APPKEY / LS_US_APPSECRET 이 없다.", file=sys.stderr)
        return 2

    # 창은 **거래일**로 센다. 달력일로 세면 연휴가 낀 주에 받는 세션이 줄고,
    # 그 줄어든 만큼이 영영 빈칸으로 남는다.
    today = now.astimezone(UTC).date()
    calendar = trading_days(MARKET, today - timedelta(days=args.sessions * 3 + 10), today)
    if not calendar:
        print("미장 거래일을 못 구했다.", file=sys.stderr)
        return 2
    window = calendar[-args.sessions :]
    start, end = window[0], today

    if args.history:
        if args.source != "etf":
            print("--history 는 --source etf 에서만 쓴다.", file=sys.stderr)
            return 2
        return etf_history(
            store, source, clock, years=args.history, dry_run=args.dry_run
        )

    exchanges = load_exchanges(store)
    scope = ""
    if args.symbols:
        symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
        # 몇 종목만 넣는 실행이 전체 실행과 같은 run_id 를 쓰면 그 세션이
        # "적재됨" 으로 잠겨 **나머지 6,600종목이 영영 안 들어온다.**
        #
        # 지문까지 붙이는 이유: ``probe`` 하나로 두면 어제 5종목으로 돌린 실행이
        # 오늘 다른 10종목 실행을 "이미 적재" 로 막는다. 명단이 다르면 다른
        # 작업이다.
        digest = hashlib.sha256(",".join(sorted(symbols)).encode()).hexdigest()[:8]
        scope = f"probe{digest}"
    elif args.source == "etf":
        # 지수 대용 ETF 4종. 명단이 코드에 박혀 있으므로 창고를 안 읽는다 —
        # 브리핑 직전에 도는 자리라 창고 조회 한 번도 아깝다.
        symbols = sorted(INDEX_PROXY_ETFS)
        exchanges.update(INDEX_PROXY_ETFS)
        scope = ETF_SCOPE
    else:
        if args.source == "sec":
            user_agent = os.environ.get(UA_ENV, "")
            if not user_agent:
                print(f"{UA_ENV} 가 없다. SEC 명단을 받지 못한다.", file=sys.stderr)
                return 2
            ranked, listed = sec_symbols(user_agent)
            # SEC 가 거래소를 주므로 캐시보다 이쪽이 정확하다.
            exchanges.update(listed)
        else:
            ranked = store_symbols(store, as_of=now)
        if not ranked:
            print(
                "미장 종목 명단이 비었다. 창고에 시세가 없으면 먼저 "
                "tools/backfill.py --market US --table prices 로 채워야 한다.",
                file=sys.stderr,
            )
            return 2
        if args.top:
            ranked = sorted(ranked, key=lambda item: item[1], reverse=True)[: args.top]
            scope = f"top{args.top}"
        # **배치 번호가 run_id 에 박히므로 명단 순서를 티커로 고정한다.**
        # 거래대금 순으로 두면 순위가 흔들릴 때마다 배치 구성이 바뀌어
        # 어제의 배치 000 과 오늘의 배치 000 이 다른 종목 묶음이 된다.
        symbols = sorted(ticker for ticker, _ in ranked)

    collector = UsIncrementalCollector(
        store=store,
        source=source,
        clock=clock,
        archive=RawArchive(root=store.root),
        policy=publication_policy(store, MARKET, clock=clock),
        market=MARKET,
        exchanges=exchanges,
        scope=scope,
        **({"batch_size": args.batch_size} if args.batch_size else {}),
    )

    groups = collector.batches(symbols)
    # 남은 배치 판정은 수집기와 **같은 규칙**이어야 한다 — 미공표 세션을
    # 여기서 세면 계획과 실제가 어긋난다.
    live = [day for day in window if collector.published(day)]
    todo = [
        (batch, group) for batch, group in groups
        if collector.pending_sessions(batch, live)
    ]
    print(
        f"US 일봉 증분 {window[0]} ~ {window[-1]} ({len(window)}세션) · "
        f"종목 {len(symbols)}개 / 배치 {len(groups)}개 (남은 {len(todo)})"
        + (f" · scope={scope}" if scope else "")
    )
    if args.dry_run:
        return 0

    started = time.monotonic()  # invariant-allow: wallclock
    done = 0
    total = sum(len(group) for _, group in todo)

    def tick(symbol: str, rows: int) -> None:
        nonlocal done
        done += 1
        if done % 200 or not total:
            return
        elapsed = time.monotonic() - started  # invariant-allow: wallclock
        left = timedelta(seconds=elapsed / done * (total - done))
        print(f"  [{done}/{total}] {symbol} rows={rows}  남은시간 ~{_short(left)}", flush=True)

    collector.on_symbol = tick

    rows = calls = deferred = missing = 0
    for batch, group in todo:
        result = collector.run_batch(batch, group, start=start, end=end)
        rows += result.rows
        calls += result.calls
        deferred += result.deferred_sessions
        missing += len(result.missing)
        print(
            f"배치 {result.unit} — rows={result.rows:,} 세션={len(result.sessions)} "
            f"(기적재 {len(result.already)}) 호출={result.calls} "
            f"미취급 {len(result.missing)}종목",
            flush=True,
        )
        # 배치마다 남긴다. 중간에 끊겨도 여기까지 알아낸 거래소는 지킨다.
        save_exchanges(store, collector.exchanges)

    elapsed = timedelta(seconds=time.monotonic() - started)  # invariant-allow: wallclock
    print(
        f"\n완료 — {rows:,}행 · 호출 {calls:,}회 · 미공표 대기 {deferred:,} · "
        f"미취급 {missing:,}종목 · 경과 {_short(elapsed)}"
    )

    # 부분 수집은 창고 신선도를 책임지지 않는다 — 상위 500종목만 받아 놓고
    # "최신" 이라고 말하면 나머지 6,100종목의 결손이 그 판정에 숨는다.
    if scope == ETF_SCOPE:
        # 명단이 고정된 4종목이라 **여기서는 판정할 수 있다.** 아침 브리핑이
        # 이 네 줄을 쓰므로, 밀린 것을 조용히 넘기면 안 된다.
        return 0 if etf_fresh_enough(store, clock) else 1
    if scope:
        print(f"부분 수집(scope={scope})이라 신선도 판정은 건너뛴다.")
        return 0
    return 0 if fresh_enough(store, clock) else 1


if __name__ == "__main__":
    raise SystemExit(main())
