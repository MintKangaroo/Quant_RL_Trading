"""Analyst 가중치 — **측정 결과에서만 온다.**

코드가 가중치를 정하지 않는다. `analyst_weights` 테이블에 적재된 IC 측정
결과를 as_of 로 읽는다. IC 0.03 을 통과하지 못한 Analyst 는 그 테이블에
가중치 0 으로 들어 있으므로, 합성에서 자동으로 빠진다 — **관찰 모드 Analyst 가
조용히 섞이는 경로는 없다** (selector.md §2).

시장별로 따로 읽는다. flow_kr 은 미장에 존재하지 않고, 국장 가중치를 미장에
그대로 쓰면 없는 Analyst 에 무게를 싣는 꼴이 된다.

## 측정된 가중치와 알파 가중치는 다르다

`measured_weights` 는 창고에 적힌 그대로다. `analyst_weights` 는 거기서
**제약 Analyst 를 뺀 것**이고, 알파 합성이 쓰는 것은 이쪽이다
(`constraints.CONSTRAINT_ANALYSTS`, 태스크 #32). IC 가 높다는 것과 알파라는
것은 다른 말이다 — `risk` 는 IC 를 통과하고도 알파가 아니다.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from quant_rl_trading.selector.constraints import alpha_weights

if TYPE_CHECKING:
    from quant_rl_trading.store import Store

ANALYST_WEIGHTS = "analyst_weights"


def analyst_weights(
    store: Store, *, as_of: datetime, market: str, lookback: int = 400
) -> dict[str, float]:
    """**알파 합성이 쓰는** 가중치. 측정값에서 제약 Analyst 를 뺀 것.

    빈 dict 를 돌려줄 수 있다 — 아직 아무도 측정되지 않은 창고다. 그때 합성
    점수는 비고, 후보도 비어야 한다. **측정 없이 동일가중으로 때우지 않는다.**
    그건 관찰 모드 Analyst 에게 실제 가중치를 주는 것과 같다.
    """
    return alpha_weights(
        measured_weights(store, as_of=as_of, market=market, lookback=lookback)
    )


def measured_weights(
    store: Store, *, as_of: datetime, market: str, lookback: int = 400
) -> dict[str, float]:
    """{analyst: weight} — **창고에 적힌 그대로**. 제약 Analyst 도 들어 있다.

    같은 Analyst 가 여러 번 측정됐으면 **가장 늦은 것**. IC 측정 결과를 있는
    그대로 보고 싶은 곳(진화의 유전자 목록, 배치 비교 도구)이 쓴다. 알파
    합성에 이걸 쓰면 `risk` 가 다시 점수로 섞인다 — `analyst_weights` 를 써라.
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
