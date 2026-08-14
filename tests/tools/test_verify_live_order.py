"""tools/verify_live_order.py — 실주문은 어디서도 내보내지 않는다.

httpx 는 ``MockTransport`` 로 막는다. ``confirm``/``prompt`` 는 스크립트로
주입해 실제 ``input()`` 을 부르지 않는다. 여기서 고정하는 것 넷:

1. 두 게이트(execution.live_trading · LSClient.live_trading) 중 하나라도
   꺼지면 주문 TR 은 나가지 않는다.
2. ``--dry-run`` 은 본문만 출력하고 끝난다 — 확인을 다 y 로 줘도 전송 콜이 없다.
3. 주문 금액 상한을 넘으면 확인 프롬프트까지 가지도 않는다.
4. 호가단위 반올림(``round_to_tick``)을 거친 가격만 주문 요약에 나온다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar

import httpx

from quant_rl_trading.collectors.ls_client import LSClient, LSCredentials
from quant_rl_trading.replay.clock import ReplayClock
from quant_rl_trading.schemas.order import Side
from tools.verify_live_order import (
    Quote,
    RunConfig,
    build_order_summary,
    reference_price,
    run,
)

CREDS = LSCredentials(appkey="key", appsecret="secret", base_url="https://api.test")
REGULAR_SESSION = datetime(2026, 8, 14, 1, 0, tzinfo=UTC)  # 한국시간 10:00 (정규장)


def token_response() -> httpx.Response:
    return httpx.Response(
        200, json={"access_token": "tok", "token_type": "Bearer", "expires_in": 86400}
    )


def quote_response(
    *, price: float = 1000.0, bid: float = 995.0, ask: float = 1005.0
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "rsp_cd": "00000",
            "t1102OutBlock": {"price": price, "bidho1": bid, "offerho1": ask},
        },
    )


def balance_response(net_asset: float = 10_000_000.0) -> httpx.Response:
    return httpx.Response(
        200,
        json={"rsp_cd": "00000", "t0424OutBlock": {"sunamt": net_asset}, "t0424OutBlock1": []},
    )


def order_ack_response(order_no: str = "700001") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "rsp_cd": "00000",
            "rsp_msg": "정상처리 되었습니다",
            "CSPAT00601OutBlock2": {"OrdNo": order_no},
        },
    )


def fills_response(rows: list[dict]) -> httpx.Response:
    return httpx.Response(200, json={"rsp_cd": "00000", "t0425OutBlock1": rows})


class FakeStore:
    """``store.config``·``store.get`` 최소 흉내. 킬스위치·잔고 조회 둘 다 쓴다."""

    def __init__(self, *, live_trading: bool) -> None:
        self.live_trading = live_trading

    #: 체결 확인(``broker/fills.py``)이 비용 계산에 필요로 하는 값들.
    #: 이 검증기 자체의 관심사는 아니라 기본값으로 채워 둔다.
    RATE_DEFAULTS: ClassVar[dict[str, float]] = {
        "accounting.fee_kr": 0.00015,
        "accounting.fee_us": 0.0,
        "accounting.transaction_tax_kr": 0.0018,
        "accounting.dividend_tax_kr": 0.0,
        "accounting.dividend_tax_us": 0.0,
        "accounting.capital_gains_us": 0.0,
    }

    def config(self, name: str, *, as_of: datetime):  # type: ignore[no-untyped-def]
        if name == "execution.live_trading":
            return self.live_trading
        if name in self.RATE_DEFAULTS:
            return self.RATE_DEFAULTS[name]
        raise AssertionError(f"예상하지 못한 config 조회: {name}")

    def get(self, table: str, *, as_of: datetime, entity=None, lookback=None, market=None):  # type: ignore[no-untyped-def]
        import pandas as pd

        # 킬스위치는 항상 비어 있다(RELEASED) — 이 테스트들의 관심사가 아니다.
        # trades 도 빈 프레임으로 — 체결 조회의 "이미 적힌 수량" 이 0에서 시작한다.
        return pd.DataFrame()

    def append(self, table: str, rows, *, ingest_run_id: str, source: str = ""):  # type: ignore[no-untyped-def]
        return len(rows)

    def ingest_run_recorded(self, table: str, run_id: str) -> bool:
        return False


def make_client(handler, *, live_trading: bool) -> LSClient:  # type: ignore[no-untyped-def]
    def routed(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return token_response()
        return handler(request)

    return LSClient(
        credentials=CREDS,
        clock=ReplayClock(REGULAR_SESSION),
        transport=httpx.MockTransport(routed),
        live_trading=live_trading,
        sleep=lambda _: None,
    )


def config(**overrides) -> RunConfig:  # type: ignore[no-untyped-def]
    base = dict(symbol="005930", quantity=1, max_order_value=100_000.0, live=False, dry_run=False)
    base.update(overrides)
    return RunConfig(**base)  # type: ignore[arg-type]


def all_yes(_: str) -> bool:
    return True


def blank(_: str) -> str:
    return ""


# -----------------------------------------------------------------------------
# 순수 계산
# -----------------------------------------------------------------------------


def test_주문금액이_상한을_넘으면_거부():
    summary = build_order_summary(
        symbol="005930", side=Side.BUY, quantity=1000,
        raw_reference_price=100_000.0, max_order_value=100_000.0,
    )
    assert summary.ok is False
    assert "상한" in summary.reason


def test_호가단위로_반올림된_가격만_쓴다():
    # 555 는 2,000원 미만 구간(호가단위 1원)이 아니라 5,000~20,000원대(10원 단위) 예시로.
    summary = build_order_summary(
        symbol="005930", side=Side.BUY, quantity=1,
        raw_reference_price=10_003.0, max_order_value=1_000_000.0,
    )
    assert summary.price % 10 == 0  # 10,000~20,000원 구간은 10원 단위


def test_매수_기준가는_매도호가1을_우선한다():
    quote = Quote(price=1000.0, bid=995.0, ask=1005.0)
    assert reference_price(quote, Side.BUY) == 1005.0
    assert reference_price(quote, Side.SELL) == 995.0


def test_호가가_비면_현재가로_물러선다():
    quote = Quote(price=1000.0, bid=0.0, ask=0.0)
    assert reference_price(quote, Side.BUY) == 1000.0
    assert reference_price(quote, Side.SELL) == 1000.0


# -----------------------------------------------------------------------------
# 게이트 — 둘 중 하나라도 꺼지면 주문 TR 이 안 나간다
# -----------------------------------------------------------------------------


def test_live_없이_돌리면_주문TR을_보내지_않는다(capsys):  # type: ignore[no-untyped-def]
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers.get("tr_cd", ""))
        if request.headers.get("tr_cd") == "t1102":
            return quote_response()
        raise AssertionError(f"예상하지 못한 TR 호출: {request.headers.get('tr_cd')}")

    client = make_client(handler, live_trading=False)
    store = FakeStore(live_trading=False)
    code = run(
        config(live=False, dry_run=False),
        store=store, client=client, clock=ReplayClock(REGULAR_SESSION),
        confirm=all_yes, prompt=blank, out=lambda _: None,
    )
    # live=False → dry_run 이 자동으로 켜져 시세 조회 이후 바로 종료한다.
    assert code == 0
    assert "CSPAT00601" not in calls
    # 잔고 조회(t0424)도 client 단에서 paper 로 막혀 전송은 없다.
    assert "t0424" not in calls


def test_live인데_execution_live_trading이_꺼져있으면_아무것도_안한다():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("게이트가 막아야 하는데 네트워크까지 갔다")

    client = make_client(handler, live_trading=True)
    store = FakeStore(live_trading=False)
    code = run(
        config(live=True, dry_run=False),
        store=store, client=client, clock=ReplayClock(REGULAR_SESSION),
        confirm=all_yes, prompt=blank, out=lambda _: None,
    )
    assert code == 1


def test_dry_run은_live여도_주문을_보내지_않는다():
    sent_orders: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        tr = request.headers.get("tr_cd")
        if tr == "t1102":
            return quote_response()
        if tr == "t0424":
            return balance_response()
        sent_orders.append(tr or "")
        raise AssertionError(f"드라이런인데 주문 TR 이 나갔다: {tr}")

    client = make_client(handler, live_trading=True)
    store = FakeStore(live_trading=True)
    code = run(
        config(live=True, dry_run=True),
        store=store, client=client, clock=ReplayClock(REGULAR_SESSION),
        confirm=all_yes, prompt=blank, out=lambda _: None,
    )
    assert code == 0
    assert sent_orders == []


def test_확인프롬프트에서_아니오면_중단한다():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("첫 확인에서 거부했는데 네트워크까지 갔다")

    client = make_client(handler, live_trading=False)
    store = FakeStore(live_trading=False)
    code = run(
        config(live=False),
        store=store, client=client, clock=ReplayClock(REGULAR_SESSION),
        confirm=lambda _: False, prompt=blank, out=lambda _: None,
    )
    assert code == 1


# -----------------------------------------------------------------------------
# 게이트 둘 다 열렸을 때 — 매수 → 체결조회 → 매도까지 끝까지 돈다
# -----------------------------------------------------------------------------


def test_양쪽_게이트가_열리면_매수_체결_매도까지_끝까지_돈다():
    order_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        tr = request.headers.get("tr_cd")
        if tr == "t1102":
            return quote_response()
        if tr == "t0424":
            return balance_response()
        if tr == "CSPAT00601":
            order_calls.append("order")
            return order_ack_response()
        if tr == "t0425":
            # 첫 조회에서 바로 전량 체결된 것으로 응답한다.
            return fills_response([
                {"ordno": "700001", "cheqty": "1", "cheprice": "1005"}
            ])
        raise AssertionError(f"예상하지 못한 TR: {tr}")

    client = make_client(handler, live_trading=True)
    store = FakeStore(live_trading=True)
    code = run(
        config(live=True, dry_run=False),
        store=store, client=client, clock=ReplayClock(REGULAR_SESSION),
        confirm=all_yes, prompt=blank, out=lambda _: None,
    )
    assert code == 0
    # 매수 1회 + 매도 1회.
    assert len(order_calls) == 2
