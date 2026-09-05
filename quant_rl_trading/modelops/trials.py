"""시행 대장과 Deflated Sharpe — 자기개선 안전장치 ③ (self-improvement.md §1).

같은 5년 데이터에 가설을 N 번 두드리면, 신호가 전혀 없어도 그중 최고는
반드시 좋아 보인다. 그래서 **모든 시행을 하나의 카운터에 누적**하고,
성과의 유의성은 그 N 으로 보정해서(DSR) 읽는다.

카운터는 `research_trials` 표의 ``n_trials`` 합이다. 실험별 리셋은 없다 —
리셋하는 순간 "이번 실험은 처음"이라는 거짓말이 시작된다.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

import numpy as np

from quant_rl_trading.store import Store

TRIALS_TABLE = "research_trials"
HOLDOUT_TABLE = "holdout_access"

#: 오일러-마스케로니 상수 — E[max] 근사(Bailey & López de Prado 2014)에 쓴다.
_EULER_GAMMA = 0.5772156649015329


def cumulative_trials(store: Store, *, as_of: datetime) -> int:
    """지금까지의 누적 시행 수. 0 이면 대장이 비었다는 뜻이지 시행이 없었다는
    뜻이 아닐 수 있다 — 소급 집계(`tools/tally_trials.py`)가 선행돼야 한다."""
    frame = store.get(TRIALS_TABLE, as_of=as_of)
    if frame.empty:
        return 0
    return int(frame["n_trials"].sum())


def trials_by_family(store: Store, *, as_of: datetime) -> dict[str, int]:
    frame = store.get(TRIALS_TABLE, as_of=as_of)
    if frame.empty:
        return {}
    grouped = frame.groupby("family")["n_trials"].sum()
    return {str(k): int(v) for k, v in grouped.items()}


def quarter_trials(store: Store, *, as_of: datetime) -> int:
    """이번 분기 시행 수 — 분기 예산(`research.trial_budget_quarter`)과 대조."""
    frame = store.get(TRIALS_TABLE, as_of=as_of)
    if frame.empty:
        return 0
    quarter_start = datetime(
        as_of.year, ((as_of.month - 1) // 3) * 3 + 1, 1, tzinfo=as_of.tzinfo
    )
    recent = frame[frame["valid_from"] >= quarter_start]
    return int(recent["n_trials"].sum()) if not recent.empty else 0


def expected_max_sharpe(n_trials: int, variance_sr: float) -> float:
    """N 번 시도했을 때 **운만으로** 기대되는 최고 샤프.

    Bailey & López de Prado (2014) 의 근사. 이것보다 낮은 샤프는 N 번 던진
    동전의 최고 기록과 구분되지 않는다.
    """
    if n_trials <= 1:
        return 0.0
    from scipy.stats import norm

    q1 = norm.ppf(1.0 - 1.0 / n_trials)
    q2 = norm.ppf(1.0 - 1.0 / (n_trials * math.e))
    return float(math.sqrt(variance_sr) * ((1.0 - _EULER_GAMMA) * q1 + _EULER_GAMMA * q2))


def deflated_sharpe(
    returns: "np.ndarray | Any", *, n_trials: int
) -> dict[str, float] | None:
    """일별 수익률 → 시행 보정 유의성(DSR = PSR(E[maxSR])).

    반환: sharpe(비연율), expected_max(운의 상한), dsr(0~1 확률).
    dsr 은 "이 샤프가 N 번 시행의 운 최고치를 진짜로 넘었을 확률"이다.
    표본 30일 미만이면 None — 숫자를 지어내지 않는다.
    """
    values = np.asarray(returns, dtype=float)
    values = values[np.isfinite(values)]
    length = values.size
    if length < 30:
        return None
    std = float(values.std(ddof=1))
    if std <= 0.0:
        return None
    sharpe = float(values.mean()) / std
    from scipy.stats import kurtosis, norm, skew

    gamma3 = float(skew(values))
    gamma4 = float(kurtosis(values, fisher=False))
    # 후보 샤프의 분산 — 후보별 성적 분포가 없으므로 추정 샤프 자체의
    # 표본 분산을 대용한다(같은 논문의 관행적 보수화).
    variance_sr = (1.0 - gamma3 * sharpe + (gamma4 - 1.0) / 4.0 * sharpe**2) / (length - 1)
    variance_sr = max(variance_sr, 1e-12)
    benchmark = expected_max_sharpe(max(n_trials, 1), variance_sr)
    numerator = (sharpe - benchmark) * math.sqrt(length - 1)
    denominator = math.sqrt(1.0 - gamma3 * sharpe + (gamma4 - 1.0) / 4.0 * sharpe**2)
    if denominator <= 0.0:
        return None
    dsr = float(norm.cdf(numerator / denominator))
    return {
        "sharpe": sharpe,
        "expected_max": benchmark,
        "dsr": dsr,
        "sample_days": float(length),
    }
