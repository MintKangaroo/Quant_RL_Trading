"""KODEX200 대비 성과 — **판정 도구와 화면이 같은 수식을 쓰는 자리** (accounting.md §8.2).

종료 기준(``tools/verify_exit_criterion.py``) · 실자금 투입 관문 1(같은 데이터·비용 규약) · 대시보드 장부별 IR
패널이 전부 여기를 부른다. 한쪽이 수식을 따로 들면 11월에 "화면은 IR 이 양수였는데 판정은 음수" 가 나오고,
어느 쪽이 맞는지 가릴 방법이 없다.

**NAV 를 다시 계산하지 않는다.** 우리 쪽은 회계가 적은 TWR 누적지수(``nav_daily.index_value``)이고, 수수료·세금은
그 안에 이미 빠져 있다 — 그래서 여기서 낸 초과수익은 비용 차감 후다.

## 총수익 보정

ETF **가격**에는 운용보수가 들어 있지만 분배금은 빠져 있어 그만큼 우리가 이긴 것처럼 보인다.
``benchmark.kodex200_distribution_yield_annual`` 연율 가정을 **넘은 거래일 수**에 비례해 더한다(복리 아님 — 등록 문구).
장부에 결손일이 있어도 거래일로 세므로, 결손 구간의 ETF 수익은 다 세면서 분배금만 덜 붙는 일이 없다.

## 결손일은 한 걸음으로 잇는다

장부에 없는 거래일은 건너뛴 구간을 한 걸음으로 본다. TWR 지수는 결손을 넘어 이어지므로 그 걸음의 우리 수익은
결손 구간 전체이고, ETF 도 같은 구간을 잰다. 결손일 목록은 결과에 같이 싣는다 — 숨기면 그 걸음이 하루짜리로 읽힌다.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from itertools import pairwise
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from quant_rl_trading.accounting import ledger
from quant_rl_trading.collectors.benchmark_etf import BENCHMARK_ETF
from quant_rl_trading.collectors.market_hours import Market, trading_days

if TYPE_CHECKING:
    from quant_rl_trading.store import Store

#: 분배금 연율 가정의 config 키. 2026-09-18 사전등록 — 판정 결과를 보고 고치지 않는다.
YIELD_KEY = "benchmark.kodex200_distribution_yield_annual"
#: 연율 가정을 거래일로 나누고, IR·추적오차를 연환산한다. 국내 증시 연 거래일.
TRADING_DAYS_PER_YEAR = 245


def session_series(frame: pd.DataFrame, column: str) -> pd.Series:
    """한국 날짜 → 그날 마지막 ``valid_from`` 행의 값. 같은 날 여러 행(정정·재계산)이 있으면 마지막이 그날이다."""
    if frame.empty:
        return pd.Series(dtype=float)
    out = frame.copy()
    out["day"] = out["valid_from"].dt.tz_convert("Asia/Seoul").dt.date
    out = out.sort_values("valid_from").groupby("day", as_index=True).tail(1)
    return out.set_index("day")[column].astype(float)


def book_index(store: Store, *, as_of: datetime, lookback: int | None) -> pd.Series:
    """장부의 TWR 누적지수(세션 날짜 → ``index_value``). 회계가 적은 값 그대로다."""
    nav = store.get(ledger.NAV_DAILY, as_of=as_of, entity=ledger.ACCOUNT, lookback=lookback,
                    columns=["valid_from", "index_value"])
    return session_series(nav, "index_value")


def etf_close(source: Store, *, as_of: datetime, lookback: int | None) -> pd.Series:
    """KODEX200(069500) 종가(세션 날짜 → close). ``indices`` 표에 ``BENCHMARK_ETF`` 로 들어 있다."""
    bench = source.get("indices", as_of=as_of, lookback=lookback, entity=BENCHMARK_ETF,
                       columns=["valid_from", "close"])
    return session_series(bench, "close")


def distribution_yield(source: Store, *, as_of: datetime) -> float:
    """분배금 연율 가정. 없으면 ``ConfigNotFound``·``LookupError``·``ValueError`` 가 그대로 난다 — 기본값을 지어내지 않는다."""
    return float(source.config(YIELD_KEY, as_of=as_of))


def overlap(ours: pd.Series, etf: pd.Series, window: Sequence[date]) -> list[date]:
    """장부 ∩ ETF ∩ 창(거래일) — 비교할 수 있는 세션."""
    return sorted(set(ours.index) & set(etf.index) & set(window))


@dataclass(frozen=True)
class Relative:
    """한 창의 KODEX200 대비 성과. ``None`` 은 표본이 모자라 못 잰 것이다 — 0 이 아니다."""

    sessions: tuple[date, ...]
    #: 창 안에서 장부에 없는 거래일. 판정 도구가 "우리 장부에 없는 거래일 n개" 로 적는 그것이다.
    missing: tuple[date, ...]
    ours_total: float
    etf_price: float
    #: 이 창에 더한 분배금(연율 × 넘은 거래일 / 245).
    dividend: float
    etf_total: float
    excess: float
    our_mdd: float
    etf_mdd: float
    #: 걸음이 셋 이하면 None(판정 도구의 기존 조건 ``len(rb) > 2``).
    beta: float | None
    alpha: float | None
    #: 걸음이 둘 미만이거나 초과수익이 한 값뿐이면 None.
    ir: float | None
    tracking_error: float | None
    #: (세션, 그날까지의 누적 초과) — 첫 세션은 0. 화면의 추이 선이다.
    curve: tuple[tuple[date, float], ...]

    @property
    def steps(self) -> int:
        return max(len(self.sessions) - 1, 0)


def compare(
    ours: pd.Series,
    etf: pd.Series,
    *,
    window: Sequence[date],
    annual_yield: float,
    through: date | None = None,
    last: int | None = None,
) -> Relative | None:
    """창 ``window``(한국 거래일) 안에서 장부 대 KODEX200 총수익. 세션이 둘 미만이면 ``None``.

    - ``through`` — 결손일을 셀 마지막 날. 판정 도구는 ``min(오늘, 판정일)`` 을 준다.
    - ``last`` — 롤링 창: 겹치는 세션 중 마지막 N개. 결손일은 그 첫 세션부터 센다.
    """
    days = [d for d in window if through is None or d <= through]
    both = overlap(ours, etf, days)
    if last is not None:
        both = both[-last:]
        if both:
            days = [d for d in days if d >= both[0]]
    if len(both) < 2:
        return None
    held = set(ours.index)
    missing = tuple(d for d in days if d not in held)

    a = ours.loc[both] / ours.loc[both].iloc[0]
    b = etf.loc[both] / etf.loc[both].iloc[0]
    # 걸음마다 넘은 거래일 수. 합은 창의 (거래일 수 − 1) 이 된다 — 분배금 총액과 걸음별 몫이 같은 달력을 쓴다.
    calendar = trading_days(Market.KR, both[0], both[-1])
    position = {d: i for i, d in enumerate(calendar)}
    spans = np.array([position[cur] - position[prev] for prev, cur in pairwise(both)], dtype=float)
    spanned = len(calendar) - 1
    dividend = annual_yield * max(spanned, 0) / TRADING_DAYS_PER_YEAR
    ours_total = float(a.iloc[-1]) - 1.0
    etf_price = float(b.iloc[-1]) - 1.0
    etf_total = etf_price + dividend

    ra, rb = a.pct_change().dropna(), b.pct_change().dropna()
    beta = alpha = None
    if len(rb) > 2 and float(np.var(rb)) > 0:
        # **ddof 를 맞춘다.** `np.cov` 는 기본 ddof=1, `np.var` 는 ddof=0 이라 그냥 나누면
        # 베타가 n/(n−1) 만큼 부푼다(60세션이면 +1.7%). 사전등록한 수식은 표본 공분산/표본 분산이다.
        beta = float(np.cov(ra, rb, ddof=1)[0, 1] / np.var(rb, ddof=1))
        alpha = ours_total - beta * etf_total

    step_dividend = annual_yield * spans / TRADING_DAYS_PER_YEAR
    excess_steps = ra.to_numpy() - (rb.to_numpy() + step_dividend)
    ir = tracking = None
    if len(excess_steps) >= 2:
        spread = float(np.std(excess_steps, ddof=1))
        if spread > 0 and math.isfinite(spread):
            tracking = spread * math.sqrt(TRADING_DAYS_PER_YEAR)
            ir = float(np.mean(excess_steps)) / spread * math.sqrt(TRADING_DAYS_PER_YEAR)

    carried = np.concatenate([[0.0], np.cumsum(step_dividend)])
    curve = tuple(
        (day, float(a.iloc[i] - 1.0) - (float(b.iloc[i] - 1.0) + float(carried[i])))
        for i, day in enumerate(both)
    )
    return Relative(
        sessions=tuple(both),
        missing=missing,
        ours_total=ours_total,
        etf_price=etf_price,
        dividend=dividend,
        etf_total=etf_total,
        excess=ours_total - etf_total,
        our_mdd=float((a / a.cummax() - 1).min()),
        etf_mdd=float((b / b.cummax() - 1).min()),
        beta=beta,
        alpha=alpha,
        ir=ir,
        tracking_error=tracking,
        curve=curve,
    )
