"""AI v2 S1 — 포트폴리오 수준 시뮬레이터 (docs/design/ai-architecture-v2.md §2 L3).

L3(강화학습·밴딧·규칙)이 매일 내는 행동은 둘이다: 노출 k ∈ [k_min, 1] 과 재조정 b ∈ {0,1}. 종목 비중은 L0·L1 이 정한 **목표 비중**
(세션 × 종목)을 그대로 쓴다. 시뮬레이터는 실전과 같은 규칙을 따른다 —

- 재조정일(b=1)엔 목표 비중으로 맞춘다(주식 몫 = k). 보유일(b=0)엔 명단·상대 비중을 드리프트한 채 두고, 노출이 바뀌었으면
  크기만 바꾼다 — `session/daily.py` 보유일 분기와 같다(selector.md §5 7번).
- 노출은 `selector.exposure.apply` 와 같은 뜻(곱하고 정규화하지 않는다 — 줄인 몫은 현금, 수익 0).
- 비용 = 편도 × 회전(½ 이 아니라 Σ|Δw| — 시행 도구들과 같은 정의).

옛 `allocator/env.py` LatticeEnv 는 쓰지 않는다 — 완충·재조정 주기·노출이 빠져 실전과 다른 게임이었다(경로 분석 2026-09-25).
행동은 **그날 개장 전**에 정해지고 수익은 t+1→t+2(시행 도구와 같은 규약)라 미래를 보지 않는다.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

ANN = 252


@dataclass
class State:
    """정책이 보는 것 — 전부 그날 개장 전에 알 수 있는 값."""

    day: object
    step: int
    since_rebalance: int
    exposure: float            # 직전 노출 k
    drift_gap: float           # ½Σ|목표 − 지금|(주식 몫 기준) — 재조정 압력
    drawdown: float            # 포트 NAV 낙폭
    bench_ret20: float         # 벤치 20세션 수익(그날까지)
    bench_vol20: float
    extra: dict = field(default_factory=dict)   # U1 국면 확률·미장 선행 등, 호출자가 채운다


Policy = Callable[[State], tuple[float, bool]]


@dataclass(frozen=True)
class Result:
    daily: pd.Series           # 비용 후 일수익
    exposure: pd.Series        # 그날 적용한 k
    rebalanced: pd.Series      # 그날 재조정했나
    turnover: float            # 연율 회전(Σ|Δw|)


def simulate(targets: pd.DataFrame, ret: pd.DataFrame, bench: pd.Series, cost: float, policy: Policy, *,
             k_min: float = 0.30, extra: Callable[[object], dict] | None = None) -> Result:
    """targets: 세션 × 종목 목표 비중(합 1, 주식만) · ret: t+1→t+2 수익 · bench: 같은 규약의 벤치 수익."""
    days = [d for d in targets.index if d in ret.index]
    prev: pd.Series | None = None          # 지금 비중(주식, 현금은 1 − 합)
    nav, peak = 1.0, 1.0
    k_prev, since = 1.0, 0
    out, ks, rb, turns = {}, {}, {}, []
    bhist: list[float] = []
    for i, day in enumerate(days):
        tgt = targets.loc[day].dropna()
        tgt = tgt[tgt > 0]
        cur_equity = float(prev.sum()) if prev is not None else 0.0
        gap = 0.5 * float((tgt - (prev / cur_equity if cur_equity > 0 else prev)).abs().sum()) if prev is not None and len(tgt) and cur_equity > 0 else 1.0
        b20 = np.array(bhist[-20:]) if bhist else np.array([0.0])
        state = State(day=day, step=i, since_rebalance=since, exposure=k_prev, drift_gap=gap,
                      drawdown=nav / peak - 1.0, bench_ret20=float(np.prod(1 + b20) - 1), bench_vol20=float(b20.std() * np.sqrt(ANN)),
                      extra=extra(day) if extra else {})
        k, b = policy(state)
        k = float(min(1.0, max(k_min, k)))
        if prev is None or b:
            w = tgt / tgt.sum() * k if len(tgt) else pd.Series(dtype=float)
            since = 0
            b = True
        elif abs(k - k_prev) > 1e-12 and cur_equity > 0:
            w = prev / cur_equity * k
            since += 1
        else:
            w = prev
            since += 1
        dr = ret.loc[day].reindex(w.index).fillna(0.0)
        t = float(w.subtract(prev, fill_value=0.0).abs().sum()) if prev is not None else float(w.sum())
        r = float((w * dr).sum() - cost * t)
        out[day], ks[day], rb[day] = r, k, bool(b)
        turns.append(t)
        nav *= 1 + r
        peak = max(peak, nav)
        d = w * (1 + dr)
        total = 1.0 - float(w.sum()) + float(d.sum())
        prev = d / total if total > 0 else w
        k_prev = k
        bhist.append(float(bench.get(day, 0.0)) if not pd.isna(bench.get(day, np.nan)) else 0.0)
    return Result(pd.Series(out), pd.Series(ks), pd.Series(rb), float(np.mean(turns) * ANN) if turns else 0.0)


# --------------------------------------------------------------------------- 규칙 정책(대조)


def fixed(every: int = 10, k: float = 1.0) -> Policy:
    """노출 고정 · every 세션마다 재조정(AO C10 = every 10)."""
    return lambda s: (k, s.step % every == 0)


def regime_rule(scale_of: Callable[[State], float], every: int = 10) -> Policy:
    """국면 배수 규칙(V6 대응) — scale_of 가 그날 국면에서 배수를 준다(확인 기간은 호출자가 반영)."""
    return lambda s: (scale_of(s), s.step % every == 0)
