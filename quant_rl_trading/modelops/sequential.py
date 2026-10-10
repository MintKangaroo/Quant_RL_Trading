"""전방 장부 순차 검정 — 언제 봐도 유효한 신뢰구간(self-improvement.md §10 ④, 사용자 10/10).

짝 장부(처리 − 대조)의 **일 초과수익** 평균에 대한 asymptotic confidence sequence(Waudby-Smith·Wu·Ramdas·Karampatziakis·Mineiro 2021,
"Time-uniform central limit theory"): μ̂_t ± σ̂_t · √( 2(tρ²+1)/(t²ρ²) · log(√(tρ²+1)/α) ). 매일 다시 봐도 1종 오류가 α 를 넘지 않는다 —
62세션에서 억지로 끊지 않고 증거가 쌓이는 날 판정한다. ρ 는 t* 에서 폭이 가장 좁게(같은 논문의 근사).
일수익이 서로 독립에 가깝다는 가정이다(재조정 사이 보유가 겹쳐 약한 자기상관이 있다 — 구간이 조금 낙관적일 수 있다).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

TRADING_DAYS = 252


@dataclass(frozen=True)
class Sequence:
    n: int
    mean: float          # 일 초과 평균
    lower: float
    upper: float
    status: str          # "우세 확정" · "열위 확정" · "모름" · "표본 부족"

    @property
    def annual(self) -> tuple[float, float, float]:
        return self.mean * TRADING_DAYS, self.lower * TRADING_DAYS, self.upper * TRADING_DAYS


def rho_for(t_star: int, alpha: float) -> float:
    a = -2.0 * math.log(alpha)
    return math.sqrt((a + math.log(a + 1.0)) / t_star)


def confidence_sequence(diff: np.ndarray, *, alpha: float, t_star: int, min_n: int = 10) -> Sequence:
    x = np.asarray(diff, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < min_n:
        return Sequence(n, float(x.mean()) if n else float("nan"), float("nan"), float("nan"), "표본 부족")
    mu, sd = float(x.mean()), float(x.std(ddof=1))
    rho = rho_for(t_star, alpha)
    radius = sd * math.sqrt(2.0 * (n * rho ** 2 + 1.0) / (n ** 2 * rho ** 2) * math.log(math.sqrt(n * rho ** 2 + 1.0) / alpha))
    lo, hi = mu - radius, mu + radius
    status = "우세 확정" if lo > 0 else ("열위 확정" if hi < 0 else "모름")
    return Sequence(n, mu, lo, hi, status)
