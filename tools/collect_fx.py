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


#: **회계가 죽는 선.** `ledger.fx_rate` 의 조회 창이 10일이라 그것을 넘기면
#: 해외분 평가가 거부되고 NAV 가 통째로 멈춘다. 원본이 늦든 우리가 못 받았든
#: 여기까지 오면 비상이다 — 그래서 이 한계는 발행 일정과 무관하게 건다.
NAV_BREAKS_AFTER_DAYS = 9

#: **날짜 수로 재던 것이 틀렸다** (2026-08-21 에 걷어냈다).
#:
#: FRED H.10 은 **월요일 주간 발행**이고 그 발행분이 담는 마지막 관측은 직전
#: 금요일이다. 그러면 금요일에는 창고 최신값이 정상적으로 7일 전이 된다 —
#: 6일 임계로는 **매주 금요일마다 경보가 뜬다.** 실측 2026-08-21(금)에 그랬다.
#:
#: 매주 우는 경보는 곧 아무도 안 보는 경보가 되고, 그러면 진짜 10일 사고가
#: 왔을 때 그 화면은 이미 빨간 채로 방치돼 있다.
#:
#: 그래서 날짜 수가 아니라 **원본이 냈어야 할 값**과 견준다.
#: `reporting.sessions.fx_source_latest` 가 그 계산을 이미 갖고 있다 —
#: 브리핑이 "우리가 못 받은 것" 과 "원본이 아직 안 낸 것" 을 가르는 데 쓰는
#: 함수다. 판정을 두 곳에 두면 화면과 수집기가 다른 말을 한다.


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

    from quant_rl_trading.reporting.sessions import fx_source_latest

    newest = frame["valid_from"].max()
    age = (now - newest.to_pydatetime()).days
    expected = fx_source_latest(now)
    have = newest.date()
    print(f"창고 최신 환율 {have} · {age}일 전 · 원본이 냈어야 할 값 {expected}")

    # 1) 회계가 죽는 선. 원본이 늦든 우리 탓이든 여기까지 오면 비상이다.
    if age > NAV_BREAKS_AFTER_DAYS:
        print(
            f"환율이 {age}일 낡았다 — {NAV_BREAKS_AFTER_DAYS}일을 넘겼다. "
            "회계가 해외분 평가를 거부하고 NAV 가 통째로 멈춘다. "
            f"원본(FRED H.10)이 냈어야 할 마지막 값은 {expected} 다.",
            file=sys.stderr,
        )
        return False

    # 2) 원본은 냈는데 우리가 못 받았나. **이것만 우리 잘못이다.**
    if have < expected:
        print(
            f"환율이 {have} 까지인데 FRED 는 {expected} 까지 냈다 — "
            "원본 지연이 아니라 **수집이 밀린 것**이다.",
            file=sys.stderr,
        )
        return False

    # 원본이 아직 안 낸 것은 실패가 아니다. H.10 은 월요일 주간 발행이라
    # 금요일에는 최신값이 정상적으로 7일 전이다.
    return True


if __name__ == "__main__":
    raise SystemExit(main())
