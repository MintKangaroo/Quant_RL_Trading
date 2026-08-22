"""종목 코드 → 이름. **창고에 묻는 자리는 여기 하나다.**

``universe`` 는 자연키마다 여러 관측이 쌓이는 표라, "지금 이름" 을 고르려면
``valid_from``·``observed_at`` 순으로 마지막 행을 골라야 한다. 그 규칙이
화면·메일에 각각 복사돼 있으면 언젠가 한쪽만 고쳐지고, 그때 같은 종목이
두 이름으로 나간다.

**이름이 없으면 채우지 않는다.** 호출부가 ``entity_id`` 를 그대로 쓰도록
빈 자리를 남긴다 — 지어낸 이름은 없는 것보다 나쁘다.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - 타입 전용
    from quant_rl_trading.store import Store

UNIVERSE = "universe"

#: 이름은 자주 안 바뀌지만 명단 갱신이 며칠 걸러 돌 수 있다. 창이 하루면
#: 갱신이 안 돈 날 이름이 통째로 사라진다.
LOOKBACK_DAYS = 10


def of(store: Store, *, as_of: datetime, entities: list[str]) -> dict[str, str]:
    if not entities:
        return {}
    # 정렬에 쓰는 열도 함께 요청한다. columns 로 좁히면 안 부른 열은 오지
    # 않고, 그때 정렬 키가 사라져 조용히 KeyError 로 죽는다.
    frame = store.get(
        UNIVERSE,
        as_of=as_of,
        entity=entities,
        lookback=LOOKBACK_DAYS,
        columns=["name", "valid_from", "observed_at"],
    )
    if frame.empty:
        return {}
    latest = frame.sort_values(["valid_from", "observed_at"]).groupby("entity_id").tail(1)
    return {
        str(row["entity_id"]): str(row["name"])
        for row in latest.to_dict(orient="records")
        if row.get("name")
    }
