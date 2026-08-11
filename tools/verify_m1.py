"""M1 완료 기준 검증기.

    uv run python tools/verify_m1.py

``docs/milestones.md`` 의 M1 완료 기준 5개를 하나씩 확인하고, 각 항목마다
근거가 된 명령과 숫자를 출력한다. 하나라도 실패하면 종료코드가 0이 아니다.

핵심은 두 번째 기준이다 — **임의 과거 날짜 100개 리플레이가 전부 결정론적**.
합성 데이터가 아니라 실제로 백필된 창고 위에서 돌린다. 단위 테스트의 결정론
검증은 10행짜리 fixture 위에서 도는 것이라, 338만 행에서도 같은지는 별개
문제다. 여기서 깨지면 백테스트 결과를 두 번 재현할 수 없고, 재현되지 않는
성적표로는 무엇이 통했는지 알 수 없다.
"""

from __future__ import annotations

import argparse
import random
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lattice.collectors.market_hours import Market, trading_days  # noqa: E402
from lattice.dashboard.services import data_quality as dq  # noqa: E402
from lattice.replay import FillParams, MarketState, ReplayClock, run_session  # noqa: E402
from lattice.replay.clock import LiveClock  # noqa: E402
from lattice.schemas.order import Order, Side  # noqa: E402
from lattice.store import Store  # noqa: E402
from tools.backfill import REPORT_SPAN_DAYS, build_store, load_env  # noqa: E402

#: 리플레이 표본 수. 완료 기준이 100개다.
SAMPLE_SESSIONS = 100

#: 표본 추출 시드. 검증 자체가 재현 가능해야 한다.
SEED = 20260811

#: 리플레이가 보는 과거 창.
LOOKBACK_DAYS = 20


@dataclass
class Check:
    name: str
    passed: bool
    evidence: list[str]

    def render(self) -> str:
        mark = "PASS" if self.passed else "FAIL"
        lines = [f"[{mark}] {self.name}"]
        lines += [f"       {line}" for line in self.evidence]
        return "\n".join(lines)


# -----------------------------------------------------------------------------
# 리플레이용 최소 전략 — M3 에서 Selector→Allocator→Executor 가 대신한다.
# -----------------------------------------------------------------------------


def top_by_close(observation: pd.DataFrame, as_of: datetime) -> list[Order]:
    """거래대금 상위 5종목을 산다.

    무엇을 사는지는 중요하지 않다. **같은 입력에서 같은 주문이 나오는지**만
    본다. 다만 빈 주문을 내는 전략으로는 결정론을 증명할 수 없어서
    (아무것도 안 하는 구현도 통과한다) 실제로 주문이 나오게 만든다.
    """
    if observation.empty:
        return []
    latest = (
        observation.sort_values(["entity_id", "valid_from"]).groupby("entity_id").last()
    )
    ranked = latest.sort_values(["value", "entity_id"], ascending=[False, True]).head(5)
    return [
        Order(entity_id=str(entity), side=Side.BUY, quantity=10, reason="top-value")
        for entity in ranked.index
    ]


def build_state(observation: pd.DataFrame, entity_id: str) -> MarketState:
    rows = observation[observation["entity_id"] == entity_id].sort_values("valid_from")
    last = rows.iloc[-1]
    return MarketState(
        entity_id=entity_id,
        close=float(last["close"]),
        volume=float(last["volume"]),
        adv=float(rows["volume"].mean()),
        volatility=0.02,
        lot_size=1,
        tick_size=1.0,
    )


# -----------------------------------------------------------------------------
# 기준 1 — 백필
# -----------------------------------------------------------------------------


