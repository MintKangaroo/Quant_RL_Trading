"""유동시총 가중 + 종목 상한 — Z2 트랙의 비중(docs/design/portfolio-construction.md "Z2 트랙").

시행 Z 의 Z2(K200 구성종목 · 유동시총 가중 · 상한 10%)를 실전 파이프라인에 옮긴 것. 후보는 selector 가 이미 골랐다(랭커 상위 24,
완충) — 여기서는 **얼마씩 드나**만 정한다: 시가총액 × 유동비율에 비례, 한 종목 상한을 넘친 몫은 나머지에 비례해 다시 나눈다.
합은 ``1 − cash_buffer`` 다(다른 룰 베이스라인과 같다). 노출 배수·사이징은 호출부가 그대로 건다.

**점수는 안 쓴다.** 점수는 누구를 드나(선정)에만 쓰였다 — 시행 Z 의 Z2 가 그랬다. 점수 부호로 후보를 빼지도 않는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from quant_rl_trading.store import Store

MARKET_STATS = "market_stats"
FLOAT_RATIO = "float_ratio"
#: 시총 스냅샷을 찾는 창(달력일). 시총은 매일 들어오므로 짧게.
CAP_LOOKBACK_DAYS = 20
#: 유동비율은 드물게 바뀐다.
FLOAT_LOOKBACK_DAYS = 400
#: 시총을 아는 후보가 이보다 적으면 동일가중으로 물러선다(경로를 driver 로 남긴다).
MIN_CAPPED = 5


def capped(weights: pd.Series, limit: float) -> pd.Series:
    """합 1 인 비중에서 한 종목 상한을 넘친 몫을 나머지에 비례해 다시 나눈다. 수렴할 때까지."""
    w = weights / weights.sum()
    for _ in range(50):
        over = w > limit + 1e-12
        if not over.any():
            break
        excess = float((w[over] - limit).sum())
        w[over] = limit
        free = ~over & (w < limit)
        if not free.any():
            break
        w[free] += excess * w[free] / float(w[free].sum())
    return w


def allocate_float_cap(
    store: Store, *, as_of: datetime, market: str, candidates: Sequence[str], limit: float, cash_buffer: float,
) -> tuple[dict[str, float], str]:
    """(목표 비중, 경로). 경로는 ``float_cap`` 또는 ``float_cap:equal_fallback``."""
    names = list(dict.fromkeys(candidates))
    if not names:
        return {}, "float_cap"
    caps = store.get(MARKET_STATS, as_of=as_of, lookback=CAP_LOOKBACK_DAYS, until=as_of, market=market,
                     entity=names, columns=["entity_id", "metric", "value", "valid_from"])
    caps = caps[caps["metric"] == "market_cap"] if not caps.empty else caps
    cap = (caps.sort_values("valid_from").groupby("entity_id")["value"].last().astype(float)
           if not caps.empty else pd.Series(dtype=float))
    cap = cap[cap > 0]
    budget = 1.0 - cash_buffer
    if len(cap) < MIN_CAPPED:
        return {e: budget / len(names) for e in names}, "float_cap:equal_fallback"
    ratio = store.get(FLOAT_RATIO, as_of=as_of, lookback=FLOAT_LOOKBACK_DAYS, entity=list(cap.index),
                      columns=["entity_id", "float_ratio", "observed_at"])
    fr = (ratio.sort_values("observed_at").groupby("entity_id")["float_ratio"].last().astype(float)
          if not ratio.empty else pd.Series(dtype=float))
    # 유동비율을 모르는 종목은 **아는 종목의 중앙값**으로 — 시행 Z 와 같은 규칙. 0 으로 두면 그 종목을 안 사는 것이 되고,
    # 1 로 두면 대주주 지분이 큰 종목을 과대 가중한다.
    fr = fr.reindex(cap.index).fillna(float(fr.median()) if not fr.empty else 1.0).clip(lower=0.01, upper=1.0)
    w = capped(cap * fr, limit) * budget
    return {str(k): float(v) for k, v in w.items()}, "float_cap"
