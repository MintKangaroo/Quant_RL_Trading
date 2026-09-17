"""종료 기준 판정 — 모의계좌 60거래일 (milestones.md "종료 기준", 2026-08-30 사전등록).

    .venv/bin/python tools/verify_exit_criterion.py            # 오늘까지 중간 집계
    .venv/bin/python tools/verify_exit_criterion.py --final    # 판정일 창으로 고정

등록된 두 조건이 **동시에** 참이면 실전 투입을 중단하고 프로젝트를 재정의한다.

    ① KODEX200(069500) 대비 비용 차감 후 초과수익 ≤ 0
    ② 낙폭 개선 없음 — 우리 MDD ≥ KODEX200 MDD

**기준은 결과를 보고 바꾸지 않는다.** 이 도구가 하는 일은 등록된 수식을 그대로 계산하는
것뿐이고, 무엇을 볼지 고르는 자리는 없다.

## 함께 적는 것 — 관문이 아니다

베타와 **베타 보정 알파**를 병기한다(2026-09-18 사전등록). 낮은 노출로 덜 잃은 것과 종목을
잘 골라 덜 잃은 것은 다른 사실인데 위 두 조건은 그것을 못 가른다. 다만 **판정에는 넣지
않는다** — 등록 뒤에 결과를 12세션 보고 나서 관문을 더하면, 옳은 변경이어도 사후 해석과
구분되지 않는다. 수식을 지금 고정해 두는 것이 11월에 고르지 않기 위한 장치다.

    beta  = cov(우리 일별수익, ETF 일별수익) / var(ETF 일별수익)
    alpha = (우리 누적) − beta × (ETF 누적)

## 총수익 보정

ETF **가격**이라 운용보수는 값 안에 있지만 분배금은 빠져 있고, 그만큼 **우리가 이긴 것처럼
보인다.** `benchmark.kodex200_distribution_yield_annual` 로 연율 가정을 거래일 비례 배분해
더한 값을 함께 낸다. 판정은 등록 문구대로 **총수익(보정본)** 으로 한다 — 보정은 우리에게
불리한 쪽이라 자기비판적인 선택이다.

종료코드: 0 계산 완료 · 2 계산 불가(데이터 부족). **판정 결과는 rc 가 아니라 글로 낸다** —
rc 는 "쟀나" 이지 "이겼나" 가 아니다(주간 IC 에서 같은 실수를 했다).
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.collectors.benchmark_etf import BENCHMARK_ETF  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market, trading_days  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from quant_rl_trading.store.errors import ConfigNotFound  # noqa: E402

#: 사전등록된 판정 창. **여기를 고치면 사후 해석이다.**
START = date(2026, 8, 28)
SESSIONS = 60
YIELD_KEY = "benchmark.kodex200_distribution_yield_annual"
#: 연율 가정을 거래일로 나눈다. 국내 증시 연 거래일.
TRADING_DAYS_PER_YEAR = 245


def judgment_day() -> date:
    days = trading_days(Market.KR, START, date(START.year + 1, 6, 30))
    return days[SESSIONS - 1]


def _series(frame: pd.DataFrame, column: str) -> pd.Series:
    out = frame.copy()
    out["day"] = out["valid_from"].dt.tz_convert("Asia/Seoul").dt.date
    out = out.sort_values("valid_from").groupby("day", as_index=True).tail(1)
    return out.set_index("day")[column].astype(float)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sandbox", default="data/_paper")
    parser.add_argument("--final", action="store_true", help="판정일 창으로 고정")
    args = parser.parse_args(argv)

    clock = LiveClock()
    now = clock.now()
    end = judgment_day()
    final = args.final or now.date() >= end
    window = [d for d in trading_days(Market.KR, START, end)]
    print(f"판정 창 {START} ~ {end} · 거래일 {len(window)}  ({'판정' if final else '중간 집계'})")

    book = Store(root=Path(args.sandbox))
    source = Store(root=Path("data"))
    nav = book.get("nav_daily", as_of=now, lookback=200)
    if nav.empty:
        print("nav_daily 가 비었다 — 계산 불가", file=sys.stderr)
        return 2
    ours = _series(nav, "index_value")
    bench = source.get("indices", as_of=now, lookback=200, entity=BENCHMARK_ETF,
                       columns=["valid_from", "close"])
    if bench.empty:
        print(f"{BENCHMARK_ETF} 가 창고에 없다 — 대조군 없이는 판정할 수 없다", file=sys.stderr)
        return 2
    etf = _series(bench, "close")

    both = sorted(set(ours.index) & set(etf.index) & set(window))
    missing = [d for d in window if d <= min(now.date(), end) and d not in set(ours.index)]
    if len(both) < 2:
        print(f"겹치는 세션이 {len(both)}개 — 계산 불가", file=sys.stderr)
        return 2
    a = ours.loc[both] / ours.loc[both].iloc[0]
    b = etf.loc[both] / etf.loc[both].iloc[0]

    try:
        annual = float(source.config(YIELD_KEY, as_of=now))
    except (ConfigNotFound, LookupError, ValueError):
        print(f"{YIELD_KEY} 가 창고에 없다 — 분배금 보정 없이는 등록 문구(총수익)를 못 맞춘다",
              file=sys.stderr)
        return 2
    # 분배금은 거래일 비례로 더한다. ETF 가격에는 보수가 이미 들어 있다.
    dividend = annual * (len(both) - 1) / TRADING_DAYS_PER_YEAR
    ours_total = float(a.iloc[-1]) - 1.0
    etf_price = float(b.iloc[-1]) - 1.0
    etf_total = etf_price + dividend

    our_mdd = float((a / a.cummax() - 1).min())
    etf_mdd = float((b / b.cummax() - 1).min())
    excess = ours_total - etf_total

    print(f"세션 {len(both)}/{SESSIONS}" + (f" · 우리 장부에 없는 거래일 {len(missing)}개" if missing else ""))
    print(f"  우리            {ours_total * 100:+7.2f}%   MDD {our_mdd * 100:7.2f}%")
    print(f"  KODEX200 가격   {etf_price * 100:+7.2f}%   MDD {etf_mdd * 100:7.2f}%")
    print(f"  KODEX200 총수익 {etf_total * 100:+7.2f}%   (분배금 가정 연 {annual * 100:.2f}% → 이 창 {dividend * 100:+.2f}%p)")
    one = excess <= 0
    two = our_mdd <= etf_mdd  # MDD 는 음수 — 우리 값이 더 작으면(더 깊으면) 개선 없음
    print(f"\n① 초과수익 {excess * 100:+.2f}%p → {'중단 쪽(≤0)' if one else '계속 쪽(>0)'}")
    print(f"② 낙폭     우리 {our_mdd * 100:.2f}% vs ETF {etf_mdd * 100:.2f}% → "
          f"{'중단 쪽(개선 없음)' if two else '계속 쪽(개선)'}")
    verdict = "중단·재정의" if (one and two) else "계속"
    print(f"\n판정: **{verdict}**  (둘 다 참일 때만 중단)")

    ra, rb = a.pct_change().dropna(), b.pct_change().dropna()
    if len(rb) > 2 and float(np.var(rb)) > 0:
        beta = float(np.cov(ra, rb)[0, 1] / np.var(rb))
        alpha = ours_total - beta * etf_total
        print(f"\n참고(관문 아님) · 베타 {beta:.2f} · 베타 보정 알파 {alpha * 100:+.2f}%p")
        print("  노출을 걷어내면 무엇이 남나. 2026-09-18 사전등록 — 판정에는 넣지 않는다.")
    if not final:
        print("\n아직 판정일이 아니다. 이 숫자로 기준을 고치지 않는다(사전등록 원칙).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
