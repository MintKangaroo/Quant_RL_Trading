"""가중치 진화 — selector.md §4 · M3-kickoff 3-4.

    uv run python tools/run_evolution.py \\
        --market KR --start 2026-01-02 --end 2026-03-31 \\
        --as-of 2026-08-14T09:00:00+09:00 \\
        --population 4 --generations 2 --folds-per-eval 1 --fold-days 5 --warmup 0

**절대 기본값(64×40)으로 이 기계에서 돌리지 마라.** OOS 백테스트가 이미 메모리
여유를 2.5GB 까지 깎아 놨다(2026-08-14 밤). ``--population``·``--generations``·
``--folds-per-eval``·``--fold-days`` 를 전부 작게 주고, 끝나면 이 스크립트가
찍는 "예상 비용" 을 보고 전체 실행 여부를 판단할 것 — 판단은 사람이 한다.

## 두 겹 오버레이 — 신호는 한 번만 계산한다

Analyst 신호(``signals``)는 가중치와 무관하다(합성 단계에서만 가중치를 쓴다).
그런데 개체마다 백테스트를 새로 돌리면 6종 Analyst 의 피처 계산을 population
× generations 번 반복하게 된다 — 이게 진화의 진짜 비용이다.

그래서 창고를 두 겹으로 쌓는다.

    실제 창고 (읽기 전용, 심볼릭 링크)
      └─ 프라이밍 레이어    writable = run_backtest.WRITABLE
           (구간 전체를 한 번 돌려 signals 를 채운다. 이 레이어의 주문·체결은
            버린다 — 누구도 다시 안 읽는다)
           └─ 개체 레이어    writable = run_backtest.WRITABLE (매 평가마다 새로)
                (source 가 프라이밍 레이어라서 signals 는 심볼릭 링크로 본다.
                 journal·analyst_weights 만 이 개체 몫으로 새로 쓴다)

개체 레이어를 공유하면 같은 날짜의 주문·체결이 개체끼리 충돌한다(자연키가
날짜 기준이라 두 번째 개체의 적재가 "이미 있음" 으로 조용히 무시된다) —
그래서 **개체마다, 폴드 평가마다 새 레이어를 깐다.**
"""

from __future__ import annotations

import argparse
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

import numpy as np  # noqa: E402

from quant_rl_trading.backtest import loop as loop_module  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market, trading_days  # noqa: E402
from quant_rl_trading.selector import evolution as evolution_module  # noqa: E402
from quant_rl_trading.selector import weights as weights_module  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import Store, overlay  # noqa: E402
from tools.backfill import build_store  # noqa: E402
from tools.run_backtest import WRITABLE as JOURNAL_WRITABLE  # noqa: E402

SEOUL = ZoneInfo("Asia/Seoul")
DEFAULT_SANDBOX = REPO_ROOT / "data" / "_evolution"

#: 프라이밍 레이어가 쓰는 테이블. run_backtest.py 와 같은 집합이다 — signals 를
#: 채우려면 loop.run 을 그대로 한 번 돌려야 하고, 그러려면 journal·capital_flows
#: 도 쓸 수 있어야 한다(버릴 뿐 다시 읽지는 않는다).
PRIMING_WRITABLE = JOURNAL_WRITABLE

#: 개체 레이어가 쓰는 테이블. **``signals`` 를 뺀다** — 빼야 오버레이가 그것을
#: 프라이밍 레이어로 심볼릭 링크하고, 그래야 개체가 프라이밍의 신호를 본다.
#: ``JOURNAL_WRITABLE`` 을 그대로 주면 ``signals`` 가 **빈 디렉터리**로 생겨
#: 개체마다 6종 Analyst 피처를 처음부터 다시 계산한다 — 프라이밍 레이어가
#: 통째로 무용지물이 되고, 그게 이 파일 문서가 없앴다고 주장하는 바로 그 비용이다.
INDIVIDUAL_WRITABLE = JOURNAL_WRITABLE - {"signals"}


