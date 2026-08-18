"""원/달러 환율 수집 — FRED DEXKOUS.

    uv run python tools/collect_fx.py --start 2021-08-01 --end 2026-08-13

**회계의 전제다.** 환율이 없으면 `accounting` 이 NAV 계산을 거부하고(그게
옳다), 그러면 백테스트도 shadow 도 한 줄도 못 나간다. 창고에 `fx` 가 0행이던
동안 회계는 테스트 위에서만 돌고 있었다.

라이브에서는 하루 한 번 어제까지를 다시 받으면 된다 — 같은 구간 run_id 는
결정론적이라 창고가 중복을 거부한다.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.collectors.macro_source import FredSource, FxCollector  # noqa: E402
from quant_rl_trading.collectors.raw import RawArchive  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from tools.backfill import build_store, load_env  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--start", help="시작일 (기본: 30일 전)")
    parser.add_argument("--end", help="종료일 (기본: 어제)")
    args = parser.parse_args(argv)

    load_env()
    store = build_store(args.data_root)
    clock = LiveClock()
    today = clock.now().date()

    end = date.fromisoformat(args.end) if args.end else today - timedelta(days=1)
    start = (
        date.fromisoformat(args.start) if args.start else end - timedelta(days=30)
    )

    source = FredSource.from_env()
    if not source.usable():
        print("FRED_API_KEY 가 없다. 환율 없이는 NAV 를 계산할 수 없다.", file=sys.stderr)
        return 2

    written = FxCollector(
        store=store,
        source=source,
        clock=clock,
        archive=RawArchive(store.root),
        start=start,
        end=end,
    ).collect()
    print(f"fx {written}행 ({start} ~ {end})")

    # **행 수로 성공을 판정하지 않는다.** ``0행`` 은 "FRED 에 새 값이 없다" 와
    # "이미 받았다" 를 같은 문구로 말하고, 둘 다 정상일 수 있다. 물어야 할 것은
    # **창고가 지금 쓸 만큼 최신인가** 다.
    #
    # 2026-08-18 실측: 환율이 8/7 에 멈춘 채 11일이 지났는데 이 도구는 매일
    # ``fx 0행 … rc=0`` 을 찍고 있었다. 아무도 몰랐고, 처음 미장 주식을 산 날
    # 회계가 "환율이 없다" 로 평가를 거부하고 나서야 드러났다. 그 사이 NAV 는
    # 계산조차 되지 않았다.
    return 0 if _fresh_enough(store, clock) else 1


#: 창고가 이보다 오래되면 실패로 본다. 회계 조회 창(``ledger.fx_rate`` 의
#: lookback=10일)보다 짧게 잡는다 — 회계가 죽고 나서 아는 것은 늦다.
#: 환율은 주말·공휴일에 안 나오므로 연휴를 견딜 만큼은 둔다.
STALE_AFTER_DAYS = 6


def _fresh_enough(store, clock) -> bool:
    """창고의 최신 환율이 쓸 만큼 새것인가. **아니면 크게 말한다.**"""
    from quant_rl_trading.accounting.ledger import FX_USDKRW

    now = clock.now()
    frame = store.get("fx", as_of=now, entity=FX_USDKRW, lookback=60)
    if frame.empty:
        print(
            "환율이 창고에 한 행도 없다 — 회계가 NAV 계산을 거부한다.",
            file=sys.stderr,
        )
        return False

    newest = frame["valid_from"].max()
    age = (now - newest.to_pydatetime()).days
    print(f"창고 최신 환율 {newest.date()} · {age}일 전")
    if age > STALE_AFTER_DAYS:
        print(
            f"환율이 {age}일 낡았다(허용 {STALE_AFTER_DAYS}일). "
            "FRED 발표 지연인지 수집 경로 고장인지 확인할 것 — "
            "10일을 넘기면 회계가 해외분 평가를 거부하고 NAV 가 통째로 멈춘다.",
            file=sys.stderr,
        )
        return False
    return True


if __name__ == "__main__":
    raise SystemExit(main())
