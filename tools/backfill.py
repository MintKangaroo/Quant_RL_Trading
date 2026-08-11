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
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

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
from lattice.collectors.ls_client import LSClient, LSCredentials  # noqa: E402
from lattice.collectors.ls_flow import LSFlowBackfiller, LSFlowSource  # noqa: E402
from lattice.collectors.market_hours import Market, trading_days  # noqa: E402
from lattice.collectors.panels import PANELS, PanelBackfiller  # noqa: E402
from lattice.collectors.panels import SHORTING as SHORTING  # noqa: E402
from lattice.collectors.publication import publication_policy  # noqa: E402
from lattice.collectors.raw import RawArchive  # noqa: E402
from lattice.dashboard.services import data_quality as dq  # noqa: E402
from lattice.replay.clock import Clock, LiveClock  # noqa: E402
from lattice.store import ConfigNotFound, Store  # noqa: E402

#: 수급은 종목 축이라 패널과 실행 경로가 다르다.
FLOW_LS = "flows-ls"

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

    if table is not None:
        panel = PANELS[table]
        if panel.table == SHORTING:
            # 실제 지연은 설정에서 온다. 코드의 기본값을 믿고 넘어가면
            # 언젠가 설정만 바꾸고 관측시각은 그대로인 상태가 된다 (불변식 10).
            panel = replace(
                panel, lag_days=int(store.config("backfill.shorting_lag_days", as_of=now))
            )
        backfiller: Backfiller | PanelBackfiller = PanelBackfiller(
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
    for day, message in report.failures[:20]:
        print(f"  실패 {day}: {message}")
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

    prices = store.get("prices", as_of=now, lookback=90)
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
        progress.record(_FlowRecord(symbol, result), at=clock.now())

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


@dataclass(frozen=True)
class _FlowRecord:
    """진행 로그가 기대하는 모양으로 맞춰 준다 (day/counts/skipped/error)."""

    symbol: str
    result: Any

    @property
    def day(self) -> Any:
        return self.symbol

    @property
    def counts(self) -> dict[str, int]:
        return dict(self.result.counts)

    @property
    def skipped(self) -> bool:
        return bool(self.result.skipped)

    @property
    def error(self) -> str | None:
        message = self.result.error
        return None if message is None else str(message)


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
    # 기본값은 store.config 의 backfill.years 다. 여기 숫자를 따로 들면
    # 두 값이 갈라져도 아무도 모른다 (불변식 10).
    parser.add_argument("--years", type=int)
    parser.add_argument("--symbols", type=int, help="시험 실행: 이 개수만큼만 백필")
    parser.add_argument("--sessions", type=int, help="최근 N 거래일만")
    parser.add_argument(
        "--table",
        choices=[*sorted(PANELS), FLOW_LS],
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

    if args.table == FLOW_LS:
        return run_flow_backfill(
            store, clock, market=market,
            years=args.years or int(store.config("backfill.years", as_of=clock.now())),
            limit=args.symbols,
        )

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
        years=args.years or int(store.config("backfill.years", as_of=clock.now())),
        symbols=args.symbols,
        sessions_limit=args.sessions,
        dry_run=args.dry_run,
        table=args.table,
    )


if __name__ == "__main__":
    raise SystemExit(main())
