"""구간 백테스트 — 오버레이 창고 위에서 돌린다.

    uv run python tools/run_backtest.py --start 2025-08-01 --end 2026-02-28
    uv run python tools/run_backtest.py --start 2025-08-01 --end 2026-02-28 --fresh

**실제 창고에는 쓰지 않는다.** 모의 체결이 실전 장부에 섞이면 회계가 그것을
구분하지 못하고, append-only 창고에서 그건 되돌릴 수 없다 (backtest.md §4).
쓰기는 전부 ``--sandbox`` 아래로 간다.

기본 워밍업 70거래일은 **필요해서 있는 값이다.** confidence 가 최근 60거래일
롤링 IC 라, 신호 이력이 없는 구간은 confidence 0 → 합성 점수 0건 → 매매 0건이
된다. 그 구간을 성적에 넣으면 평평한 앞부분이 MDD 를 실제보다 작게 만든다.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.backtest import loop  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import Store, overlay  # noqa: E402
from tools.backfill import build_store  # noqa: E402

#: 백테스트가 쓰는 테이블. 나머지는 실제 창고를 링크로 그대로 본다.
#:
#: ``analyst_weights`` 가 여기 있는 이유는 **워크포워드** 때문이다. 실전
#: 가중치는 오늘 관측이라 과거 백테스트가 보면 안 되고(보면 미래를 훔친다),
#: 과거 시점 측정본을 실전 창고에 심으면 "그때 알았던 것" 이라는 거짓 기록이
#: 남는다. 샌드박스에 두면 둘 다 피한다 (backtest.md §7).
WRITABLE = frozenset(
    {"signals", "verdicts", "orders", "trades", "realized_weights", "nav_daily",
     "capital_flows", "dividends", "events", "killswitch", "analyst_weights"}
)

DEFAULT_SANDBOX = REPO_ROOT / "data" / "_backtest"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", help="평가 시작일 (YYYY-MM-DD)")
    parser.add_argument("--end", help="평가 종료일 (YYYY-MM-DD)")
    parser.add_argument("--market", default="KR", choices=["KR", "US"])
    parser.add_argument("--capital", type=float, default=100_000_000.0)
    parser.add_argument(
        "--warmup", type=int, default=None, help="성적에 넣지 않는 선행 거래일 (기본: 설정값)"
    )
    parser.add_argument("--sandbox", default=str(DEFAULT_SANDBOX))
    parser.add_argument("--fresh", action="store_true", help="샌드박스를 비우고 시작한다")
    parser.add_argument(
        "--build-only",
        action="store_true",
        help="오버레이만 만들고 끝낸다. 이 위에 measure_ic --data-root 로 과거 가중치를 심는다",
    )
    args = parser.parse_args(argv)

    load_env()
    source = build_store(None).root
    layer = overlay.build(
        root=Path(args.sandbox), source=source, writable=WRITABLE
    )
    if args.fresh:
        layer.clear()
    store = Store(root=layer.root)

    if not args.build_only and not (args.start and args.end):
        print("--start 와 --end 가 필요하다 (--build-only 제외).", file=sys.stderr)
        return 2
    if args.build_only:
        print(f"오버레이 준비됨: {layer.root}")
        return 0

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    # 임계치는 설정에서 온다 (불변식 10). 시점은 평가 시작일의 스냅샷 시각이다.
    probe = loop.snapshot_moment(store, start, as_of=loop.datetime.combine(
        start, loop.DEFAULT_SNAPSHOT_TIME, tzinfo=loop.SEOUL))
    warmup = (
        args.warmup
        if args.warmup is not None
        else int(store.config("backtest.warmup_days", as_of=probe))
    )
    print(f"{args.market} {start} ~ {end} · 워밍업 {warmup}거래일 · 샌드박스 {layer.root}")

    def show(day: loop.DayResult) -> None:
        mark = "·" if day.warmup else " "
        print(
            f"  {mark}{day.as_of.date()}  NAV {day.nav:>14,.0f}  "
            f"지수 {day.index_value:>8.2f}  낙폭 {day.drawdown:>7.2%}  "
            f"후보 {len(day.candidates):>2}  주문 {day.planned_orders:>3}  "
            f"체결 {day.filled:>5}"
            + (f"  ⚠️ {day.blocked_by}" if day.blocked_by else "")
        )

    result = loop.run(
        store,
        start=start,
        end=end,
        market=args.market,
        capital=args.capital,
        warmup_days=warmup,
        on_day=show,
    )

    for note in result.notes:
        print(f"  ⚠️  {note}")
    if result.performance is None:
        return 1

    print()
    print(result.performance.summary())
    print(f"지문 {result.digest()}")

    # M3 완료 기준. 판정을 사람 눈에 맡기지 않는다.
    budget = float(store.config("backtest.mdd_promotion_gate", as_of=result.days[-1].as_of))
    passed = abs(result.performance.max_drawdown) <= budget
    print(f"MDD 예산 {budget:.0%} → {'통과' if passed else '초과'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
