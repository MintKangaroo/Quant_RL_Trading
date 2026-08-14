"""LSBroker — 실제로 나가는 마지막 구간이 안전한지.

httpx 는 MockTransport 로 막는다. **실제 주문은 이 테스트 스위트 어디서도
내보내지 않는다.**
"""

from __future__ import annotations

from datetime import datetime

import httpx
import pytest

from quant_rl_trading.broker import BrokerError, RejectedOrder
from quant_rl_trading.broker.ls_order import LSBroker
from quant_rl_trading.collectors.ls_client import LSClient, LSCredentials
from quant_rl_trading.executor.orders import PlannedOrder
from quant_rl_trading.replay.clock import ReplayClock
from quant_rl_trading.schemas.order import Order, Side

CREDS = LSCredentials(appkey="key", appsecret="secret", base_url="https://api.test")


class FakeStore:
    """``execution.live_trading`` 하나만 흉내낸다. 실제 Store 는 필요 없다."""

    def __init__(self, *, live_trading: bool) -> None:
        self.live_trading = live_trading
        self.calls: list[tuple[str, datetime]] = []

    def config(self, name: str, *, as_of: datetime) -> bool:
        self.calls.append((name, as_of))
        assert name == "execution.live_trading"
        return self.live_trading


def token_response() -> httpx.Response:
    return httpx.Response(
        200, json={"access_token": "tok", "token_type": "Bearer", "expires_in": 86400}
    )


def make_client(handler, ts, *, client_live: bool = True) -> LSClient:  # type: ignore[no-untyped-def]
    def routed(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return token_response()
        return handler(request)

    return LSClient(
        credentials=CREDS,
        clock=ReplayClock(ts(2026, 8, 14, 10)),
        transport=httpx.MockTransport(routed),
        live_trading=client_live,
        sleep=lambda _: None,
    )


def planned_order(
    *, order_id: str = "abc123", side: Side = Side.BUY, price: float | None = 1000.0
) -> PlannedOrder:
    return PlannedOrder(
        order=Order(entity_id="005930", side=side, quantity=10, limit_price=price),
        order_id=order_id,
        session_id="KR-2026-08-14",
        slice_seq=0,
        target_weight=0.05,
    )


def ok_response(order_no: str = "700001") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "rsp_cd": "00000",
            "rsp_msg": "정상처리 되었습니다",
            "CSPAT00601OutBlock2": {"OrdNo": order_no},
        },
    )


# -- 전송 여부 두 게이트 ---------------------------------------------------


def test_store_gate_off_blocks_send_without_any_http_call(ts) -> None:  # type: ignore[no-untyped-def]
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return ok_response()

    client = make_client(handler, ts)
    store = FakeStore(live_trading=False)
    broker = LSBroker(client=client, store=store)

    ack = broker.submit(planned_order(), as_of=ts(2026, 8, 14, 10))

    assert ack.sent is False
    assert ack.accepted is True
    assert calls == []  # 네트워크에 닿지도 않았다


def test_client_side_paper_blocks_send_even_if_store_says_live(ts) -> None:  # type: ignore[no-untyped-def]
    """store 는 켜졌지만 client 자체가 paper — 여전히 안 나간다."""
    client = make_client(lambda r: ok_response(), ts, client_live=False)
    store = FakeStore(live_trading=True)
    broker = LSBroker(client=client, store=store)

    ack = broker.submit(planned_order(), as_of=ts(2026, 8, 14, 10))

    assert ack.sent is False


def test_both_gates_open_sends(ts) -> None:  # type: ignore[no-untyped-def]
    client = make_client(lambda r: ok_response(), ts)
    store = FakeStore(live_trading=True)
    broker = LSBroker(client=client, store=store)

    ack = broker.submit(planned_order(), as_of=ts(2026, 8, 14, 10))

    assert ack.sent is True
    assert ack.accepted is True


# -- body 생성: 지정가/시장가, 매수/매도 방향 -------------------------------


def test_limit_order_body(ts) -> None:  # type: ignore[no-untyped-def]
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.append(json.loads(request.content))
        return ok_response()

    client = make_client(handler, ts)
    broker = LSBroker(client=client, store=FakeStore(live_trading=True))

    broker.submit(planned_order(side=Side.BUY, price=1000.0), as_of=ts(2026, 8, 14, 10))

    block = seen[0]["CSPAT00601InBlock1"]
    assert block["IsuNo"] == "A005930"
    assert block["OrdQty"] == 10
    assert block["OrdPrc"] == 1000
    assert block["OrdprcPtnCode"] == "00"  # 지정가
    assert block["BnsTpCode"] == "2"  # 매수


def test_market_order_body_is_used_only_when_limit_price_is_none(ts) -> None:  # type: ignore[no-untyped-def]
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.append(json.loads(request.content))
        return ok_response()

    client = make_client(handler, ts)
    broker = LSBroker(client=client, store=FakeStore(live_trading=True))

    broker.submit(
        planned_order(order_id="market-order", side=Side.SELL, price=None),
        as_of=ts(2026, 8, 14, 10),
    )

    block = seen[0]["CSPAT00601InBlock1"]
    assert block["OrdPrc"] == 0
    assert block["OrdprcPtnCode"] == "03"  # 시장가
    assert block["BnsTpCode"] == "1"  # 매도


def test_sell_side_direction(ts) -> None:  # type: ignore[no-untyped-def]
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.append(json.loads(request.content))
        return ok_response()

    client = make_client(handler, ts)
    broker = LSBroker(client=client, store=FakeStore(live_trading=True))

    broker.submit(planned_order(side=Side.SELL), as_of=ts(2026, 8, 14, 10))

    assert seen[0]["CSPAT00601InBlock1"]["BnsTpCode"] == "1"


