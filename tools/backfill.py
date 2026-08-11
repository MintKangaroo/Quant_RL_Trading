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
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lattice.collectors.backfill import (  # noqa: E402
    UNIVERSE,
    Backfiller,
    BackfillReport,
    ProgressLog,
    eta,
)
from lattice.collectors.krx_source import KrxSource, credentials_present  # noqa: E402
from lattice.collectors.market_hours import Market, trading_days  # noqa: E402
from lattice.collectors.publication import publication_policy  # noqa: E402
from lattice.collectors.raw import RawArchive  # noqa: E402
from lattice.dashboard.services import data_quality as dq  # noqa: E402
from lattice.replay.clock import Clock, LiveClock  # noqa: E402
from lattice.store import ConfigNotFound, Store  # noqa: E402

ENV_FILE = REPO_ROOT / ".env"


def load_env(path: Path = ENV_FILE) -> None:
    """``.env`` 를 환경변수로. 이미 설정된 값은 덮지 않는다.

    의존성을 하나 더 늘리지 않으려고 직접 읽는다. 셸에서 export 한 값이
    파일보다 우선한다 — 일회성 덮어쓰기가 가능해야 한다.
    """
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


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
    backfiller = Backfiller(
        store=store,
        source=source,
        clock=clock,
        archive=RawArchive(root=store.root),
        policy=publication_policy(store, market, clock=clock),
        market=market,
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
        status = "SKIP" if result.skipped else ("FAIL" if result.error else "ok")
        tail = f"  남은시간 ~{_short(remaining)}" if remaining else ""
        print(
            f"[{index}/{len(pending)}] {day} {status}  "
            f"prices={result.prices} universe={result.universe}{tail}"
            + (f"  {result.error}" if result.error else "")
        )

    print(
        f"\n완료 — 세션 {report.sessions}개 (건너뜀 {report.skipped}), "
        f"prices {report.prices:,}행, universe {report.universe:,}행, "
        f"실패 {len(report.failures)}건"
    )
    for day, message in report.failures[:20]:
        print(f"  실패 {day}: {message}")
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
    (``lattice/dashboard/services/data_quality.py``). 두 벌로 두면 같은 데이터에서
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
    parser.add_argument("--years", type=int, default=5)
    parser.add_argument("--symbols", type=int, help="시험 실행: 이 개수만큼만 백필")
    parser.add_argument("--sessions", type=int, help="최근 N 거래일만")
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

    if market is not Market.KR:
        print(
            f"{market} 백필 소스가 아직 없다. LS 해외주식 TR 필드맵이 미검증이고 "
            "(postmortem-ls.md §6-6) US appkey 도 없다.",
            file=sys.stderr,
        )
        return 2

    return run_backfill(
        store,
        clock,
        market=market,
        years=args.years,
        symbols=args.symbols,
        sessions_limit=args.sessions,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())
