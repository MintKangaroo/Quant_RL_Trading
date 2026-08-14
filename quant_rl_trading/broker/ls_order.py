"""LS 증권으로 주문을 실제로 내보내는 창구. ``Broker`` Protocol 구현.

port(spec only, 코드는 새로 짬): Invest_KOREA_Stock_Project/ls_kr_rl_trader/broker/ls_client.py
의 ``place_order``/``modify_order``/``cancel_order`` — TR 코드·필드명·
``BnsTpCode`` 방향만 가져왔다. 재시도·레이트리밋·토큰 관리는 이미
``LSClient`` 가 한다. 이 파일은 그 위에서 **비즈니스 규약**(전송 여부 판단,
멱등성, 오류 번역)만 얹는다.

## 두 개의 live_trading, 그리고 둘의 관계

``execution.live_trading`` (store 설정)과 ``LSClient.live_trading`` (전송
계층 플래그)은 **의도적으로 별개다.** 하나로 합치면 "실수로 켜지는 경로가
하나도 없어야 한다"를 지킬 수 없다.

- ``execution.live_trading`` 은 **비즈니스 결정**이다 — 오늘 이 세션에서
  실전 매매를 승인했는가. ``as_of`` 시점의 설정값으로, 리플레이에서도
  그날 켜져 있었는지를 그대로 재현한다.
- ``LSClient.live_trading`` 은 **전송 계층의 물리적 게이트**다. 꺼져 있으면
  ``PAPER_ALLOWED_TR`` 밖의 TR(주문 TR 전부)은 네트워크에 닿지도 않고
  ``{"paper": True, ...}`` 를 돌려준다.

``LSBroker`` 는 **둘 다** 확인한다. 하나만 보면 실수로 켜지는 경로가 생긴다:

1. 파이프라인 배선 실수로 ``LSClient.live_trading=True`` 인 클라이언트를
   ``execution.live_trading=False`` 인 세션에 물렸다면 — store 설정을 먼저
   확인해 여기서 막는다. 네트워크 호출 자체를 하지 않는다(레이트리밋 슬롯도
   아낀다).
2. 반대로 ``execution.live_trading=True`` 인데 안전을 위해 paper
   ``LSClient`` 가 꽂혀 있다면 — ``request_tr`` 이 돌려주는
   ``{"paper": True}`` 응답을 여기서 감지해 ``sent=False`` 로 기록한다.
   client 의 판단을 덮어쓰지 않는다.

**둘 다 True 여야만 실제로 나간다.** 이게 이 클래스가 지키는 유일한 안전
불변식이다.

## LSAPIError 번역 — BrokerError 대 RejectedOrder

``LSAPIError.rsp_cd`` 가 채워지는 경로는 딱 하나다: ``LSClient.request_tr``
이 **거래소가 실제로 응답한 rsp_cd** 를 읽고 ``OK_CODES`` 에도 없고 완료
메시지도 아닐 때(`quant_rl_trading/collectors/ls_client.py:349`). 이건
거래소가 요청을 받아서 "안 된다"고 답한 것이다 — 확실히 안 나갔다.
→ ``RejectedOrder`` 로 번역한다. 재전송해도 안전하다.

그 외의 모든 경로(OAuth 실패, HTTP != 200, JSON 파싱 실패, 네트워크 오류,
"재시도 소진")는 ``rsp_cd=None`` 인 채로 올라온다. 이 경우 요청이 게이트웨이
어디까지 갔는지, 거래소가 실제로 받았는지 알 수 없다 — 응답만 못 받았을
뿐 주문은 들어갔을 수 있다. → ``BrokerError`` 로 번역한다. 이건 재전송하면
안 되고, 체결 조회로 사실을 확인해야 한다(``BrokerError`` 문서 그대로).

## 멱등성

``client_order_id`` 는 (session_id, entity_id, slice_seq) 로 결정론적이다
(``executor/orders.py``). "보낸 기록"을 창고에 남기는 일은 파이프라인
배선의 몫이지만, ``LSBroker`` 는 **자기 안에서도** 같은 order_id 로 두 번
불렸을 때 두 번 나가면 안 된다 — 프로세스 하나가 살아있는 동안의 재시도나
호출부 실수까지 창고 기록에만 의존하면, 그 기록이 아직 쓰이기 전(직전에
죽었다 그 자리에서 바로 재시도하는 경우)에 중복 전송이 생길 수 있다.
그래서 성공한 ``Ack`` 를 메모리에 캐시하고, 같은 order_id 가 다시 오면
그 캐시를 그대로 돌려주고 **다시 전송하지 않는다.** 이건 창고 기록을
대신하지 않는다 — 프로세스가 죽으면 캐시도 같이 사라진다. 그건 파이프라인이
"적고 나서 보낸다" 순서로 메꾸는 몫이다(``executor/orders.py`` 문서 참고).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from quant_rl_trading.broker import Ack, BrokerError, RejectedOrder
from quant_rl_trading.collectors.errors import LSAPIError
from quant_rl_trading.collectors.ls_client import PATH_ORDER, LSClient, isu_code
from quant_rl_trading.schemas.order import Side

if TYPE_CHECKING:
    from quant_rl_trading.executor.orders import PlannedOrder
    from quant_rl_trading.store import Store

#: 신규 주문. port: LS_KR ls_client.py place_order.
TR_NEW = "CSPAT00601"
#: 정정. port: LS_KR ls_client.py modify_order.
TR_MODIFY = "CSPAT00701"
#: 취소. port: LS_KR ls_client.py cancel_order.
TR_CANCEL = "CSPAT00801"

#: BnsTpCode. **틀리면 반대로 산다.** port: LS_KR ls_client.py:521
#: (``bns_tp = "2" if side == "buy" else "1"``).
BNS_BUY = "2"
BNS_SELL = "1"

#: OrdprcPtnCode. 지정가가 기본, 시장가는 청산·킬스위치에만
#: (``executor/orders.py`` limit_price 문서, ``execution.order_type``).
ORD_PTN_LIMIT = "00"
ORD_PTN_MARKET = "03"

#: 신용거래 아님(보통 매매). 이 시스템은 신용거래를 다루지 않는다.
MGNTRN_NONE = "000"
#: 주문조건 없음(0=없음, 1=IOC, 2=FOK). 분할집행은 이미 슬라이스로 물량을
#: 쪼개 놓았으므로 IOC/FOK 로 한 번 더 조이지 않는다.
ORD_CNDI_NONE = "0"


def _order_body(
    *, symbol: str, side: Side, quantity: int, limit_price: float | None
) -> dict[str, Any]:
    """CSPAT00601InBlock1. 시장가는 ``limit_price is None`` 으로 판별한다
    (``PlannedOrder.order.limit_price`` — ``executor/orders.py`` plan_slices)."""
    market = limit_price is None
    return {
        "CSPAT00601InBlock1": {
            "IsuNo": isu_code(symbol),
            "OrdQty": int(quantity),
            "OrdPrc": int(limit_price) if limit_price is not None else 0,
            "BnsTpCode": BNS_BUY if side is Side.BUY else BNS_SELL,
            "OrdprcPtnCode": ORD_PTN_MARKET if market else ORD_PTN_LIMIT,
            "MgntrnCode": MGNTRN_NONE,
            "LoanDt": "",
            "OrdCndiTpCode": ORD_CNDI_NONE,
        }
    }


def _ord_no(data: dict[str, Any]) -> str | None:
    """OutBlock2 우선, 없으면 OutBlock1. port: LS_KR broker/order.py:659-662.

    **둘 다 없어도 예외를 던지지 않는다.** "타임아웃/OrdNo 누락은 거부가
    아니라 모름이다" — 거래소는 이미 받았을 수 있다. 호출부가 ``Ack`` 의
    ``broker_order_no is None`` 을 보고 체결 조회로 재확인해야 한다.
    """
    out2 = data.get("CSPAT00601OutBlock2") or data.get("CSPAT00701OutBlock2") or {}
    out1 = data.get("CSPAT00601OutBlock1") or data.get("CSPAT00701OutBlock1") or {}
    no = out2.get("OrdNo") or out1.get("OrdNo")
    return str(no) if no else None


@dataclass
class LSBroker:
    """LS 증권 실전 창구. ``execution.live_trading`` 이 꺼져 있으면 전송하지 않는다."""

    client: LSClient
    store: Store
    #: 성공 전송한 order_id → Ack 캐시. 재시작하면 비지만, 한 프로세스가
    #: 살아있는 동안의 중복 호출은 여기서 막는다 (모듈 docstring 참고).
    _sent: dict[str, Ack] = field(default_factory=dict, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def _live(self, *, as_of: datetime) -> bool:
        return bool(self.store.config("execution.live_trading", as_of=as_of))

    def _translate(self, error: LSAPIError) -> BrokerError:
        """모듈 docstring §LSAPIError 번역 참고."""
        if error.rsp_cd is not None:
            # 거래소가 실제로 응답한 거부다 — 확실히 안 나갔다.
            return RejectedOrder(str(error))
        # 응답을 못 받았거나(네트워크/파싱/HTTP 오류) 재시도가 소진됐다.
        # 거래소에 들어갔는지 알 수 없다 — 재전송 금지.
        return BrokerError(str(error))

    def submit(self, order: PlannedOrder, *, as_of: datetime) -> Ack:
        with self._lock:
            cached = self._sent.get(order.order_id)
        if cached is not None:
            # 같은 order_id 재호출 — 다시 내보내지 않고 이전 결과를 그대로 돌려준다.
            return cached

        if not self._live(as_of=as_of):
            ack = Ack(
                order_id=order.order_id,
                accepted=True,
                sent=False,
                rsp_msg="execution.live_trading 꺼짐 — 전송하지 않았다",
            )
            return ack

        planned = order.order
        body = _order_body(
            symbol=planned.entity_id,
            side=planned.side,
            quantity=planned.quantity,
            limit_price=planned.limit_price,
        )
        try:
            data = self.client.request_tr(PATH_ORDER, TR_NEW, body)
        except LSAPIError as error:
            raise self._translate(error) from error

        if data.get("paper"):
            # LSClient 자체가 paper 상태다(§관계 참고) — store 는 켜졌다고
            # 해도 실제로는 안 나갔다. client 의 판단을 따른다.
            ack = Ack(
                order_id=order.order_id,
                accepted=True,
                sent=False,
                rsp_msg="LSClient.live_trading 꺼짐 — 전송하지 않았다",
                raw=data,
            )
            return ack

        ack = Ack(
            order_id=order.order_id,
            accepted=True,
            broker_order_no=_ord_no(data),
            rsp_cd=data.get("rsp_cd"),
            rsp_msg=data.get("rsp_msg"),
            sent=True,
            raw=data,
        )
        with self._lock:
            # 실제로 나간 것만 캐시한다 — paper 응답을 캐시하면 store 설정이
            # 나중에 켜져도 이전 paper 결과가 재사용되어 영원히 안 나간다.
            self._sent[order.order_id] = ack
        return ack

    def cancel(self, *, broker_order_no: str, entity_id: str, quantity: int) -> Ack:
        # cancel/modify 는 Broker Protocol 에 as_of 가 없다. Clock 은
        # client 가 이미 주입받아 들고 있으므로 그걸 빌린다 —
        # datetime.now() 를 새로 부르지 않는다 (불변식 2).
        as_of = self.client.clock.now()
        if not self._live(as_of=as_of):
            return Ack(order_id=broker_order_no, accepted=True, sent=False)

        body = {
            "CSPAT00801InBlock1": {
                "OrgOrdNo": int(broker_order_no),
                "IsuNo": isu_code(entity_id),
                "OrdQty": int(quantity),
            }
        }
        try:
            data = self.client.request_tr(PATH_ORDER, TR_CANCEL, body)
        except LSAPIError as error:
            raise self._translate(error) from error

        if data.get("paper"):
            return Ack(order_id=broker_order_no, accepted=True, sent=False, raw=data)

        return Ack(
            order_id=broker_order_no,
            accepted=True,
            broker_order_no=broker_order_no,
            rsp_cd=data.get("rsp_cd"),
            rsp_msg=data.get("rsp_msg"),
            sent=True,
            raw=data,
        )

    def modify(
        self, *, broker_order_no: str, entity_id: str, quantity: int, price: float
    ) -> Ack:
        as_of = self.client.clock.now()
        if not self._live(as_of=as_of):
            return Ack(order_id=broker_order_no, accepted=True, sent=False)

        body = {
            "CSPAT00701InBlock1": {
                "OrgOrdNo": int(broker_order_no),
                "IsuNo": isu_code(entity_id),
                "OrdQty": int(quantity),
                # 재호가는 항상 지정가다 — Protocol 의 ``price`` 는 필수
                # 인자라 시장가로 정정하는 경로가 없다(모듈 docstring 참고).
                "OrdprcPtnCode": ORD_PTN_LIMIT,
                "OrdCndiTpCode": ORD_CNDI_NONE,
                "OrdPrc": int(price),
            }
        }
        try:
            data = self.client.request_tr(PATH_ORDER, TR_MODIFY, body)
        except LSAPIError as error:
            raise self._translate(error) from error

        if data.get("paper"):
            return Ack(order_id=broker_order_no, accepted=True, sent=False, raw=data)

        new_order_no = _ord_no(data) or broker_order_no
        return Ack(
            order_id=broker_order_no,
            accepted=True,
            broker_order_no=new_order_no,
            rsp_cd=data.get("rsp_cd"),
            rsp_msg=data.get("rsp_msg"),
            sent=True,
            raw=data,
        )
