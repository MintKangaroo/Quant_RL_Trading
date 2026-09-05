"""RL 세션 피처 캐시 굽기.

    uv run python tools/build_rl_cache.py --start 2024-09-01 --end 2026-08-14
    uv run python tools/build_rl_cache.py --start 2026-08-10 --end 2026-08-14 --market KR
    uv run python tools/build_rl_cache.py --start ... --end ... --report

**중단해도 된다.** 다시 같은 명령을 치면 이미 구운 세션은 건너뛰고 이어받는다
(`tools/backfill.py` 의 ``ingest_run_recorded`` 규약과 같은 정신이다: 이미
있는 것을 다시 만들지 않는다). 몇 시간짜리 작업이고 이 기계는 램을 다른
작업과 나눠 쓰므로, **중단은 예외가 아니라 기본값이다.**

세션 하나를 다 굽고 나서야 파일이 생긴다(`cache.write` 가 임시 파일에 쓰고
``os.replace`` 로 갈아 끼운다). Ctrl-C 로 죽여도 반쯤 쓰인 parquet 이 "이미
구웠다" 로 읽히는 일이 없다.

## 무엇이 캐시에 들어가는지는 여기서 정하지 않는다

경계는 `quant_rl_trading/allocator/cache.py` 의 모듈 독스트링에 있다. 이 파일은
그 함수를 세션마다 부르고 진행률을 찍는 것뿐이다 — 굽는 쪽에 계산이 한 줄이라도
따로 있으면 캐시 경로와 창고 경로가 갈라진다.

## 언제 구워야 하나

`analyst_weights` 이력(롤링 IC)이 채워진 뒤여야 뜻이 있다. 이력이 비어 있으면
후보가 0건으로 나오고, 그 0건이 그대로 구워져 학습이 "살 게 없는 세계" 를
배운다 — 예외는 안 난다. `--report` 로 후보 수를 먼저 보라.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import deque
from datetime import date, datetime, timedelta
from datetime import time as clock_time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.allocator import cache as cache_module  # noqa: E402
from quant_rl_trading.backtest import loop as loop_module  # noqa: E402
from quant_rl_trading.collectors.backfill import eta  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market, trading_days  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.store import ConfigNotFound, Store  # noqa: E402

SEOUL = "Asia/Seoul"

#: 이 조각만 시딩한다. 전체 파일을 시딩하면 다른 에이전트의 미완성 편집까지
#: 창고에 확정된다 (`config/rl_cache.yaml` 의 머리말).
CONFIG_FRAGMENT = REPO_ROOT / "config" / "rl_cache.yaml"


def session_moment(store: Store, day: date, *, session_time: clock_time | None) -> datetime:
    """그 세션의 기준 시각. `allocator/env.py` 의 ``_moment`` 와 같은 규칙이다.

    다르면 캐시가 env 와 **다른 as_of** 로 구워지고, 그러면 캐시 경로와 창고
    경로가 다른 값을 낸다 — 이 도구가 낼 수 있는 가장 조용한 사고다.

    시각 성분은 세션마다 같으므로 한 번만 구해서 재사용한다(공표정책 조회가
    세션 수만큼 반복되는 것을 막는다).
    """
    from zoneinfo import ZoneInfo

    zone = ZoneInfo(SEOUL)
    if session_time is None:
        probe = datetime.combine(day, loop_module.DEFAULT_SNAPSHOT_TIME, tzinfo=zone)
        moment = loop_module.snapshot_moment(store, day, as_of=probe)
        # **서울 시각으로 바꿔서 시각 성분을 뽑는다** — UTC 의 시:분을 그대로
        # 서울에 다시 붙이면 세션이 아홉 시간 앞으로 당겨진다 (env 의 같은 자리).
        session_time = moment.astimezone(zone).timetz()
    return datetime.combine(
        day, clock_time(session_time.hour, session_time.minute), tzinfo=zone
    )


def already_built(
    store: Store, root: Path, market: str, day: date, as_of: datetime
) -> bool:
    """이 세션이 **지금 창고·지금 설정으로** 이미 구워져 있는가.

    파일이 있는 것만으로는 부족하다. 다른 창고를 가리킨 채, 혹은 임계치가
    바뀐 뒤에 남은 파일은 있어도 못 쓴다 — 그걸 "이미 구웠다" 로 세면 낡은
    캐시가 영영 안 갱신된다.
    """
    path = cache_module.cache_path(root, market, day)
    if not path.exists():
        return False
    try:
        cached = cache_module.read(path)
        cache_module.verify(cached, store=store, market=market, session=day, as_of=as_of)
    except (cache_module.CacheStampMismatch, OSError, KeyError, ValueError):
        return False
    return True


def carry_entities(history: deque[tuple[date, tuple[str, ...]]]) -> list[str]:
    """최근 세션들의 후보를 합친 것. **보유 종목이 여기서 걸린다.**

    RL 은 슬롯에 오른 종목만 살 수 있고 슬롯은 (보유 + 후보)다. 그래서 오늘
    보유 중인 종목은 언제인가 후보였다 — 최근 N 세션을 모으면 대부분 잡힌다.
    못 잡힌 것은 env 가 창고에서 그 종목만 다시 읽는다(값은 같고 느릴 뿐이다).
    """
    seen: dict[str, None] = {}
    for _day, entities in history:
        for entity in entities:
            seen.setdefault(entity, None)
    return list(seen)


def load_history(
    store: Store, root: Path, market: str, days: list[date], *, carry: int
) -> deque[tuple[date, tuple[str, ...]]]:
    """구간 앞쪽에 이미 구워진 세션이 있으면 그 후보를 이어받는다.

    없으면 빈 채로 시작한다 — 구간 초반 몇 세션은 이월 종목이 얇아 학습이
    창고로 되돌아가는 일이 잦지만, **값은 같다.**
    """
    history: deque[tuple[date, tuple[str, ...]]] = deque(maxlen=carry)
    for day in days:
        path = cache_module.cache_path(root, market, day)
        if not path.exists():
            continue
        try:
            cached = cache_module.read(path)
        except (OSError, KeyError, ValueError):
            continue
        history.append((day, tuple(entity for entity, _score in cached.selection.candidates)))
    return history


def run(
    store: Store,
    *,
    market: str,
    start: date,
    end: date,
    root: Path,
    rebuild: bool,
    limit: int | None,
) -> int:
    equity = float(store.config("allocator.env.cache_equity", as_of=_probe(store)))
    carry = int(store.config("allocator.env.cache_carry_sessions", as_of=_probe(store)))

    sessions = trading_days(Market(market), start, end)
    if not sessions:
        print(f"{start}~{end} 에 {market} 거래일이 없다.", file=sys.stderr)
        return 2

    # 이월 후보는 **구간 앞쪽**에서도 온다. 어제까지 구워 뒀는데 오늘부터
    # 다시 굽는 경우가 흔하고, 그때 이월을 버리면 초반이 통째로 느려진다.
    warmup_start = start - timedelta(days=int(carry * 1.8) + 10)
    warmup = [day for day in trading_days(Market(market), warmup_start, start) if day < start]
    session_time = None
    history = load_history(store, root, market, warmup[-carry:], carry=carry)

    pending: list[date] = []
    for day in sessions:
        moment = session_moment(store, day, session_time=session_time)
        session_time = moment.timetz()
        if not rebuild and already_built(store, root, market, day, moment):
            history.append((day, _cached_candidates(root, market, day)))
            continue
        pending.append(day)
    if limit is not None:
        pending = pending[:limit]

    print(
        f"{market} 세션 피처 캐시 — {start}~{end} 거래일 {len(sessions)}개 중 "
        f"굽는다 {len(pending)}개 (이미 구움 {len(sessions) - len(pending)}), "
        f"자본 {equity:,.0f}원 · 이월 {carry}세션",
        flush=True,
    )
    if not pending:
        print("할 일이 없다.")
        return 0

    reader = cache_module.SessionReader(store, market)
    started = time.monotonic()  # invariant-allow: wallclock
    failures: list[tuple[date, str]] = []
    baked = 0
    total_rows = 0
    total_bytes = 0

    for index, day in enumerate(pending, start=1):
        moment = session_moment(store, day, session_time=session_time)
        session_time = moment.timetz()
        try:
            capped = cache_module.price_capped(
                store, as_of=moment, market=market, equity=equity
            )
            if capped:
                # 굽는 자본에서 1주 가격 상한에 걸린 종목이 있으면 그 세션의
                # 후보는 자본에 의존한다 — 캐시가 자본과 무관하다는 전제가
                # 깨진다. 굽지 않고 넘어간다(env 는 창고로 간다).
                raise ValueError(
                    f"1주 가격 상한에 {capped}종목이 걸렸다. 이 세션은 자본에 "
                    f"의존하므로 굽지 않는다 — allocator.env.cache_equity 를 올려라"
                )
            cache = cache_module.build_session(
                reader,
                as_of=moment,
                session=day,
                equity=equity,
                extra_entities=carry_entities(history),
            )
            path = cache_module.write(cache, cache_module.cache_path(root, market, day))
            history.append((day, tuple(entity for entity, _score in cache.selection.candidates)))
            baked += 1
            total_rows += len(cache.covered)
            total_bytes += path.stat().st_size
            note = (
                f"후보={len(cache.selection.candidates)} 행={len(cache.covered)} "
                f"레짐={cache.scalars.regime_state}"
            )
            status = "ok"
        except Exception as error:
            failures.append((day, str(error)))
            note = str(error)[:90]
            status = "FAIL"

        elapsed = timedelta(seconds=time.monotonic() - started)  # invariant-allow: wallclock
        remaining = eta(index, len(pending), elapsed)
        tail = f"  남은시간 ~{_short(remaining)}" if remaining else ""
        print(f"[{index}/{len(pending)}] {day} {status}  {note}{tail}", flush=True)

    elapsed = timedelta(seconds=time.monotonic() - started)  # invariant-allow: wallclock
    per_session = elapsed / max(1, baked)
    print(
        f"\n완료 ({_short(elapsed)}) — 구움 {baked}세션 · 행 {total_rows:,} · "
        f"{total_bytes / 1e6:.1f}MB · 세션당 {per_session.total_seconds():.1f}초 · "
        f"{(total_bytes / max(1, baked)) / 1024:.0f}KB, 실패 {len(failures)}건"
    )
    for day, message in failures[:20]:
        print(f"  실패 {day}: {message}")
    return 1 if failures else 0


def report(store: Store, *, market: str, start: date, end: date, root: Path) -> int:
    """구운 것을 세어 본다. **후보 0건이 몇 세션인지가 핵심 지표다.**"""
    sessions = trading_days(Market(market), start, end)
    built = empty = 0
    rows = 0
    size = 0
    for day in sessions:
        path = cache_module.cache_path(root, market, day)
        if not path.exists():
            continue
        cached = cache_module.read(path)
        built += 1
        rows += len(cached.covered)
        size += path.stat().st_size
        if not cached.selection.candidates:
            empty += 1
    print(
        f"{market} {start}~{end} — 거래일 {len(sessions)} · 구움 {built} · "
        f"후보 0건 {empty} · 행 {rows:,} · {size / 1e6:.1f}MB"
    )
    if empty:
        print(
            f"  ⚠️ {empty}세션이 후보 0건이다. IC 이력(analyst_weights)이 비어 있으면 "
            "이렇게 나온다 — 그 구간으로 학습하면 '살 게 없는 세계' 를 배운다"
        )
    return 0


def _cached_candidates(root: Path, market: str, day: date) -> tuple[str, ...]:
    cached = cache_module.read(cache_module.cache_path(root, market, day))
    return tuple(entity for entity, _score in cached.selection.candidates)


def _probe(store: Store) -> datetime:
    """설정을 읽을 시각. 굽는 자본·이월 세션 수는 **운영 파라미터**라 세션마다
    다를 이유가 없다. 최신값 하나를 본다.

    시각은 `LiveClock` 에서 온다 — 도구도 벽시계를 직접 부르지 않는다(불변식 2).
    """
    return LiveClock().now()


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--market", default="KR")
    parser.add_argument("--root", type=Path, default=None, help="창고 경로 (기본: data/)")
    parser.add_argument(
        "--cache-root", type=Path, default=None, help="캐시 경로 (기본: <창고>/rl_cache)"
    )
    parser.add_argument(
        "--rebuild", action="store_true", help="이미 구운 세션도 다시 굽는다"
    )
    parser.add_argument("--limit", type=int, default=None, help="이번에 굽는 세션 수 상한")
    parser.add_argument("--report", action="store_true", help="굽지 않고 세어만 본다")
    args = parser.parse_args(argv)

    store = Store(root=args.root) if args.root is not None else Store()
    try:
        store.config("allocator.env.cache_equity", as_of=_probe(store))
    except ConfigNotFound:
        # **조각만 심는다.** 전체 파일을 심으면 다른 에이전트의 미완성 편집까지
        # 확정된다 (`config/rl_cache.yaml`).
        store.seed_config_defaults(path=CONFIG_FRAGMENT)

    root = args.cache_root or cache_module.default_cache_root(store)
    if args.report:
        return report(store, market=args.market, start=args.start, end=args.end, root=root)
    return run(
        store,
        market=args.market,
        start=args.start,
        end=args.end,
        root=root,
        rebuild=args.rebuild,
        limit=args.limit,
    )


if __name__ == "__main__":
    raise SystemExit(main())
