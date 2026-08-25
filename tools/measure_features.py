"""피처별 IC — **어디를 고칠지 정하기 위한 도구.**

    uv run python tools/measure_features.py --analyst chart --sessions 300

Analyst 단위 IC 는 합격/불합격만 말해 준다. 미달일 때 그 다음 질문은 언제나
같다 — **어느 피처가 신호이고 어느 피처가 잡음인가.** 그걸 모르는 채로
가중치를 손보는 것은 추측이다.

여기서는 피처 하나를 그대로 점수로 놓고 잰다. Analyst IC 와 같은 검증기
(purged K-fold + embargo, 같은 타깃, 같은 세션)를 쓰므로 두 수치를 나란히
읽을 수 있다.

**읽는 법**

- 피처 IC 가 음수인데 가중치가 양수면, 그 피처는 점수를 깎고 있다
- 폴드 부호가 뒤집히는 피처는 약한 신호가 아니라 **잡음**이다. 가중치를
  줄이는 게 아니라 빼는 것이 맞다
- 합산 IC 가 개별 피처 최고치보다 낮으면, 가중치가 서로를 상쇄하고 있다

**이 결과로 가중치를 자동으로 정하지 않는다.** 같은 표본에서 IC 를 재고 그
표본에 맞춰 가중치를 고르면, 그 가중치는 표본에 맞춘 것이고 다음 구간에서
사라진다. 여기서 얻는 것은 **빼야 할 피처의 목록**이지 최적 가중치가 아니다.
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

from quant_rl_trading.analysts import ic  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market, trading_days  # noqa: E402
from quant_rl_trading.collectors.publication import publication_policy  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock, ReplayClock  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from tools.backfill import build_store, load_env  # noqa: E402
from tools.measure_ic import ANALYSTS, target_span  # noqa: E402


def feature_panel(
    analyst_name: str,
    store: Store,
    sessions: list[date],
    market: Market,
    *,
    verbose: bool,
) -> tuple[pd.DataFrame, dict[str, float]]:
    """세션마다 피처 프레임을 모아 하나의 패널로.

    ``run()`` 이 아니라 ``features()`` 만 부른다 — 증거·확신도는 여기서 쓰지
    않고, 그만큼 세션당 비용이 준다.
    """
    clock = LiveClock()
    analyst = ANALYSTS[analyst_name](store, clock, market=market)
    policy = publication_policy(store, market, clock=clock)

    frames: list[pd.DataFrame] = []
    started = time.monotonic()  # invariant-allow: wallclock

    for index, session in enumerate(sessions, start=1):
        as_of = policy.for_session(session)
        analyst.clock = ReplayClock(as_of)
        frame = analyst.features(as_of)
        if not frame.empty:
            frames.append(
                frame.reset_index().rename(columns={"index": "entity_id"}).assign(session=session)
            )
        if verbose and index % 25 == 0:
            elapsed = time.monotonic() - started  # invariant-allow: wallclock
            rate = elapsed / index
            print(
                f"       … {index}/{len(sessions)} "
                f"(남은시간 ~{timedelta(seconds=int(rate * (len(sessions) - index)))})",
                flush=True,
            )

    weights = dict(getattr(sys.modules[type(analyst).__module__], "WEIGHTS", {}) or {})
    if not frames:
        return pd.DataFrame(), weights
    return pd.concat(frames, ignore_index=True), weights


def measure(
    analyst_name: str, store: Store, *, market: Market, sessions: int, verbose: bool
) -> tuple[list[ic.ICResult], dict[str, float]]:
    clock = LiveClock()
    as_of = clock.now()
    threshold, min_days, t_min = ic.thresholds(store, as_of=as_of)

    targets = ic.build_targets(
        store, as_of=as_of, lookback=target_span(sessions), market=str(market)
    )
    if targets.empty:
        raise SystemExit("타깃이 비었다. prices 백필이 되어 있는지 확인할 것.")

    available = sorted(targets["session"].unique())
    window = available[-sessions:]
    calendar = [
        day for day in trading_days(market, window[0], window[-1]) if day in set(window)
    ]

    panel, weights = feature_panel(
        analyst_name, store, calendar, market, verbose=verbose
    )
    if panel.empty:
        raise SystemExit(f"{analyst_name} 피처가 비었다. 명단·시세를 먼저 확인할 것.")

    columns = [
        name for name in panel.columns if name not in {"entity_id", "session"}
    ]
    results: list[ic.ICResult] = []
    for column in columns:
        scores = panel[["entity_id", "session", column]].rename(
            columns={column: "score"}
        )
        results.append(
            ic.evaluate(
                scores,
                targets,
                analyst=column,
                analyst_version=f"{analyst_name}-feature",
                market=str(market),
                threshold=threshold,
                min_sample_days=min_days,
        t_min=t_min,
            )
        )
    return results, weights


def render(
    analyst_name: str,
    results: list[ic.ICResult],
    weights: dict[str, float],
    *,
    market: Market,
) -> str:
    total = sum(abs(value) for value in weights.values()) or 1.0

    def contribution(result: ic.ICResult) -> float:
        """가중치 부호까지 반영한 기여.

        ``reversal_5`` 처럼 **음수 가중치로 선언된 피처**는 raw IC 가 음수여야
        점수에 양으로 기여한다. raw 만 보고 "음수니까 빼자" 하면 제대로 일하던
        피처를 빼게 된다.
        """
        declared = weights.get(result.analyst)
        if declared is None or declared == 0:
            return result.ic
        return result.ic if declared > 0 else -result.ic

    lines = [
        f"=== {analyst_name} 피처별 IC · {market} ===",
        "",
        f"{'피처':<20}{'기여IC':>10}{'raw':>10}{'가중치':>10}   폴드별",
    ]
    for result in sorted(results, key=contribution, reverse=True):
        declared = weights.get(result.analyst)
        share = f"{declared / total:>9.1%}" if declared is not None else f"{'—':>10}"
        folds = "  ".join(f"{value:+.3f}" for value in result.fold_ics)
        flip = "  ⚠ 부호 뒤집힘" if _flips(result.fold_ics) else ""
        lines.append(
            f"{result.analyst:<20}{contribution(result):>+10.4f}"
            f"{result.ic:>+10.4f}{share}   {folds}{flip}"
        )

    lines += [
        "",
        f"표본 {results[0].sample_days}일 / {results[0].sample_rows:,}행"
        if results
        else "",
        "",
        "⚠ 는 폴드 부호가 뒤집힌다는 뜻이다 — 약한 신호가 아니라 잡음이다.",
        "이 표로 가중치를 고르면 그 표본에 맞춘 값이 된다. 여기서 얻을 것은",
        "**뺄 피처의 목록**이다.",
    ]
    return "\n".join(lines)


def _flips(folds: tuple[float, ...]) -> bool:
    """폴드 부호가 갈리는가. 한쪽이라도 반대면 그렇다."""
    if len(folds) < 2:
        return False
    return any(value > 0 for value in folds) and any(value < 0 for value in folds)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analyst", required=True, choices=sorted(ANALYSTS))
    parser.add_argument("--sessions", type=int, default=300)
    parser.add_argument("--market", default="KR", choices=[m.value for m in Market])
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    load_env()
    store = build_store(args.data_root)
    market = Market(args.market)

    results, weights = measure(
        args.analyst,
        store,
        market=market,
        sessions=args.sessions,
        verbose=not args.quiet,
    )
    print()
    print(render(args.analyst, results, weights, market=market))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