def check_backfill(store: Store, as_of: datetime, market: Market) -> Check:
    coverage = dq.collect_coverage(store, as_of=as_of, lookback=REPORT_SPAN_DAYS)
    if not coverage.rows:
        return Check("과거 5년치 전종목 백필", False, ["prices 가 비어 있다"])

    sessions = coverage.sessions
    expected = trading_days(market, sessions[0], sessions[-1])
    ratio = len(sessions) / len(expected)

    delisted, listed = _universe_state(store, as_of)
    years = (sessions[-1] - sessions[0]).days / 365.25

    return Check(
        "과거 5년치 전종목 curated 백필 (상장폐지 종목 포함)",
        ratio >= 1.0 and years >= 4.9 and delisted > 0,
        [
            f"구간 {sessions[0]} ~ {sessions[-1]} ({years:.1f}년)",
            f"거래일 {len(sessions)} / 기대 {len(expected)} = {ratio:.1%}",
            f"prices {coverage.total:,}행, 종목(누적) {len(coverage.entities):,}개",
            f"현재 상장 {listed:,} / 상장폐지 감지 {delisted:,}"
            + ("" if delisted else "  ← 0이면 생존편향 미제거"),
        ],
    )


def _universe_state(store: Store, as_of: datetime) -> tuple[int, int]:
    state: dict[str, bool] = {}
    seen: dict[str, date] = {}
    for window in dq.iter_windows(
        store, "universe", as_of=as_of, lookback=REPORT_SPAN_DAYS
    ):
        if window.empty:
            continue
        for row in window.sort_values("valid_from").to_dict(orient="records"):
            entity = str(row["entity_id"])
            session = row["valid_from"].date()
            if entity not in seen or session >= seen[entity]:
                seen[entity] = session
                state[entity] = bool(row["is_listed"])
    delisted = sum(1 for listed in state.values() if not listed)
    return delisted, len(state) - delisted


# -----------------------------------------------------------------------------
# 기준 2 — 임의 과거 날짜 100개 리플레이가 전부 결정론적
# -----------------------------------------------------------------------------


def check_determinism(
    store: Store, as_of: datetime, market: Market, *, samples: int, verbose: bool
) -> Check:
    coverage = dq.collect_coverage(store, as_of=as_of, lookback=REPORT_SPAN_DAYS)
    sessions = coverage.sessions
    if len(sessions) < samples:
        return Check(
            f"임의 과거 날짜 {samples}개 리플레이 → 전부 결정론적",
            False,
            [f"세션이 {len(sessions)}개뿐이다"],
        )

    # 창을 채울 수 없는 초반 세션은 제외한다. 관측이 비면 전략이 빈 주문을 내고,
    # 빈 주문끼리 같은 것은 결정론의 증거가 아니다.
    eligible = sessions[LOOKBACK_DAYS:]
    picked = sorted(random.Random(SEED).sample(eligible, samples))

    params = FillParams.from_store(store, as_of=as_of)
    mismatches: list[str] = []
    empty: list[str] = []
    digests: set[str] = set()

    for index, day in enumerate(picked, start=1):
        moment = datetime(day.year, day.month, day.day, 23, tzinfo=UTC)
        first = _replay(store, moment, f"m1-{day:%Y%m%d}-a", params)
        second = _replay(store, moment, f"m1-{day:%Y%m%d}-b", params)

        if first.serialized().encode("utf-8") != second.serialized().encode("utf-8"):
            mismatches.append(day.isoformat())
        if not first.orders:
            empty.append(day.isoformat())
        digests.add(first.digest())

        if verbose and index % 20 == 0:
            print(f"       … {index}/{samples}", flush=True)

    return Check(
        f"임의 과거 날짜 {samples}개 리플레이 → 전부 결정론적",
        not mismatches and not empty,
        [
            f"표본 {samples}개 (시드 {SEED}), 각 날짜를 두 번 실행해 바이트 비교",
            f"구간 {picked[0]} ~ {picked[-1]}",
            f"불일치 {len(mismatches)}건" + (f": {mismatches[:5]}" if mismatches else ""),
            f"빈 주문 {len(empty)}건" + (f": {empty[:5]}" if empty else ""),
            # 모든 날짜가 같은 결과를 내면 그건 결정론이 아니라 고장이다.
            f"서로 다른 결과 {len(digests)}종 / {samples}일",
        ],
    )


