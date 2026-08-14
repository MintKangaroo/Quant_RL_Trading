"""주문을 **실제로 내보내는 자리**. 여기까지가 Executor 의 끝이다.

지금까지 Executor 는 목표비중을 주문으로 바꿔 창고에 적는 데서 끝났다.
`orders.py` 가 분할·지정가·멱등 식별자까지 만들어 놓고도 그 주문을 아무도
보내지 않았다 — 실매매의 진짜 관문이 여기다.

## 이 모듈이 지키는 것

**1. Executor 안에 AI 가 없다** (불변식 6). 여기 있는 것은 전부 순수 코드다.
   무엇을 살지는 이미 정해져서 내려온다.

**2. 멱등성.** ``client_order_id`` 는 (session_id, entity_id, slice_seq) 로
   결정론적이다(`orders.client_order_id`). 프로세스가 죽었다 살아나는 것은
   반드시 일어나는 일이고, 그때 같은 주문이 두 번 나가면 안 된다. 그래서
   **보내기 전에 창고에 "보낸다" 를 먼저 적는다** — 적고 나서 죽으면 다음
   실행이 그 기록을 보고 건너뛴다. 반대 순서로 하면 보낸 뒤 죽었을 때
   기록이 없어 또 보낸다. 중복 주문은 되돌릴 수 없다.

**3. 기본은 보내지 않는 것.** ``live_trading`` 이 꺼져 있으면
   `PaperBroker` 가 들어온다. 실전 전송은 명시적으로 켤 때만 일어난다.

## 왜 Protocol 인가

백테스트·shadow·실전이 **같은 코드를 타야 한다** (불변식 5). 분기를 파이프라인
안에 두면 "shadow 에서는 됐는데" 가 생긴다. 그래서 파이프라인은 ``Broker``
하나만 알고, 무엇을 끼우느냐로 모드가 갈린다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

__all__ = [
    "Ack",
    "Broker",
    "BrokerError",
    "Fill",
    "PaperBroker",
    "RejectedOrder",
]


class BrokerError(RuntimeError):
    """전송 자체가 실패했다. **주문이 나갔는지 모르는 상태를 포함한다.**

    네트워크가 끊긴 경우 거래소에는 들어갔는데 응답만 못 받았을 수 있다.
    그래서 이 예외를 잡은 쪽은 **재전송하면 안 된다** — 체결 조회로
    사실을 확인하는 것이 유일하게 안전한 복구다.
    """


class RejectedOrder(BrokerError):
    """거래소가 받지 않았다. 이건 확실히 안 나간 것이라 다시 낼 수 있다."""


@dataclass(frozen=True)
class Ack:
    """전송 결과 한 건.

    ``broker_order_no`` 는 정정·취소에 필요하다. 이게 없으면 낸 주문을
    되돌릴 방법이 없으므로, 받으면 반드시 창고에 남긴다.
    """

    order_id: str
    accepted: bool
    broker_order_no: str | None = None
    rsp_cd: str | None = None
    rsp_msg: str | None = None
    #: 보내지 않은 경우(paper). 기록에 "냈다" 로 남으면 안 된다.
    sent: bool = True
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Fill:
    """체결 한 건. **부분체결이 정상 경로다** (M3-kickoff 3-3).

    수량을 다 못 채운 것은 사고가 아니라 흔한 일이다. 전량 체결만 정상으로
    다루면 부분체결이 예외 경로로 새고, 장부가 실제 보유와 어긋난다.
    """

    order_id: str
    entity_id: str
    side: str
    quantity: float
    price: float
    filled_at: datetime
    fee: float = 0.0
    tax: float = 0.0
    broker_order_no: str | None = None


class Broker(Protocol):
    """주문 창구. 구현은 셋이다 — paper · shadow · 실전(LS)."""

    def submit(self, order: Any, *, as_of: datetime) -> Ack:
        """신규 주문. ``order`` 는 ``executor.orders.PlannedOrder`` 다."""
        ...

    def cancel(self, *, broker_order_no: str, entity_id: str, quantity: int) -> Ack:
        """미체결 잔량 취소."""
        ...

    def modify(
        self, *, broker_order_no: str, entity_id: str, quantity: int, price: float
    ) -> Ack:
        """재호가(정정). 취소 후 재주문보다 우선한다 — 사이에 시세가 뛰면
        취소만 되고 못 사는 구간이 생긴다."""
        ...


@dataclass
class PaperBroker:
    """아무것도 보내지 않는다. **기본값이다.**

    받은 주문을 그대로 승인 처리하되 ``sent=False`` 로 표시한다. 이걸
    ``sent=True`` 로 적으면 shadow 성적표가 실거래로 읽힌다.
    """

    acks: list[Ack] = field(default_factory=list)

    def submit(self, order: Any, *, as_of: datetime) -> Ack:
        ack = Ack(
            order_id=order.order_id,
            accepted=True,
            sent=False,
            rsp_msg="paper — 전송하지 않았다",
        )
        self.acks.append(ack)
        return ack

    def cancel(self, *, broker_order_no: str, entity_id: str, quantity: int) -> Ack:
        return Ack(order_id=broker_order_no, accepted=True, sent=False)

    def modify(
        self, *, broker_order_no: str, entity_id: str, quantity: int, price: float
    ) -> Ack:
        return Ack(order_id=broker_order_no, accepted=True, sent=False)