# -- OrdNo 파싱 --------------------------------------------------------------


def test_ord_no_parsed_from_out_block2(ts) -> None:  # type: ignore[no-untyped-def]
    client = make_client(lambda r: ok_response(order_no="900123"), ts)
    broker = LSBroker(client=client, store=FakeStore(live_trading=True))

    ack = broker.submit(planned_order(), as_of=ts(2026, 8, 14, 10))

    assert ack.broker_order_no == "900123"


def test_missing_ord_no_is_not_an_error_but_unconfirmed(ts) -> None:  # type: ignore[no-untyped-def]
    """OrdNo 누락은 거부가 아니라 모름이다 — accepted 는 True, 번호만 없다."""
    response = httpx.Response(200, json={"rsp_cd": "00000", "rsp_msg": "ok"})
    client = make_client(lambda r: response, ts)
    broker = LSBroker(client=client, store=FakeStore(live_trading=True))

    ack = broker.submit(planned_order(), as_of=ts(2026, 8, 14, 10))

    assert ack.accepted is True
    assert ack.broker_order_no is None


# -- 오류 번역: BrokerError vs RejectedOrder --------------------------------


def test_explicit_rejection_from_exchange_is_rejected_order(ts) -> None:  # type: ignore[no-untyped-def]
    """거래소가 실제로 거부 응답을 준 경우 — 확실히 안 나갔다, 재전송 가능."""
    response = httpx.Response(
        200, json={"rsp_cd": "40510", "rsp_msg": "잔고부족"}
    )
    client = make_client(lambda r: response, ts)
    broker = LSBroker(client=client, store=FakeStore(live_trading=True))

    with pytest.raises(RejectedOrder):
        broker.submit(planned_order(), as_of=ts(2026, 8, 14, 10))


def test_network_error_is_broker_error_not_rejected(ts) -> None:  # type: ignore[no-untyped-def]
    """응답을 못 받은 경우 — 나갔는지 모른다, 재전송하면 안 된다."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    client = make_client(handler, ts)
    broker = LSBroker(client=client, store=FakeStore(live_trading=True))

    with pytest.raises(BrokerError) as caught:
        broker.submit(planned_order(), as_of=ts(2026, 8, 14, 10))

    assert not isinstance(caught.value, RejectedOrder)


def test_http_error_is_broker_error_not_rejected(ts) -> None:  # type: ignore[no-untyped-def]
    client = make_client(lambda r: httpx.Response(500, text="boom"), ts)
    broker = LSBroker(client=client, store=FakeStore(live_trading=True))

    with pytest.raises(BrokerError) as caught:
        broker.submit(planned_order(), as_of=ts(2026, 8, 14, 10))

    assert not isinstance(caught.value, RejectedOrder)


# -- 멱등성 -------------------------------------------------------------------


def test_same_order_id_twice_sends_only_once(ts) -> None:  # type: ignore[no-untyped-def]
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return ok_response(order_no="555")

    client = make_client(handler, ts)
    broker = LSBroker(client=client, store=FakeStore(live_trading=True))
    order = planned_order(order_id="dup-1")

    first = broker.submit(order, as_of=ts(2026, 8, 14, 10))
    second = broker.submit(order, as_of=ts(2026, 8, 14, 10))

    assert len(calls) == 1
    assert first.broker_order_no == second.broker_order_no == "555"


def test_paper_result_is_not_cached_so_a_later_live_flip_can_send(ts) -> None:  # type: ignore[no-untyped-def]
    """paper 로 끝난 시도는 캐시하지 않는다 — 나중에 live 로 켜지면 실제로 나가야 한다."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return ok_response()

    client = make_client(handler, ts)
    store = FakeStore(live_trading=False)
    broker = LSBroker(client=client, store=store)
    order = planned_order(order_id="flip-1")

    first = broker.submit(order, as_of=ts(2026, 8, 14, 10))
    assert first.sent is False
    assert calls == []

    store.live_trading = True
    second = broker.submit(order, as_of=ts(2026, 8, 14, 10))

    assert second.sent is True
    assert len(calls) == 1


# -- cancel / modify -----------------------------------------------------


def test_cancel_sends_org_ord_no(ts) -> None:  # type: ignore[no-untyped-def]
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"rsp_cd": "00463", "rsp_msg": "취소완료되었습니다"})

    client = make_client(handler, ts)
    broker = LSBroker(client=client, store=FakeStore(live_trading=True))

    ack = broker.cancel(broker_order_no="900123", entity_id="005930", quantity=10)

    assert ack.sent is True
    assert seen[0]["CSPAT00801InBlock1"]["OrgOrdNo"] == 900123
    assert seen[0]["CSPAT00801InBlock1"]["IsuNo"] == "A005930"


def test_cancel_blocked_when_store_live_trading_off(ts) -> None:  # type: ignore[no-untyped-def]
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"rsp_cd": "00463"})

    client = make_client(handler, ts)
    broker = LSBroker(client=client, store=FakeStore(live_trading=False))

    ack = broker.cancel(broker_order_no="900123", entity_id="005930", quantity=10)

    assert ack.sent is False
    assert calls == []


def test_modify_uses_limit_price_pattern_code(ts) -> None:  # type: ignore[no-untyped-def]
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.append(json.loads(request.content))
        return ok_response(order_no="900124")

    client = make_client(handler, ts)
    broker = LSBroker(client=client, store=FakeStore(live_trading=True))

    ack = broker.modify(
        broker_order_no="900123", entity_id="005930", quantity=5, price=1010.0
    )

    block = seen[0]["CSPAT00701InBlock1"]
    assert block["OrdprcPtnCode"] == "00"
    assert block["OrdPrc"] == 1010
    assert ack.sent is True
