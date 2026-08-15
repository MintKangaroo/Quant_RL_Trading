"""거부 성적표 — News·SNS 필터가 일을 했는지 사후에 따진다.

이 둘은 IC 로 검증할 수 없다. 점수가 아니라 판정이고, 과거 뉴스·SNS 를 시점
정합성 있게 확보할 수 없기 때문이다. **그래서 성적표가 유일한 검증 수단이다.**

성적표가 없으면 이 필터는 검증되지 않은 채로 매수만 막는 장치가 된다. 그리고
막힌 종목이 나중에 올랐는지 떨어졌는지는 **아무도 물어보지 않게 된다** — 그건
포트폴리오에 없으니 손익에 안 잡히고, 화면에도 안 나온다.

## 무엇을 재나

차단 기간(``as_of`` ~ ``expires_at``) 동안 **차단된 종목의 수익률 - 같은 날
시장 중앙값**. 음수면 필터가 손실을 피하게 해 준 것이고, 양수면 기회를 버린
것이다.

시장 중앙값을 빼는 이유는 시장이 통째로 빠진 날의 하락을 필터의 공으로
세지 않기 위해서다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from quant_rl_trading.store import Store
from quant_rl_trading.store.prices import read_prices

VERDICTS = "verdicts"


def _returns_between(
    prices: pd.DataFrame, start: datetime, end: datetime
) -> pd.Series:
    """구간 수익률. 종목별 첫 종가 → 마지막 종가."""
    window = prices[(prices["valid_from"] >= start) & (prices["valid_from"] <= end)]
    if window.empty:
        return pd.Series(dtype=float)
    ordered = window.sort_values("valid_from")
    grouped = ordered.groupby("entity_id")["close"]
    first, last = grouped.first(), grouped.last()
    return (last / first - 1.0).replace([np.inf, -np.inf], np.nan).dropna()


def evaluate_blocks(
    store: Store, *, as_of: datetime, lookback: int
) -> dict[str, Any]:
    """차단 건별 성적. 만료된 차단만 채점한다.

    아직 유효한 차단은 결과가 안 나왔으므로 넣지 않는다 — 진행 중인 것을
    성적에 넣으면 성적이 매일 흔들린다.
    """
    verdicts = store.get(VERDICTS, as_of=as_of, lookback=lookback)
    if verdicts.empty:
        return _empty()

    blocked = verdicts[verdicts["decision"] == "block"]
    settled = blocked[blocked["expires_at"] <= as_of]
    if settled.empty:
        return _empty(pending=len(blocked))

    prices = read_prices(store, as_of=as_of, lookback=lookback)
    if prices.empty:
        return _empty(pending=len(blocked))

    records: list[dict[str, Any]] = []
    for row in settled.to_dict(orient="records"):
        start, end = row["valid_from"], row["expires_at"]
        returns = _returns_between(prices, start, end)
        if returns.empty:
            continue
        entity = str(row["entity_id"])
        if entity not in returns.index:
            continue

        # 시장 중앙값을 빼야 시장이 통째로 빠진 날의 하락을 공으로 세지 않는다.
        excess = float(returns[entity] - returns.median())
        records.append(
            {
                "entity_id": entity,
                "analyst": str(row["analyst"]),
                "category": str(row["category"]),
                "blocked_at": start.isoformat(),
                "expired_at": end.isoformat(),
                "excess_return": excess,
                # 음수면 필터가 손실을 피하게 해 줬다.
                "helped": excess < 0,
            }
        )

    if not records:
        return _empty(pending=len(blocked))

    frame = pd.DataFrame(records)
    return {
        "settled": len(frame),
        "pending": int(len(blocked) - len(settled)),
        "hit_rate": float(frame["helped"].mean()),
        "mean_excess": float(frame["excess_return"].mean()),
        "median_excess": float(frame["excess_return"].median()),
        "by_category": [
            {
                "category": str(name),
                "count": len(group),
                "hit_rate": float(group["helped"].mean()),
                "mean_excess": float(group["excess_return"].mean()),
            }
            for name, group in frame.groupby("category")
        ],
        "worst_calls": frame.nlargest(5, "excess_return").to_dict(orient="records"),
    }


def _empty(pending: int = 0) -> dict[str, Any]:
    """채점할 것이 없다. **0 으로 채우지 않는다** — 0과 '모름'은 다른 사실이다."""
    return {
        "settled": 0,
        "pending": pending,
        "hit_rate": None,
        "mean_excess": None,
        "median_excess": None,
        "by_category": [],
        "worst_calls": [],
    }


__all__ = ["evaluate_blocks"]
