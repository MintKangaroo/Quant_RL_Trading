"""Analyst 가중치 — **측정 결과에서만 온다.**

코드가 가중치를 정하지 않는다. `analyst_weights` 테이블에 적재된 IC 측정
결과를 as_of 로 읽는다. IC 0.03 을 통과하지 못한 Analyst 는 그 테이블에
가중치 0 으로 들어 있으므로, 합성에서 자동으로 빠진다 — **관찰 모드 Analyst 가
조용히 섞이는 경로는 없다** (selector.md §2).

시장별로 따로 읽는다. flow_kr 은 미장에 존재하지 않고, 국장 가중치를 미장에
그대로 쓰면 없는 Analyst 에 무게를 싣는 꼴이 된다.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lattice.store import Store

ANALYST_WEIGHTS = "analyst_weights"


def analyst_weights(
    store: Store, *, as_of: datetime, market: str, lookback: int = 400
) -> dict[str, float]:
    """{analyst: weight}. 같은 Analyst 가 여러 번 측정됐으면 **가장 늦은 것**.

    빈 dict 를 돌려줄 수 있다 — 아직 아무도 측정되지 않은 창고다. 그때 합성
    점수는 비고, 후보도 비어야 한다. **측정 없이 동일가중으로 때우지 않는다.**
    그건 관찰 모드 Analyst 에게 실제 가중치를 주는 것과 같다.
    """
    frame = store.get(ANALYST_WEIGHTS, as_of=as_of, lookback=lookback)
    if frame.empty:
        return {}
    frame = frame[frame["market"] == market]
    if frame.empty:
        return {}
    latest = frame.sort_values(["observed_at", "valid_from"]).groupby("entity_id").tail(1)
    return {
        str(row["entity_id"]): float(row["weight"])
        for row in latest.to_dict(orient="records")
        if float(row["weight"]) > 0.0
    }
