#!/usr/bin/env python
"""가중치 격자 탐색 실행기 — GA 대신 심플렉스를 통째로 잰다.

    uv run python tools/run_grid.py --start 2025-01-02 --end 2025-12-30 \
        --holdout-start 2026-01-02 --holdout-end 2026-06-30 --steps 10

``tools/run_evolution.py`` 와 창고 격리·프라이밍 구조를 그대로 쓴다. 다른
알고리즘을 위해 두 번째 배선을 만들면 둘의 성적 차이가 알고리즘 차이인지
배선 차이인지 말할 수 없게 된다.

## 격자가 GA 와 다른 점 하나 — 폴드를 안 바꾼다

``run_evolution`` 은 세대마다 폴드를 새로 뽑는다(과적합 방지). 격자는 그러면
안 된다. **모든 점이 같은 폴드에서 채점돼야** 점수 차이가 가중치 차이가 된다
— 점마다 구간이 다르면 그 차이는 지형이 아니라 장세다. GA 가 세대 안에서
폴드를 고정하는 것과 같은 이유이고, 격자는 통째로 한 세대다.

과적합은 폴드 교체가 아니라 **홀드아웃**으로 막는다. 학습에 한 번도 안 쓴
구간에서 최고점과 동일가중을 다시 재고, 거기서 못 이기면 채택하지 않는다.

## 비용

점 하나가 폴드 수만큼의 백테스트다. 3 Analyst · 해상도 0.1 이면 66점이고,
폴드 2개면 132회다. 시작 전에 계산해서 찍는다 — 몇 시간짜리인지 모르고
거는 것이 이 저장소에서 제일 비싼 실수였다.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from datetime import date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.backtest import loop as loop_module  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market  # noqa: E402
from quant_rl_trading.selector import evolution as evolution_module  # noqa: E402
from quant_rl_trading.selector import grid as grid_module  # noqa: E402
from quant_rl_trading.selector import weights as weights_module  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import Store, overlay  # noqa: E402
from tools.backfill import build_store  # noqa: E402
from tools.run_backtest import WRITABLE as JOURNAL_WRITABLE  # noqa: E402
from tools.run_evolution import (  # noqa: E402
    INDIVIDUAL_WRITABLE,
    _available_fold_starts,
    _weight_as_of,
)

SEOUL = ZoneInfo("Asia/Seoul")
DEFAULT_SANDBOX = REPO_ROOT / "data" / "_grid"
PRIMING_WRITABLE = JOURNAL_WRITABLE


def _spread(starts: list[date], count: int, *, fold_days: int) -> tuple[list[date], str | None]:
    """폴드 시작일을 고른다. **가능하면 안 겹치게.**

    ``starts[:count]`` 로 자르면 안 된다. ``_available_fold_starts`` 는 가능한
    시작일을 하루 단위로 전부 돌려주므로, 앞에서 N개를 자르면 하루 간격으로
    붙은 날짜가 나온다 — 60거래일짜리 폴드 둘이 하루 차이면 **같은 구간을 두
    번 재는 것**이고, 그 둘의 중앙값은 표본 2개가 아니라 1개다. 그러면
    ``fold_noise`` 가 잡음을 실제보다 작게 추정하고, 격자가 못 고른 것을
    골랐다고 말하게 된다.

    ``starts`` 는 연속된 거래일 시작점이라 **인덱스 간격이 곧 거래일 간격**
    이다. 그래서 간격을 ``fold_days`` 이상으로 잡으면 폴드가 안 겹친다.
    구간이 좁아 그만큼 못 벌리면 등간격으로 물러서되, **겹친다는 사실을
    돌려준다** — 조용히 겹치면 표본 수를 잘못 세게 된다.
    """
    if count <= 0 or not starts:
        return [], None
    if count == 1:
        return [starts[0]], None

    # 1순위: 안 겹치는 폴드.
    picked = [0]
    for index in range(1, len(starts)):
        if index - picked[-1] >= fold_days:
            picked.append(index)
            if len(picked) == count:
                return [starts[i] for i in picked], None

    # 2순위: 등간격. 겹치는 정도를 같이 알린다.
    stride = max(1, len(starts) // count)
    chosen = starts[::stride][:count]
    overlap = max(0, fold_days - stride)
    note = (
        f"폴드가 {overlap}거래일 겹친다(간격 {stride} < 폴드 {fold_days}) — "
        f"구간이 좁아 {count}개를 안 겹치게 못 뽑았다. 표본은 {len(chosen)}개보다 "
        "적게 쳐야 하고, 잡음 추정(SE)이 실제보다 작게 나온다"
    )
    return chosen, note


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", default="KR", choices=["KR", "US"])
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--holdout-start", default=None)
    parser.add_argument("--holdout-end", default=None)
    parser.add_argument(
        "--steps", type=int, default=grid_module.DEFAULT_STEPS,
        help="해상도. 10 이면 0.1 단위(3 Analyst 기준 66점), 20 이면 0.05(231점)",
    )
    parser.add_argument("--fold-days", type=int, default=60, help="폴드 하나의 거래일 수")
    parser.add_argument(
        "--folds", type=int, default=2,
        help="점마다 쓸 폴드 수. **모든 점이 같은 폴드를 쓴다**",
    )
    parser.add_argument("--warmup", type=int, default=0)
    parser.add_argument("--capital", type=float, default=100_000_000.0)
    parser.add_argument("--board", default="KOSPI")
    parser.add_argument("--l1-penalty", type=float, default=None)
    parser.add_argument("--turnover-penalty", type=float, default=None)
    parser.add_argument("--sandbox", default=str(DEFAULT_SANDBOX))
    parser.add_argument("--fresh", action="store_true", help="샌드박스를 비우고 시작")
    parser.add_argument("--as-of", default=None, help="가중치 조회 시점 (ISO8601)")
    parser.add_argument("--out", default=None, help="격자 결과 JSONL 경로")
    parser.add_argument(
        "--dry-run", action="store_true", help="비용만 계산하고 끝낸다"
    )
    args = parser.parse_args(argv)

    # 몇 시간짜리다. 파이프로 나가면 블록 버퍼링이 걸려 죽을 때까지 로그가
    # 0바이트로 남는다 — run_evolution 이 그렇게 3시간을 날렸다.
    sys.stdout.reconfigure(line_buffering=True)

    load_env()
    market_enum = Market(args.market)
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    source_root = build_store(None).root
    real_store = Store(root=source_root)
    as_of = (
        datetime.fromisoformat(args.as_of)
        if args.as_of
        else datetime.combine(end, dtime(16, 0), tzinfo=SEOUL)
    )

    active = sorted(
        weights_module.analyst_weights(real_store, as_of=as_of, market=args.market).keys()
    )
    if not active:
        print(
            f"{args.market} 에 IC 0.03 을 통과한 Analyst 가 없다(as_of={as_of}). "
            "measure_ic 를 먼저 돌릴 것.",
            file=sys.stderr,
        )
        return 2

    points_count = grid_module.grid_size(active, steps=args.steps)
    print(f"축({len(active)}개): {', '.join(active)}")
    print(
        f"격자 {points_count}점 × 폴드 {args.folds} = 백테스트 "
        f"{points_count * args.folds}회 (+ 동일가중·홀드아웃)"
    )

    fold_starts = _available_fold_starts(market_enum, start, end, args.fold_days)
    if len(fold_starts) < args.folds:
        print(
            f"{start}~{end} 에 {args.fold_days}거래일짜리 폴드가 "
            f"{len(fold_starts)}개뿐이다 (필요 {args.folds}개).",
            file=sys.stderr,
        )
        return 2

    # **모든 점이 같은 폴드를 쓴다.** 구간에 고르게 퍼지도록 등간격으로 고른다 —
    # 앞에서 N개를 자르면 격자 전체가 한 장세만 보게 된다.
    folds, fold_note = _spread(fold_starts, args.folds, fold_days=args.fold_days)
    print(f"폴드({len(folds)}개, 전 점 공통): {', '.join(str(d) for d in folds)}")
    if fold_note:
        print(f"  ⚠️  {fold_note}")

    holdout_folds: list[date] = []
    if bool(args.holdout_start) != bool(args.holdout_end):
        print("--holdout-start 와 --holdout-end 는 같이 준다.", file=sys.stderr)
        return 2
    if args.holdout_start:
        holdout_from = date.fromisoformat(args.holdout_start)
        holdout_to = date.fromisoformat(args.holdout_end)
        if holdout_from <= end:
            print(
                f"홀드아웃 시작 {holdout_from} 이 학습 끝 {end} 보다 앞선다 — "
                "겹치면 홀드아웃이 아니다.",
                file=sys.stderr,
            )
            return 2
        holdout_folds, holdout_note = _spread(
            _available_fold_starts(
                market_enum, holdout_from, holdout_to, args.fold_days
            ),
            args.folds,
            fold_days=args.fold_days,
        )
        if not holdout_folds:
            print(f"{holdout_from}~{holdout_to} 에 폴드가 없다.", file=sys.stderr)
            return 2
        print(f"홀드아웃 폴드: {', '.join(str(d) for d in holdout_folds)}")
        if holdout_note:
            print(f"  ⚠️  {holdout_note}")
    else:
        print(
            "⚠️  홀드아웃이 없다. 격자는 폴드를 안 바꾸므로 과적합을 막는 것이 "
            "홀드아웃뿐이다 — 결과를 채택 근거로 쓰지 마라"
        )

    if args.dry_run:
        return 0

    l1_penalty = (
        args.l1_penalty
        if args.l1_penalty is not None
        else float(real_store.config("selector.l1_penalty", as_of=as_of))
    )
    turnover_penalty = (
        args.turnover_penalty
        if args.turnover_penalty is not None
        else float(real_store.config("selector.turnover_penalty", as_of=as_of))
    )

    sandbox = Path(args.sandbox)
    if args.fresh and sandbox.exists():
        shutil.rmtree(sandbox)

    # 1. 프라이밍 — 구간 전체 신호를 한 번만 계산하고 모든 점이 링크로 본다.
    priming_root = sandbox / "priming"
    priming_layer = overlay.build(
        root=priming_root, source=source_root, writable=PRIMING_WRITABLE
    )
    priming_store = Store(root=priming_layer.root)
    weight_as_of = _weight_as_of(start, args.warmup)

    prime_run_id = f"grid-prime-{args.market}"
    if not priming_store.ingest_run_recorded("analyst_weights", prime_run_id):
        uniform = 1.0 / len(active)
        priming_store.append(
            "analyst_weights",
            [
                {
                    "entity_id": analyst, "valid_from": weight_as_of,
                    "observed_at": weight_as_of, "source": "grid-prime",
                    "market": args.market, "weight": uniform,
                    "analyst_version": "grid-prime",
                }
                for analyst in active
            ],
            ingest_run_id=prime_run_id,
            source="grid-prime",
        )

    prime_end = max(end, holdout_folds[-1] if holdout_folds else end)
    if args.holdout_end:
        prime_end = max(prime_end, date.fromisoformat(args.holdout_end))
    # 완료 표시는 창고가 아니라 샌드박스 파일이다 — 이건 사실 기록이 아니라
    # 이 도구가 이미 한 일이다. 성공한 **뒤에** 쓴다.
    primed_marker = priming_root / ".primed"
    prime_key = f"{args.market} {start} {prime_end} warmup={args.warmup}"
    already = (
        primed_marker.exists()
        and prime_key in primed_marker.read_text(encoding="utf-8").splitlines()
    )
    if already:
        print(f"프라이밍 재사용: {start}~{prime_end}")
    else:
        print(f"프라이밍: {start}~{prime_end} 신호 계산 (한 번만)…")
        started = time.perf_counter()
        loop_module.run(
            priming_store, start=start, end=prime_end, market=args.market,
            capital=args.capital, board=args.board, warmup_days=args.warmup,
            produce_signals=True,
        )
        with primed_marker.open("a", encoding="utf-8") as handle:
            handle.write(prime_key + "\n")
        print(f"프라이밍 완료: {time.perf_counter() - started:.1f}s")

    # 2. 점마다 새 레이어. journal·analyst_weights 만 그 점 몫이다.
    point_root = sandbox / "point"
    counter = {"n": 0}

    def score(
        individual: evolution_module.Individual,
        fold_set: list[date],
        tag: int,
    ) -> evolution_module.FitnessResult:
        if point_root.exists():
            shutil.rmtree(point_root)
        layer = overlay.build(
            root=point_root, source=priming_layer.root, writable=INDIVIDUAL_WRITABLE
        )
        return evolution_module.backtest_fitness(
            Store(root=layer.root), individual,
            market=args.market, fold_starts=fold_set,
            fold_trading_days=args.fold_days,
            # backtest_fitness 는 이 값으로 적재 run_id 를 가른다. 점마다 달라야
            # 두 번째 점의 가중치 적재가 "이미 있음" 으로 버려지지 않는다.
            generation=tag,
            weight_as_of=weight_as_of, capital=args.capital,
            warmup_days=args.warmup, board=args.board,
            l1_penalty=l1_penalty, turnover_penalty=turnover_penalty,
            produce_signals=False,
        )

    out_path = Path(args.out) if args.out else sandbox / "grid.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    handle = out_path.open("a", encoding="utf-8")
    print(f"격자 기록: {out_path}")

    timings: list[float] = []

    def evaluate(individual: evolution_module.Individual) -> evolution_module.FitnessResult:
        counter["n"] += 1
        began = time.perf_counter()
        result = score(individual, folds, counter["n"])
        timings.append(time.perf_counter() - began)
        return result

    def on_point(point: grid_module.GridPoint, index: int, total: int) -> None:
        # 점마다 한 줄. 몇 시간짜리라 중간에 죽어도 어디까지 했는지 남아야 한다.
        handle.write(
            json.dumps(
                {
                    "index": index,
                    "weights": point.individual.normalized(),
                    "fitness": point.result.fitness,
                    "ir_median": point.result.ir_median,
                    "per_fold_ir": list(point.result.per_fold_ir),
                },
                ensure_ascii=False,
            )
            + "\n"
        )
        handle.flush()
        mean = sum(timings) / len(timings) if timings else 0.0
        left = timedelta(seconds=int(mean * (total - index)))
        weights = " ".join(
            f"{k[:4]}={v:.2f}" for k, v in sorted(point.individual.normalized().items())
        )
        print(
            f"  [{index}/{total}] {weights} → 적합도 {point.result.fitness:+.4f} "
            f"(남은시간 ~{left})"
        )

    started = time.perf_counter()
    report = grid_module.search(
        active, evaluate=evaluate, steps=args.steps, on_point=on_point
    )
    handle.close()
    print(f"\n격자 완료: {time.perf_counter() - started:.1f}s")
    print(report.summary())

    smoothed = grid_module.smoothed_fitness(report.best_smoothed, report.points)
    formatted = " · ".join(
        f"{k} {v:.2f}"
        for k, v in sorted(report.best_smoothed.individual.normalized().items())
    )
    print(f"평활 최고점: {formatted} (평활 적합도 {smoothed:+.4f})")

    if not holdout_folds:
        print("\n홀드아웃이 없어 채택 판정을 하지 않는다.")
        return 0

    # 3. 홀드아웃 — 평활 최고점을 잰다. 단발 잡음일 수 있는 생 최고점보다
    #    평활 쪽이 "지형이 가리키는 점" 이다.
    print("\n홀드아웃 채점…")

    def evaluate_holdout(
        individual: evolution_module.Individual,
    ) -> evolution_module.FitnessResult:
        counter["n"] += 1
        return score(individual, holdout_folds, -counter["n"])

    holdout = evolution_module.holdout_report(
        report.best_smoothed.individual,
        analysts=active,
        folds=holdout_folds,
        train_fitness=report.best_smoothed.result.fitness,
        evaluate=evaluate_holdout,
    )
    print(holdout.summary())

    # 채택은 **둘 다** 통과해야 한다. 격자에서 잡음을 넘었어도 홀드아웃에서
    # 동일가중을 못 이기면 학습 구간에서만 좋았던 것이다(selector.md §4).
    adopt = report.resolvable and holdout.beats_uniform
    print(
        f"\n채택 판정: {'채택' if adopt else '기각'} "
        f"(격자 구분 {report.resolvable} · 홀드아웃 승 {holdout.beats_uniform})"
    )
    return 0 if adopt else 1


if __name__ == "__main__":
    raise SystemExit(main())
