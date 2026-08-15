"""주문 식별자와 분할 집행. 순수 코드.

## 멱등성 ⭐

client order id 를 ``(session_id, entity_id, slice_seq)`` 로 **결정론적으로**
만든다. 프로세스가 죽었다 살아나는 것은 가능성이 아니라 **반드시 일어나는
일**이고, 그때 같은 주문이 두 번 나가면 비중이 두 배가 된다.

무작위 UUID 를 쓰면 재시작한 프로세스는 자기가 이미 낸 주문을 알아볼 수 없다.
시각을 넣어도 같다 — 재시작하면 시각이 달라진다. **입력만으로 결정되는 id** 여야
재시작 후에도 같은 id 가 나오고, 창고와 증권사 양쪽에서 중복이 걸린다.

## 분할 집행

한 번에 던지면 충격비용이 커진다. ``slice_count`` 개로 나누고
``slice_interval_sec`` 간격으로 낸다. 나눈 조각의 합은 반드시 원 수량과
같아야 한다 — 나머지를 버리면 목표에 영영 도달하지 못한다.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from quant_rl_trading.executor.ticks import round_to_tick
from quant_rl_trading.schemas.order import Order, Side

if TYPE_CHECKING:
    from quant_rl_trading.store import Store

#: 증권사 주문번호 길이 제한을 넉넉히 밑돈다.
ORDER_ID_LENGTH = 24


def session_id(*, as_of: datetime, market: str) -> str:
    """하루의 실행 단위. **같은 as_of 는 같은 session_id 다** (리플레이 결정론)."""
    return f"{market}-{as_of.date().isoformat()}"


def client_order_id(*, session: str, entity_id: str, slice_seq: int) -> str:
    """결정론적 주문 식별자.

    해시를 쓰는 이유는 길이 때문이다. 종목코드와 세션을 그대로 이으면 증권사
    필드 길이를 넘길 수 있다. 같은 입력이 같은 출력을 주는 성질은 유지된다.
    """
    # **매수/매도가 씨앗에 없다.** 지금은 그래도 되는데, 그건
    # `sizing.py` 가 목표비중 차이(delta)의 부호로 방향을 정해
    # **한 세션에 한 종목은 한 방향뿐**이기 때문이다. 그 불변식이 이 함수를
    # 떠받치고 있다 — 언젠가 같은 세션에서 같은 종목을 사고팔 수 있게 되면
    # 두 주문의 id 가 같아지고, 멱등 처리가 뒤엣것을 앞엣것으로 착각해
    # **조용히 삼킨다**(실제로 검증 도구에서 그 증상이 났다). 그때는 side 를
    # 씨앗에 넣어야 하는데, 그러면 기존 주문의 id 가 전부 바뀌므로
    # `submit-{order_id}` 매니페스트도 같이 옮겨야 한다.
    seed = f"{session}|{entity_id}|{slice_seq}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return digest[:ORDER_ID_LENGTH]


@dataclass(frozen=True)
class SliceParams:
    slice_count: int
    slice_interval_sec: int
    max_slippage: float

    @classmethod
    def from_store(cls, store: Store, *, as_of: datetime) -> SliceParams:
        return cls(
            slice_count=int(store.config("execution.slice_count", as_of=as_of)),
            slice_interval_sec=int(
                store.config("execution.slice_interval_sec", as_of=as_of)
            ),
            max_slippage=float(store.config("execution.max_slippage", as_of=as_of)),
        )


def split(quantity: int, *, slices: int) -> list[int]:
    """수량을 조각으로. **합은 반드시 원 수량과 같다.**

    나머지를 버리면 목표 비중에 영영 도달하지 못하고, 그 차이가 매일 누적된다.
    앞 조각에 하나씩 얹어 나머지를 소진한다.
    """
    if slices <= 1 or quantity <= slices:
        return [quantity]
    base, remainder = divmod(quantity, slices)
    plan = [base + (1 if index < remainder else 0) for index in range(slices)]
    assert sum(plan) == quantity
    return [piece for piece in plan if piece > 0]


def limit_price(
    *, reference: float, side: Side, max_slippage: float, market: str = "KR"
) -> float:
    """지정가. **슬리피지 상한 안에서만 산다.**

    시장가는 청산과 킬스위치 발동 때만 쓴다 (execution.order_type). 평시에
    시장가를 쓰면 호가가 얇은 종목에서 백테스트 가정을 훨씬 넘는 가격에
    체결되고, 그 차이는 전부 성과에서 빠진다.

    상한 계산 값을 그대로 내면 거래소가 거부한다 — 호가단위(tick)에 맞는
    가격만 낼 수 있다. ``round_to_tick`` 이 상한을 넘지 않는 방향으로
    반올림한다(매수 내림·매도 올림, 근거는 ``ticks.py``).

    ``market`` 기본값이 ``"KR"`` 인 것은 기존 호출부 계약을 지키기 위해서다.
    **미장 주문에 이 기본값을 그대로 두면 원화 호가단위표로 반올림된다** —
    호출부가 반드시 ``market="US"`` 를 넘겨야 한다(``ticks.round_to_tick``).
    """
    edge = 1.0 + max_slippage if side is Side.BUY else 1.0 - max_slippage
    return round_to_tick(reference * edge, side=side, market=market)


@dataclass(frozen=True)
class PlannedOrder:
    """집행 직전의 주문. ``Order`` 에 실행 메타를 붙인 것."""

    order: Order
    order_id: str
    session_id: str
    slice_seq: int
    target_weight: float

    def row(self, *, as_of: datetime, observed_at: datetime, market: str, status: str) -> dict:
        return {
            "entity_id": self.order.entity_id,
            "valid_from": as_of,
            "observed_at": observed_at,
            "source": "executor",
            "market": market,
            "session_id": self.session_id,
            "slice_seq": self.slice_seq,
            "side": str(self.order.side),
            "quantity": float(self.order.quantity),
            "limit_price": self.order.limit_price,
            "target_weight": self.target_weight,
            "status": status,
            "reason": self.order.reason,
        }


def plan_slices(
    *,
    entity_id: str,
    side: Side,
    quantity: int,
    reference_price: float,
    target_weight: float,
    session: str,
    params: SliceParams,
    market_order: bool = False,
    market: str = "KR",
) -> list[PlannedOrder]:
    """한 종목의 분할 주문 묶음.

    ``market`` 은 호가단위 표만 가른다(``limit_price``) — 자연키·분할 규칙은
    시장과 무관하다. 기본값 ``"KR"`` 은 기존 호출부 계약을 지키기 위해서다.
    """
    out: list[PlannedOrder] = []
    for seq, piece in enumerate(split(quantity, slices=params.slice_count)):
        price = (
            None
            if market_order
            else limit_price(
                reference=reference_price,
                side=side,
                max_slippage=params.max_slippage,
                market=market,
            )
        )
        out.append(
            PlannedOrder(
                order=Order(
                    entity_id=entity_id,
                    side=side,
                    quantity=piece,
                    limit_price=price,
                    reason="청산·킬스위치 시장가" if market_order else "",
                ),
                order_id=client_order_id(
                    session=session, entity_id=entity_id, slice_seq=seq
                ),
                session_id=session,
                slice_seq=seq,
                target_weight=target_weight,
            )
        )
    return out
