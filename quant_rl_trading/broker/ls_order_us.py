"""LS 증권 **해외주식(미국)** 주문 창구. ``broker/ls_order.py`` 의 미장 짝.

국장 파일을 건드리지 않고 **새 파일로 뺐다.** 두 시장은 TR 도 필드도 다르고,
같은 함수 안에서 분기하면 국장 경로가 미장 변경에 흔들린다. 안전 규약
(두 게이트·멱등성·오류 번역)은 ``ls_order.py`` 모듈 docstring 을 그대로 따르고,
여기서는 **미장에서만 다른 것**만 적는다.

## 국장과 겹치는 필드는 둘뿐이다

`IsuNo` 와 `OrdprcPtnCode` 만 이름이 같다. 나머지를 국장 것으로 쓰면 거부되거나
엉뚱한 필드가 먹는다.

| | 국장 `CSPAT00601` | 미장 `COSAT00301` |
|---|---|---|
| 매매구분 | `BnsTpCode` `"2"`매수 `"1"`매도 | **`OrdPtnCode`** `"02"`매수 `"01"`매도 |
| 취소 | 전용 TR `CSPAT00801` | **같은 TR, `OrdPtnCode="08"`** |
| 정정 | 전용 TR `CSPAT00701` | 전용 TR `COSAT00311`, `OrdPtnCode="07"` |
| 종목 | `IsuNo` = ``A`` + 6자리 | `IsuNo` = 심볼 그대로 (`WEN`) |
| 가격 | `OrdPrc` 정수 원 | **`OvrsOrdPrc`** float 달러 |
| 시장 | 없음 | **`OrdMktCode`** `81`뉴욕 `82`나스닥 **필수** |

> ⚠️ **`OrdPtnCode`(매수/매도)와 `OrdprcPtnCode`(지정가/시장가)는 다른 필드다.**
> 세 글자(`prc`) 차이다. 바꿔 쓰면 `"00"`(지정가)이 매매구분 자리에 들어간다.

## 신용이 없다 — 없는 것을 없다고 못 박는다

`COSAT00601InBlock1` 에는 국장의 `MgntrnCode`(신용구분)·`LoanDt`(대출일자)에
해당하는 필드가 **아예 없다**. 그래서 미장에서 신용을 막는 방법은 "값을
`"000"` 으로 둔다" 가 아니라 **"그 필드를 만들지 않는다"** 다.
필드가 늘어나면 그 규약이 소리 없이 깨지므로 `tests/invariants/test_cash_only.py`
가 본문 키 집합을 통째로 고정한다.

미수(예수금 초과 매수)는 국장과 똑같이 **호출부가** 막는다 — 여기서는 막지
않는다. `tools/verify_live_order.py` 가 `FcurrOrdAbleAmt`(USD 주문가능금액)와
비교한다.

## 소수점 주문은 불가

`OrdQty` 는 정수만 받는다. 소수점 수량이 오면 **반올림하지 않고 거부한다** —
0.4주를 0주로 깎으면 "주문했는데 아무것도 안 샀다" 가 되고, 1주로 올리면
예수금 검사를 통과한 금액보다 더 산다. 둘 다 조용한 사고다.

출처: TR 존재는 실호출로 확인했고 `COSAT00301` 필드는 LS 포털 공식 예제,
`01`/`08`/`07` 은 LS OpenApi 샘플이다. 등급별 근거는 `docs/design/ls-api.md` §0.
**본문을 실제로 보낸 적은 아직 없다.**
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from quant_rl_trading.broker import Ack, BrokerError, RejectedOrder
from quant_rl_trading.collectors.errors import LSAPIError
from quant_rl_trading.collectors.ls_client import LSClient
from quant_rl_trading.schemas.order import Side

if TYPE_CHECKING:
    from quant_rl_trading.executor.orders import PlannedOrder
    from quant_rl_trading.store import Store

#: 해외주식 주문 경로. 국장(``/stock/order``)과 다르다.
PATH_ORDER_US = "/overseas-stock/order"
#: 해외주식 계좌 경로 (잔고·예수금·체결조회). 국장은 ``/stock/accno``.
PATH_ACCNO_US = "/overseas-stock/accno"

#: 신규·취소. 미장은 취소가 별도 TR 이 아니라 같은 TR 의 ``OrdPtnCode`` 다.
TR_NEW = "COSAT00301"
#: 정정 전용.
TR_MODIFY = "COSAT00311"

#: ``OrdPtnCode`` — 주문유형. **국장의 ``BnsTpCode`` 가 아니다.**
ORD_PTN_SELL = "01"
ORD_PTN_BUY = "02"
ORD_PTN_MODIFY = "07"
ORD_PTN_CANCEL = "08"

#: ``OrdprcPtnCode`` — 호가유형. 이름과 값 모두 국장과 같다.
ORDPRC_LIMIT = "00"
ORDPRC_MARKET = "03"

#: ``OrdMktCode`` — 주문시장. 시세 조회로 **확인해서** 넣는다. 짐작하면 안 된다:
#: 틀린 코드로 g3104 를 부르면 "해당종목이 없습니다" 가 온다(2026-08-15 실측).
MKT_NYSE = "81"
MKT_NASDAQ = "82"
MARKET_CODES = (MKT_NASDAQ, MKT_NYSE)

#: 중개인구분코드. 비워 둔다.
BRK_NONE = ""

#: ``COSAT00301InBlock1`` 이 가져야 하는 키 전부. 신용 관련 필드가 **없다**는
#: 것 자체가 규약이라, 늘어나도 줄어도 불변식 테스트가 잡는다.
ORDER_BODY_KEYS = frozenset(
    {
        "RecCnt",
        "OrdPtnCode",
        "OrdMktCode",
        "IsuNo",
        "OrdQty",
        "OvrsOrdPrc",
        "OrdprcPtnCode",
        "BrkTpCode",
    }
)


class FractionalQuantity(RejectedOrder):
    """소수점 수량 — 미장 LS 는 정수주만 받는다. 깎지 않고 거부한다."""


def us_symbol(entity_id: str) -> str:
    """주문용 ``IsuNo``. 창고는 ``US:WEN`` 으로 들고 있고 LS 는 ``WEN`` 을 받는다.

    국장 ``isu_code()`` 처럼 접두어를 **붙이는** 것이 아니라 **떼는** 함수다.
    """
    stripped = (entity_id or "").strip()
    _, _, symbol = stripped.rpartition(":")
    return (symbol or stripped).upper()


def _int_quantity(quantity: float) -> int:
    """정수주만. 소수점이면 거부한다 (모듈 docstring §소수점 참고)."""
    as_int = int(quantity)
    if as_int != quantity:
        raise FractionalQuantity(
            f"미장 LS 는 정수주만 받는다 — {quantity}주는 보낼 수 없다 "
            "(반올림하면 예수금 검사를 통과한 금액과 어긋난다)"
        )
    if as_int <= 0:
        raise RejectedOrder(f"주문 수량이 0 이하다: {quantity}")
    return as_int


def us_order_body(
    *,
    symbol: str,
    side: Side,
    quantity: float,
    limit_price: float | None,
    market_code: str,
) -> dict[str, Any]:
    """``COSAT00301InBlock1`` — 신규 주문. 시장가는 ``limit_price is None``."""
    if market_code not in MARKET_CODES:
        raise RejectedOrder(
            f"OrdMktCode 는 {MARKET_CODES} 중 하나여야 한다 — 받은 값 {market_code!r}. "
            "시세 조회로 확인한 값을 넣어라(짐작하면 '해당종목이 없습니다' 가 온다)."
        )
    market = limit_price is None
    return {
        f"{TR_NEW}InBlock1": {
            "RecCnt": 1,
            "OrdPtnCode": ORD_PTN_BUY if side is Side.BUY else ORD_PTN_SELL,
            "OrdMktCode": market_code,
            "IsuNo": us_symbol(symbol),
            "OrdQty": _int_quantity(quantity),
            "OvrsOrdPrc": 0.0 if market else float(limit_price),
            "OrdprcPtnCode": ORDPRC_MARKET if market else ORDPRC_LIMIT,
            "BrkTpCode": BRK_NONE,
        }
    }


def us_cancel_body(*, order_no: str, quantity: float, market_code: str) -> dict[str, Any]:
    """취소. **신규와 같은 TR** 에 ``OrdPtnCode="08"`` + ``OrgOrdNo``.

    샘플은 `IsuNo`·`OrdMktCode` 를 비우고 `OrdQty=0` 으로 보낸다 — 원주문번호만으로
    식별한다는 뜻이다. 다만 **이 본문은 아직 보내 본 적이 없다.** 취소 수량의
    의미(이번에 취소할 수량인지 잔량 전체인지)는 국장에서도 확인 중인 항목이라
    (`docs/live-order-checklist.md`), 미장 첫 실행에서 관찰해야 한다.
    """
    return {
        f"{TR_NEW}InBlock1": {
            "RecCnt": 1,
            "OrdPtnCode": ORD_PTN_CANCEL,
            "OrgOrdNo": int(order_no),
            "OrdMktCode": market_code,
            "IsuNo": "",
            "OrdQty": int(quantity),
            "OvrsOrdPrc": 0.0,
            "OrdprcPtnCode": ORDPRC_LIMIT,
            "BrkTpCode": BRK_NONE,
        }
    }


def us_modify_body(*, order_no: str, price: float, market_code: str) -> dict[str, Any]:
    """정정. 전용 TR ``COSAT00311`` + ``OrdPtnCode="07"``.

    샘플은 ``OrdQty=0``·``OrdprcPtnCode=""`` 로 **가격만** 바꾼다. 수량 정정은
    경로가 다를 수 있으나 확인된 바 없다 — 여기서는 가격 정정만 다룬다.
    """
    return {
        f"{TR_MODIFY}InBlock1": {
            "RecCnt": 1,
            "OrdPtnCode": ORD_PTN_MODIFY,
            "OrgOrdNo": int(order_no),
            "OrdMktCode": market_code,
            "IsuNo": "",
            "OrdQty": 0,
            "OvrsOrdPrc": float(price),
            "OrdprcPtnCode": "",
            "BrkTpCode": BRK_NONE,
        }
    }


def us_order_no(data: dict[str, Any]) -> str | None:
    """``OutBlock2`` 우선, 없으면 ``OutBlock1``. 국장 ``_ord_no`` 와 같은 규약 —
    **둘 다 없어도 예외를 던지지 않는다.** 주문번호를 못 읽은 것은 거부가
    아니라 모름이다(``ls_order.py`` 참고)."""
    for tr in (TR_NEW, TR_MODIFY):
        for suffix in ("OutBlock2", "OutBlock1"):
            block = data.get(f"{tr}{suffix}") or {}
            no = block.get("OrdNo") or block.get("OrgOrdNo")
            if no:
                return str(no)
    return None


@dataclass
class LSUSBroker:
    """미장 실전 창구. ``LSBroker`` 와 같은 규약 — 두 게이트가 다 열려야 나간다."""

    client: LSClient
    store: Store
    #: 주문 시장 코드. 호출부가 시세 조회로 **확인해서** 넣는다.
    market_code: str = MKT_NASDAQ
    _sent: dict[str, Ack] = field(default_factory=dict, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def _live(self, *, as_of: datetime) -> bool:
        return bool(self.store.config("execution.live_trading", as_of=as_of))

    def _translate(self, error: LSAPIError) -> BrokerError:
        """``ls_order.py`` §LSAPIError 번역과 같은 규약."""
        if error.rsp_cd is not None:
            return RejectedOrder(str(error))
        return BrokerError(str(error))

    def _not_live_ack(self, order_id: str, reason: str, raw: dict[str, Any] | None = None) -> Ack:
        return Ack(
            order_id=order_id, accepted=True, sent=False, rsp_msg=reason, raw=raw or {}
        )

    def _send(self, order_id: str, tr: str, body: dict[str, Any], *, as_of: datetime) -> Ack:
        if not self._live(as_of=as_of):
            return self._not_live_ack(order_id, "execution.live_trading 꺼짐 — 전송하지 않았다")
        try:
            data = self.client.request_tr(PATH_ORDER_US, tr, body)
        except LSAPIError as error:
            raise self._translate(error) from error
        if data.get("paper"):
            return self._not_live_ack(
                order_id, "LSClient.live_trading 꺼짐 — 전송하지 않았다", data
            )
        return Ack(
            order_id=order_id,
            accepted=True,
            broker_order_no=us_order_no(data),
            rsp_cd=data.get("rsp_cd"),
            rsp_msg=data.get("rsp_msg"),
            sent=True,
            raw=data,
        )

    def submit(self, order: PlannedOrder, *, as_of: datetime) -> Ack:
        with self._lock:
            cached = self._sent.get(order.order_id)
        if cached is not None:
            return cached

        planned = order.order
        body = us_order_body(
            symbol=planned.entity_id,
            side=planned.side,
            quantity=planned.quantity,
            limit_price=planned.limit_price,
            market_code=self.market_code,
        )
        ack = self._send(order.order_id, TR_NEW, body, as_of=as_of)
        if ack.sent:
            # 실제로 나간 것만 캐시한다 (``ls_order.py`` §멱등성).
            with self._lock:
                self._sent[order.order_id] = ack
        return ack

    def cancel(self, *, broker_order_no: str, entity_id: str, quantity: int) -> Ack:
        # cancel/modify 에는 as_of 가 없다. client 가 이미 들고 있는 Clock 을
        # 빌린다 — datetime.now() 를 새로 부르지 않는다 (불변식 2).
        as_of = self.client.clock.now()
        body = us_cancel_body(
            order_no=broker_order_no, quantity=quantity, market_code=self.market_code
        )
        return self._send(f"cancel:{broker_order_no}", TR_NEW, body, as_of=as_of)

    def modify(self, *, broker_order_no: str, entity_id: str, quantity: int, price: float) -> Ack:
        as_of = self.client.clock.now()
        body = us_modify_body(
            order_no=broker_order_no, price=price, market_code=self.market_code
        )
        return self._send(f"modify:{broker_order_no}", TR_MODIFY, body, as_of=as_of)