def _weight_as_of(earliest_start: date, warmup_days: int) -> datetime:
    """모든 폴드·워밍업보다 확실히 앞선 시각. 넉넉한 버퍼라 정밀할 필요 없다."""
    buffer_days = warmup_days * 3 + 30
    probe_day = earliest_start - timedelta(days=buffer_days)
    return datetime.combine(probe_day, dtime(0, 0), tzinfo=SEOUL)


def _available_fold_starts(
    market: Market, start: date, end: date, fold_trading_days: int
) -> list[date]:
    sessions = trading_days(market, start, end)
    if len(sessions) < fold_trading_days:
        return []
    return sessions[: len(sessions) - fold_trading_days + 1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--market", default="KR", choices=["KR", "US"])
    parser.add_argument("--start", required=True, help="폴드를 뽑을 구간 시작 (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="폴드를 뽑을 구간 끝 (YYYY-MM-DD)")
    parser.add_argument(
        "--as-of", required=True,
        help="Analyst 가 IC 0.03 을 통과했는지 볼 시각(ISO, tz 포함). "
        "실제 창고의 최신 analyst_weights 를 이 시각 기준으로 읽어 유전자 목록을 정한다.",
    )
    parser.add_argument(
        "--population", type=int, default=None, help="기본: selector.population"
    )
    parser.add_argument(
        "--generations", type=int, default=None, help="기본: selector.generations"
    )
    parser.add_argument(
        "--folds-per-eval", type=int, default=3, help="개체당 시작일 수(중앙값 평가)"
    )
    parser.add_argument("--fold-days", type=int, default=20, help="폴드 하나의 거래일 수")
    parser.add_argument("--warmup", type=int, default=0, help="폴드 앞에 붙일 워밍업 거래일")
    parser.add_argument("--capital", type=float, default=100_000_000.0)
    parser.add_argument("--board", default="KOSPI")
    parser.add_argument("--l1-penalty", type=float, default=None, help="기본: selector.l1_penalty")
    parser.add_argument(
        "--turnover-penalty", type=float, default=evolution_module.DEFAULT_TURNOVER_PENALTY
    )
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--patience", type=int, default=evolution_module.DEFAULT_PATIENCE)
    parser.add_argument(
        "--stability-top-n", type=int, default=evolution_module.DEFAULT_STABILITY_TOP_N
    )
    parser.add_argument(
        "--stability-threshold", type=float, default=evolution_module.DEFAULT_STABILITY_THRESHOLD
    )
    parser.add_argument("--sandbox", default=str(DEFAULT_SANDBOX))
    parser.add_argument("--fresh", action="store_true", help="샌드박스를 비우고 시작한다")
    args = parser.parse_args(argv)

    load_env()
    market_enum = Market(args.market)
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    as_of = datetime.fromisoformat(args.as_of)

    source_root = build_store(None).root
    real_store = Store(root=source_root)

    # 유전자 목록 — IC 0.03 을 통과한 Analyst 만(실제 창고, 최신 측정 기준).
    active = sorted(
        weights_module.analyst_weights(real_store, as_of=as_of, market=args.market).keys()
    )
    if not active:
        print(
            f"{args.market} 에 IC 0.03 을 통과한 Analyst 가 없다(as_of={as_of}). "
            "진화는 유전자가 있어야 돈다 — measure_ic 를 먼저 돌릴 것.",
            file=sys.stderr,
        )
        return 2
    print(f"유전자({len(active)}개): {', '.join(active)}")

    population = args.population or int(real_store.config("selector.population", as_of=as_of))
    generations = args.generations or int(real_store.config("selector.generations", as_of=as_of))
    l1_penalty = (
        args.l1_penalty
        if args.l1_penalty is not None
        else float(real_store.config("selector.l1_penalty", as_of=as_of))
    )

    fold_starts = _available_fold_starts(market_enum, start, end, args.fold_days)
    if not fold_starts:
        print(f"{start}~{end} 에 {args.fold_days}거래일짜리 폴드가 없다.", file=sys.stderr)
        return 2

    sandbox = Path(args.sandbox)
    if args.fresh and sandbox.exists():
        shutil.rmtree(sandbox)

    # 1. 프라이밍 레이어 — 구간 전체 signals 를 한 번만 계산한다.
    priming_root = sandbox / "priming"
    priming_layer = overlay.build(root=priming_root, source=source_root, writable=PRIMING_WRITABLE)
    priming_store = Store(root=priming_layer.root)

    weight_as_of = _weight_as_of(start, args.warmup)
    prime_run_id = f"evolution-prime-{args.market}"
    if not priming_store.ingest_run_recorded("analyst_weights", prime_run_id):
        uniform = 1.0 / len(active)
        priming_store.append(
            "analyst_weights",
            [
                {
                    "entity_id": analyst, "valid_from": weight_as_of, "observed_at": weight_as_of,
                    "source": "evolution-prime", "market": args.market, "weight": uniform,
                    "analyst_version": "evo-prime",
                }
                for analyst in active
            ],
            ingest_run_id=prime_run_id,
            source="evolution-prime",
        )

    print(f"프라이밍: {start}~{end} 신호 계산 (한 번만, 이후 개체들이 공유)…")
    prime_started = time.perf_counter()
    loop_module.run(
        priming_store, start=start, end=end, market=args.market,
        capital=args.capital, board=args.board, warmup_days=args.warmup, produce_signals=True,
    )
    prime_elapsed = time.perf_counter() - prime_started
    print(f"프라이밍 완료: {prime_elapsed:.1f}s")

    # 2. 개체 레이어 — 매 평가마다 새로 깐다. journal·analyst_weights 만 이
    #    개체 몫이고, signals 는 프라이밍 레이어를 그대로 링크로 본다.
    individual_root = sandbox / "individual"
    eval_timings: list[float] = []
    rng_folds = np.random.default_rng(args.seed + 1)  # 폴드 리샘플링 전용 rng — GA 본체와 분리

    #: **세대당 한 번만 뽑는다.** selector.md §4 는 "매 세대 검증 구간을 다른
    #: 폴드로 교체" 라고 못 박는다 — 개체마다 다시 뽑으면 같은 세대의 16개
    #: 개체가 서로 **다른 구간**에서 채점되고, 그 적합도 차이는 가중치가 아니라
    #: 구간 차이다. 그러면 토너먼트 선택이 노이즈를 고르고, 안정성 검사는
    #: "지형이 평평하다" 는 **거짓 음성**을 낸다(측정이 흔들린 것을 지형
    #: 탓으로 읽는다).
    fold_cache: dict[int, list[date]] = {}

    def evaluate(
        individual: evolution_module.Individual, generation: int
    ) -> evolution_module.FitnessResult:
        folds = fold_cache.get(generation)
        if folds is None:
            folds = evolution_module.resample_folds(fold_starts, args.folds_per_eval, rng_folds)
            fold_cache[generation] = folds
        if individual_root.exists():
            shutil.rmtree(individual_root)
        layer = overlay.build(
            root=individual_root, source=priming_layer.root, writable=INDIVIDUAL_WRITABLE
        )
        eval_store = Store(root=layer.root)
        started = time.perf_counter()
        result = evolution_module.backtest_fitness(
            eval_store, individual,
            market=args.market, fold_starts=folds, fold_trading_days=args.fold_days,
            generation=generation, weight_as_of=weight_as_of, capital=args.capital,
            warmup_days=args.warmup, board=args.board,
            l1_penalty=l1_penalty, turnover_penalty=args.turnover_penalty,
            # 프라이밍이 이미 구간 전체의 신호를 채웠다. 개체는 읽기만 한다.
            produce_signals=False,
        )
        eval_timings.append(time.perf_counter() - started)
        return result

    print(
        f"진화 시작: {args.market} · population {population} · generations {generations} · "
        f"폴드/개체 {args.folds_per_eval} · 폴드 거래일 {args.fold_days} · seed {args.seed}"
    )
    evolve_started = time.perf_counter()
    outcome = evolution_module.evolve(
        analysts=active, population_size=population, generations=generations,
        evaluate=evaluate, patience=args.patience, seed=args.seed,
        stability_top_n=args.stability_top_n, stability_threshold=args.stability_threshold,
    )
    evolve_elapsed = time.perf_counter() - evolve_started

    print()
    print(f"{generations_run_note(outcome)}")
    for record in outcome.history:
        print(
            f"  세대 {record.generation:>3}  최고 {record.best_fitness:+.4f}  "
            f"평균 {record.mean_fitness:+.4f}  최고개체 "
            f"{_format_weights(record.best_individual)}"
        )

    print()
    print(outcome.stability.summary())
    print(f"채택 여부: {'가능' if outcome.adopt else '불가 — 동일가중을 대신 쓸 것'}")

    best = max(outcome.fitnesses, key=lambda item: item.fitness)
    print(f"\n최종 최고 개체 적합도 {best.fitness:+.4f}  IR중앙값 {best.ir_median:+.4f}  "
          f"회전율중앙값 {best.turnover_median:.3f}  가중치 {_format_weights(best.individual)}")

    _print_cost_projection(
        eval_timings=eval_timings,
        prime_elapsed=prime_elapsed,
        evolve_elapsed=evolve_elapsed,
        actual_population=population,
        actual_generations=outcome.generations_run,
        folds_per_eval=args.folds_per_eval,
        fold_days=args.fold_days,
    )
    return 0 if outcome.adopt else 1


def generations_run_note(outcome: evolution_module.EvolutionResult) -> str:
    tail = "조기 종료" if outcome.stopped_early else "전체 세대 완주"
    return f"{outcome.generations_run}세대 실행({tail})"


def _format_weights(individual: evolution_module.Individual) -> str:
    normed = individual.normalized()
    return " ".join(f"{name}={weight:.3f}" for name, weight in sorted(normed.items()))


def _print_cost_projection(
    *,
    eval_timings: list[float],
    prime_elapsed: float,
    evolve_elapsed: float,
    actual_population: int,
    actual_generations: int,
    folds_per_eval: int,
    fold_days: int,
) -> None:
    """이번 소규모 실행에서 잰 시간을 kickoff 명세(population 64 × generations 40)로
    외삽한다. **실행하지 않는다 — 계산만 한다.**
    """
    print("\n--- 비용 추정 (이번 실행 실측 → 64×40 외삽) ---")
    if not eval_timings:
        print("개체 평가가 0건이라 추정할 수 없다.")
        return
    mean_eval = sum(eval_timings) / len(eval_timings)
    evals_done = len(eval_timings)
    print(
        f"이번 실행: 프라이밍 {prime_elapsed:.1f}s · 개체 평가 {evals_done}건 · "
        f"평균 {mean_eval:.1f}s/개체(폴드 {folds_per_eval}개 × 거래일 {fold_days}일) · "
        f"진화 총 {evolve_elapsed:.1f}s"
    )
    full_evals = 64 * 40
    projected_seconds = full_evals * mean_eval
    print(
        f"kickoff 명세(population 64 × generations 40 = {full_evals}회 평가)로 외삽하면 "
        f"약 {projected_seconds / 3600:.1f}시간(신호 프라이밍은 재사용되므로 별도)."
    )
    print(
        "이 추정은 이번 실행의 fold-days·folds-per-eval·warmup 스케일에 매인 값이다 — "
        "실전 스케일(warmup 70일 등)로 늘리면 개체당 시간이 비례해 커진다. "
        "실행 전에 반드시 사람이 이 숫자를 보고 규모를 정할 것."
    )


if __name__ == "__main__":
    raise SystemExit(main())