def _replay(store: Store, moment: datetime, run_id: str, params: FillParams):  # type: ignore[no-untyped-def]
    return run_session(
        store=store,
        clock=ReplayClock(moment),
        run_id=run_id,
        strategy=top_by_close,
        market_state=build_state,
        params=params,
        lookback=LOOKBACK_DAYS,
        # 벽시계까지 고정한다. 이벤트 로그의 payload_hash 도 같아야 한다.
        wall_clock=ReplayClock(datetime(2026, 1, 1, tzinfo=UTC)),
    )


# -----------------------------------------------------------------------------
# 기준 3·4 — 테스트와 정적 가드
# -----------------------------------------------------------------------------


def _run(command: list[str]) -> tuple[int, str]:
    finished = subprocess.run(command, cwd=REPO_ROOT, capture_output=True, text=True)
    return finished.returncode, (finished.stdout + finished.stderr).strip()


def check_contract_tests() -> Check:
    code, output = _run(["uv", "run", "pytest", "tests/invariants/", "-q"])
    tail = [line for line in output.splitlines() if "passed" in line or "failed" in line]
    return Check(
        "결정론 / 미래 훔쳐보기 / 생존편향 / 정정공시 테스트 통과",
        code == 0,
        ["$ uv run pytest tests/invariants/ -q", *(tail or output.splitlines()[-3:])],
    )


def check_static_guards() -> Check:
    code, output = _run(["uv", "run", "python", "tools/invariant_guard.py"])
    return Check(
        "datetime.now() 직접 호출 0건, 데이터 직접 조회 0건 (CI 검사)",
        code == 0,
        ["$ uv run python tools/invariant_guard.py", *output.splitlines()[-3:]],
    )


# -----------------------------------------------------------------------------
# 기준 5 — 지연 실측
# -----------------------------------------------------------------------------


def check_latency(store: Store, as_of: datetime) -> Check:
    stats = dq.latency_percentiles(store, as_of=as_of, lookback=REPORT_SPAN_DAYS)
    overall = stats["overall"]
    has_samples = stats["samples"] > 0 and overall.get("p90") is not None

    evidence = [
        f"표본 {stats['samples']:,}건, 소스 {stats.get('sources', [])}",
        f"전체 p50 {overall.get('p50', 0):.1f}ms / p90 {overall.get('p90', 0):.1f}ms",
    ]
    evidence += [
        f"  {row['stage']:<10} p50 {row['p50']:>9.1f}ms  p90 {row['p90']:>9.1f}ms"
        f"  표본 {row['samples']:,}"
        for row in stats["stages"]
    ]
    evidence.append("대시보드: GET /api/data-quality/latency (as_of 필수)")
    return Check("지연 실측 수집 시작 (p50/p90 대시보드 표시)", has_samples, evidence)


# -----------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=SAMPLE_SESSIONS)
    parser.add_argument("--market", default="KR", choices=[m.value for m in Market])
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    load_env()
    market = Market(args.market)
    store = build_store(args.data_root)
    as_of = LiveClock().now()

    print(f"M1 완료 기준 검증 — as_of {as_of.isoformat()}, 창고 {store.root}\n")

    checks = [
        check_backfill(store, as_of, market),
        check_determinism(
            store, as_of, market, samples=args.samples, verbose=not args.quiet
        ),
        check_contract_tests(),
        check_static_guards(),
        check_latency(store, as_of),
    ]

    for check in checks:
        print(check.render())
        print()

    failed = [check for check in checks if not check.passed]
    if failed:
        print(f"실패 {len(failed)}건 — M2 로 넘어가지 않는다.")
        for check in failed:
            print(f"  - {check.name}")
        return 1
    print(f"M1 완료 기준 {len(checks)}개 전부 통과.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
