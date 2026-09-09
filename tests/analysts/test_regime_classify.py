"""국면 분류 — crisis 문턱 (시행 R, 2026-09-07)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from quant_rl_trading.analysts.regime import classify


def _index(last_month_move: float, *, hot: bool) -> pd.Series:
    """200일 지수, 결정적. 잔잔 ±0.2% 교대; hot 이면 앞 140일 ±2%·뒤 60일 ±3% 교대(최근 변동성이 자기 분포 위).
    마지막 21일 수준을 한 번에 옮겨 21세션 모멘텀을 ``last_month_move`` 로 정확히 만든다."""
    if hot:
        r = [0.02 * (1 if i % 2 else -1) for i in range(140)] + [0.03 * (1 if i % 2 else -1) for i in range(60)]
    else:
        # 잔잔: 앞이 뒤보다 조금 더 요동(±0.4% → ±0.2%)이라 마지막 수준 이동 한 번이 분위를 못 넘는다
        r = [0.004 * (1 if i % 2 else -1) for i in range(140)] + [0.002 * (1 if i % 2 else -1) for i in range(60)]
    px = 100 * np.cumprod(1 + np.array(r))
    # classify 는 index[-1]/index[-21] 을 본다 — 기준점 px[-21] 은 두고 그 뒤 20개만 옮긴다
    factor = (1 + last_month_move) / (px[-1] / px[-21])
    px[-20:] *= factor
    return pd.Series(px)


def test_고변동_살짝_음수는_문턱_아래에서만_crisis() -> None:
    idx = _index(-0.006, hot=True)
    assert classify(idx, crisis_floor=0.0) == "crisis"        # 옛 규칙 — 부호 하나
    assert classify(idx, crisis_floor=-0.03) == "volatile"    # 시행 R — 폭 기준


def test_고변동_큰_음수는_문턱과_무관하게_crisis() -> None:
    idx = _index(-0.08, hot=True)
    assert classify(idx, crisis_floor=-0.03) == "crisis"


def test_저변동은_문턱과_무관() -> None:
    assert classify(_index(0.01, hot=False), crisis_floor=-0.03) == "bull"
    assert classify(_index(-0.01, hot=False), crisis_floor=-0.03) == "bear"
