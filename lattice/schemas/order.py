"""Order — Executor 가 내보내는 주문.

동결(frozen) 이다. 주문은 만들어진 뒤 바뀌지 않는다 — 변경 가능한 주문은
어느 시점의 값으로 체결됐는지 사후에 알 수 없게 만든다.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class Order(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    entity_id: str
    side: Side
    quantity: int = Field(gt=0)
    #: None 이면 시장가.
    limit_price: float | None = None
    #: 왜 이 주문이 나왔는지. Decision Trace 화면이 이걸 읽는다.
    reason: str = ""

    def canonical(self) -> dict[str, Any]:
        """직렬화 시 항상 같은 모양이 되도록 정규화한 표현."""
        return {
            "entity_id": self.entity_id,
            "side": str(self.side),
            "quantity": self.quantity,
            "limit_price": self.limit_price,
            "reason": self.reason,
        }
