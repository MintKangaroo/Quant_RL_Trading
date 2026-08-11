"""Analyst IC 측정 — 실제 창고 위에서.

    uv run python tools/measure_ic.py --analyst chart risk --sessions 300

세션마다 Analyst 를 그 시점 ``as_of`` 로 돌려 점수를 만들고, purged K-fold +
embargo 로 OOS IC 를 잰다. 결과는 ``analyst_weights`` 에 적재된다 — **가중치는
코드가 아니라 측정 결과에서만 나온다.**

읽는 법:

- **IC 0.03~0.08**: 정상 범위. 통과면 가중치를 받는다
- **IC 0.15 이상**: 축하할 일이 아니라 **누수를 찾을 일이다.** 이 규모의 알파는
  일별 횡단면에서 나오지 않는다
- **IC 음수**: 부호를 뒤집고 싶어지지만, 그건 표본에 맞춰 사후에 고르는 것이라
  다음 구간에서 사라진다. 피처 설계로 돌아가는 게 옳다
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lattice.analysts import ic  # noqa: E402
from lattice.analysts.base import Analyst, to_scores_frame  # noqa: E402
from lattice.analysts.chart import ChartAnalyst  # noqa: E402
from lattice.analysts.risk import RiskAnalyst  # noqa: E402
from lattice.collectors.market_hours import Market, trading_days  # noqa: E402
from lattice.collectors.publication import publication_policy  # noqa: E402
from lattice.replay.clock import LiveClock, ReplayClock  # noqa: E402
from lattice.store import Store  # noqa: E402
from tools.backfill import build_store, load_env  # noqa: E402

ANALYSTS: dict[str, type[Analyst]] = {
    "chart": ChartAnalyst,
    "risk": RiskAnalyst,
}

#: 라벨을 만들 때 훑는 구간. 측정 구간보다 넉넉해야 앞뒤가 잘리지 않는다.
TARGET_SPAN_DAYS = 365 * 6


def score_sessions(
    analyst: Analyst,
    store: Store,
    sessions: list[date],
    market: Market,
    *,
    verbose: bool,
) -> pd.DataFrame:
    """세션마다 그 시점 as_of 로 Analyst 를 돌린다.

    as_of 는 **그 세션의 공표 시각**이다. 자정으로 두면 그날 종가를 못 보고,
    다음날로 두면 하루를 버린다.
    """
    policy = publication_policy(store, market, clock=LiveClock())
    frames: list[pd.DataFrame] = []
    started = time.monotonic()  # invariant-allow: wallclock

    for index, session in enumerate(sessions, start=1):
        as_of = policy.for_session(session)
        analyst.clock = ReplayClock(as_of)
        signals = analyst.run(as_of)
        if signals:
            frames.append(to_scores_frame(signals).assign(session=session))
        if verbose and index % 25 == 0:
            elapsed = time.monotonic() - started  # invariant-allow: wallclock
            rate = elapsed / index
            print(
                f"       … {index}/{len(sessions)} "
                f"(남은시간 ~{timedelta(seconds=int(rate * (len(sessions) - index)))})",
                flush=True,
            )

    if not frames:
        return pd.DataFrame(columns=["entity_id", "session", "score"])
    return pd.concat(frames, ignore_index=True)


def measure(
    name: str, store: Store, *, market: Market, sessions: int, verbose: bool
) -> ic.ICResult:
    clock = LiveClock()
    as_of = clock.now()
    threshold, min_days = ic.thresholds(store, as_of=as_of)

    targets = ic.build_targets(store, as_of=as_of, lookback=TARGET_SPAN_DAYS)
    if targets.empty:
        raise SystemExit("타깃이 비었다. prices 백필이 되어 있는지 확인할 것.")

    # 타깃이 존재하는 구간에서만 잰다. 라벨이 없는 최근 며칠은 자연히 빠진다.
    available = sorted(targets["session"].unique())
    window = available[-sessions:]
    calendar = [day for day in trading_days(market, window[0], window[-1]) if day in set(window)]

    analyst = ANALYSTS[name](store, clock, market=market)
    scores = score_sessions(analyst, store, calendar, market, verbose=verbose)

    return ic.evaluate(
        scores,
        targets,
        analyst=analyst.name,
        analyst_version=analyst.version,
        market=str(market),
        threshold=threshold,
        min_sample_days=min_days,
    )


def render(result: ic.ICResult) -> str:
    verdict = "통과" if result.passed else "미통과"
    lines = [
        f"[{verdict}] {result.analyst} ({result.analyst_version}) · {result.market}",
        f"    IC          {result.ic:+.4f}   (합격선 {result.threshold})",
        f"    일별 표준편차 {result.ic_std:.4f}",
        f"    표본        {result.sample_days}일 / {result.sample_rows:,}행 "
        f"(하한 {result.min_sample_days}일)",
        f"    가중치      {result.weight}",
    ]
    if result.fold_ics:
        folds = "  ".join(f"{value:+.3f}" for value in result.fold_ics)
        lines.append(f"    폴드별 IC   {folds}")
    if result.ic > 0.15:
        lines += [
            "    ⚠️ IC 0.15 초과. 이 규모의 알파는 일별 횡단면에서 나오지 않는다.",
            "       축하할 일이 아니라 누수를 찾을 일이다.",
        ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analyst", nargs="+", default=sorted(ANALYSTS), choices=sorted(ANALYSTS))
    parser.add_argument("--sessions", type=int, default=300, help="측정할 최근 거래일 수")
    parser.add_argument("--market", default="KR", choices=[m.value for m in Market])
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--save", action="store_true", help="analyst_weights 에 적재")
    args = parser.parse_args(argv)

    load_env()
    store = build_store(args.data_root)
    market = Market(args.market)
    clock = LiveClock()

    results = []
    for name in args.analyst:
        print(f"\n=== {name} ===", flush=True)
        result = measure(
            name, store, market=market, sessions=args.sessions, verbose=not args.quiet
        )
        print(render(result))
        results.append(result)

    if args.save:
        now = clock.now()
        rows = [
            result.row(as_of=now, observed_at=now, source="ic-measure")
            for result in results
        ]
        run_id = f"ic-{market}-{now:%Y%m%dT%H%M%S}"
        written = store.append("analyst_weights", rows, ingest_run_id=run_id)
        print(f"\nanalyst_weights 적재: {written}행")

    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
