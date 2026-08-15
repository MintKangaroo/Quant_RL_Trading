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

from quant_rl_trading.broker import RejectedOrder
from quant_rl_trading.broker.ls_order_us import (
    FractionalQuantity,
    us_cancel_body,
    us_modify_body,
    us_order_body,
)
from quant_rl_trading.collectors.ls_client import LSClient, LSCredentials
from quant_rl_trading.replay.clock import ReplayClock
from quant_rl_trading.schemas.order import Side
from tools.verify_live_order import (
    Quote,
    RunConfig,
    build_order_summary,
    fetch_balance_us,
    fetch_quote_us,
    reference_price,
    round_to_tick_us,
    run,
)

# ``kind`` 를 선언한다. 코드는 모의·실전을 판별할 수 없으므로 주문 도구가
# 선언을 요구한다 — 미선언이면 거기서 멈춘다(그 자체를 아래에서 검증한다).
CREDS = LSCredentials(
    appkey="key", appsecret="secret", base_url="https://api.test", kind="real"
)
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
        "accounting.capital_gains_allowance_krw": 2_500_000.0,
    }

    def config(self, name: str, *, as_of: datetime):  # type: ignore[no-untyped-def]
        if name == "execution.live_trading":
            return self.live_trading
        if name == "execution.live_account_fingerprint":
            # 고정 안 함. 지문 고정 자체는 아래 전용 테스트가 본다.
            return ""
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


# =============================================================================
# 미장 (--market US)
#
# 국장과 **자격증명·TR·통화·호가단위가 전부 다르다.** 여기서 고정하는 것은
# "미장이 된다" 가 아니라 **"국장 것이 섞여 나가지 않는다"** 다 — 필드 하나만
# 국장 것이 남아도 반대로 사거나 거부된다.
# =============================================================================

US_CREDS = LSCredentials(
    appkey="uskey", appsecret="ussecret", base_url="https://api.test", kind="real"
)
#: 미국 동부 10:00 (정규장). 2026-08-17 은 월요일 — 국장은 광복절 대체공휴일로
#: 쉬고 미장은 연다. 시장별 달력이 정말 갈리는지도 이 상수가 같이 본다.
US_REGULAR_SESSION = datetime(2026, 8, 17, 14, 0, tzinfo=UTC)

#: LS 가 g3104 로 돌려주는 종목별 호가단위. **표를 만들지 않는다.**
US_TICK = "0.0100"


def us_quote_response(
    *, close: float = 8.65, tick: str = US_TICK, suspend: str = "N", sellonly: str = "0"
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "rsp_cd": "00000",
            "g3104OutBlock": {
                "clos": f"{close}", "untprc": tick, "currency": "USD",
                "suspend": suspend, "sellonly": sellonly,
                # 호가 "가격" 이 아니라 호가 **수량 단위**다 — 이게 bid/ask 로
                # 새어 들어가면 $1 짜리 지정가가 나간다.
                "bidlotsize": "1", "asklotsize": "1",
            },
        },
    )


def us_deposit_response(order_able: float = 9.49) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "rsp_cd": "00000",
            "COSOQ02701OutBlock3": [
                {"CrcyCode": "USD", "FcurrDps": f"{order_able}",
                 "FcurrOrdAbleAmt": f"{order_able}", "BaseXchrat": "1414.9000"},
            ],
            # **원화 5원.** 이걸 주문가능금액으로 읽으면 안 된다.
            "COSOQ02701OutBlock4": {"WonDpsBalAmt": 5},
        },
    )


def make_us_client(handler, *, live_trading: bool) -> LSClient:  # type: ignore[no-untyped-def]
    def routed(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return token_response()
        return handler(request)

    return LSClient(
        credentials=US_CREDS,
        clock=ReplayClock(US_REGULAR_SESSION),
        transport=httpx.MockTransport(routed),
        live_trading=live_trading,
        sleep=lambda _: None,
    )


class FakeUSStore(FakeStore):
    """미장 지문 키를 아는 창고. 국장 키와 **다른 값**을 든다."""

    def __init__(self, *, live_trading: bool, us_fingerprint: str = "usfp") -> None:
        super().__init__(live_trading=live_trading)
        self.us_fingerprint = us_fingerprint

    def config(self, name: str, *, as_of: datetime):  # type: ignore[no-untyped-def]
        if name == "execution.live_account_fingerprint_us":
            return self.us_fingerprint
        return super().config(name, as_of=as_of)


def us_config(**overrides) -> RunConfig:  # type: ignore[no-untyped-def]
    base = dict(
        symbol="WEN", quantity=1, max_order_value=20.0,
        live=False, dry_run=False, market="US",
    )
    base.update(overrides)
    return RunConfig(**base)  # type: ignore[arg-type]


def run_us(cfg: RunConfig, *, handler, live_trading: bool, store=None, lines=None):  # type: ignore[no-untyped-def]
    client = make_us_client(handler, live_trading=live_trading)
    store = store if store is not None else FakeUSStore(live_trading=live_trading)
    sink = lines if lines is not None else []
    code = run(
        cfg, store=store, client=client, clock=ReplayClock(US_REGULAR_SESSION),
        confirm=all_yes, prompt=blank, out=sink.append,
    )
    return code, sink


# -- 주문 본문 ------------------------------------------------------------------


def test_미장_주문본문에_국장필드가_없다():
    body = us_order_body(
        symbol="US:WEN", side=Side.BUY, quantity=1, limit_price=8.65, market_code="82"
    )
    block = body["COSAT00301InBlock1"]
    assert block["OrdPtnCode"] == "02"          # 매수 — BnsTpCode 가 아니다
    assert block["OrdMktCode"] == "82"
    assert block["IsuNo"] == "WEN"              # "A" 접두어도 "US:" 접두어도 없다
    assert block["OvrsOrdPrc"] == 8.65          # 정수 원(OrdPrc)이 아니다
    assert block["OrdprcPtnCode"] == "00"       # 지정가
    # 국장 필드가 하나라도 섞이면 거부되거나 엉뚱한 필드가 먹는다.
    for kr_only in ("BnsTpCode", "OrdPrc", "MgntrnCode", "LoanDt", "OrdCndiTpCode"):
        assert kr_only not in block, f"국장 필드 {kr_only} 가 미장 본문에 들어갔다"


def test_미장_매도는_01이다():
    body = us_order_body(
        symbol="WEN", side=Side.SELL, quantity=2, limit_price=8.7, market_code="81"
    )
    assert body["COSAT00301InBlock1"]["OrdPtnCode"] == "01"


def test_미장_취소는_별도TR이_아니라_같은TR의_08이다():
    body = us_cancel_body(order_no="700001", quantity=1, market_code="82")
    block = body["COSAT00301InBlock1"]
    assert block["OrdPtnCode"] == "08"
    assert block["OrgOrdNo"] == 700001


def test_미장_정정은_COSAT00311의_07이다():
    body = us_modify_body(order_no="700001", price=8.7, market_code="82")
    assert "COSAT00311InBlock1" in body
    assert body["COSAT00311InBlock1"]["OrdPtnCode"] == "07"


def test_주문시장코드를_짐작하면_거부된다():
    """81/82 가 아닌 값은 본문을 만들지 못한다 — 틀린 코드로는 주문이
    'IsuNo 없음' 이 아니라 **엉뚱한 시장**으로 나갈 수 있다."""
    import pytest

    with pytest.raises(RejectedOrder):
        us_order_body(
            symbol="WEN", side=Side.BUY, quantity=1, limit_price=8.65, market_code="99"
        )


# -- 소수점 수량 ----------------------------------------------------------------


def test_소수점_수량은_본문에서_거부된다():
    import pytest

    with pytest.raises(FractionalQuantity):
        us_order_body(
            symbol="WEN", side=Side.BUY, quantity=0.5, limit_price=8.65, market_code="82"
        )


def test_소수점_수량은_주문요약에서_먼저_걸린다():
    """본문까지 가기 전에 사람이 읽을 수 있는 이유로 멈춰야 한다."""
    summary = build_order_summary(
        symbol="WEN", side=Side.BUY, quantity=1.5, raw_reference_price=8.65,
        max_order_value=20.0, tick=0.01, currency="USD",
    )
    assert summary.ok is False
    assert "정수주" in summary.reason


# -- 호가단위 -------------------------------------------------------------------


def test_미장_호가단위는_LS가_준_값을_쓴다():
    """``executor/ticks.py`` 의 원화 표를 타면 $8.653 이 8원/10원 단위로
    반올림된다. 매수는 내림, 매도는 올림 — 방향 규칙은 국장과 같다."""
    assert round_to_tick_us(8.653, side=Side.BUY, tick=0.01) == 8.65
    assert round_to_tick_us(8.653, side=Side.SELL, tick=0.01) == 8.66
    # $1 미만 종목(워런트)은 tick 이 0.0001 이다 — int 표로는 표현이 안 된다.
    assert round_to_tick_us(0.00731, side=Side.BUY, tick=0.0001) == 0.0073


def test_호가단위_배수인_가격은_그대로_남는다():
    """부동소수점 오차로 한 칸 밀리면 거래소가 거부한다."""
    assert round_to_tick_us(8.65, side=Side.BUY, tick=0.01) == 8.65
    assert round_to_tick_us(305.26, side=Side.SELL, tick=0.01) == 305.26


# -- 잔고 · 통화 ----------------------------------------------------------------


def test_USD_주문가능금액을_읽는다_원화5원이_아니다():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("tr_cd") == "COSOQ02701"
        assert request.url.path == "/overseas-stock/accno"
        return us_deposit_response()

    client = make_us_client(handler, live_trading=True)
    balance = fetch_balance_us(client)
    assert balance.net_asset == 9.49  # WonDpsBalAmt(5) 를 읽으면 안 된다


def test_USD_예수금보다_큰_주문은_거부된다():
    """상한(20.0)은 통과하지만 주문가능금액(9.49)을 넘는 주문."""
    def handler(request: httpx.Request) -> httpx.Response:
        tr = request.headers.get("tr_cd")
        if tr == "COSOQ02701":
            return us_deposit_response(order_able=9.49)
        if tr == "g3104":
            return us_quote_response(close=15.00)  # 1주 $15 > $9.49
        raise AssertionError(f"예수금 검사에서 멈춰야 하는데 {tr} 까지 갔다")

    code, lines = run_us(
        us_config(live=True, dry_run=True, max_order_value=20.0),
        handler=handler, live_trading=True,
        store=FakeUSStore(live_trading=True, us_fingerprint=US_CREDS.fingerprint),
    )
    assert code == 1
    assert any("예수금 부족" in line for line in lines)


# -- 게이트 ---------------------------------------------------------------------


def test_미장_지문이_다르면_live가_거부된다():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("지문 게이트가 막아야 하는데 네트워크까지 갔다")

    code, lines = run_us(
        us_config(live=True),
        handler=handler, live_trading=True,
        store=FakeUSStore(live_trading=True, us_fingerprint="다른지문"),
    )
    assert code == 1
    assert any("지문 불일치" in line for line in lines)


def test_미장_지문이_비어있으면_live가_거부된다():
    """국장은 빈 값이 '고정 안 함' 이지만 미장은 **미선언도 거부**다."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("미선언인데 네트워크까지 갔다")

    code, lines = run_us(
        us_config(live=True),
        handler=handler, live_trading=True,
        store=FakeUSStore(live_trading=True, us_fingerprint=""),
    )
    assert code == 1
    assert any("고정되지 않았다" in line for line in lines)


def test_미장_드라이런은_국장TR을_하나도_안_부른다():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        tr = request.headers.get("tr_cd") or ""
        seen.append(tr)
        if tr == "COSOQ02701":
            return us_deposit_response()
        if tr == "g3104":
            return us_quote_response()
        raise AssertionError(f"예상하지 못한 TR: {tr}")

    code, lines = run_us(
        us_config(live=True, dry_run=True),
        handler=handler, live_trading=True,
        store=FakeUSStore(live_trading=True, us_fingerprint=US_CREDS.fingerprint),
    )
    assert code == 0
    # 국장 TR 이 하나도 안 나갔다.
    for kr_tr in ("t0424", "t1102", "t0425", "CSPAT00601", "CSPAT00701", "CSPAT00801"):
        assert kr_tr not in seen, f"미장 검증인데 국장 TR {kr_tr} 이 나갔다"
    assert seen == ["COSOQ02701", "g3104"]
    # 주문 본문은 출력됐지만 전송은 없다.
    assert any("COSAT00301InBlock1" in line for line in lines)


def test_미장도_dry_run이면_주문TR이_안_나간다():
    def handler(request: httpx.Request) -> httpx.Response:
        tr = request.headers.get("tr_cd")
        if tr == "COSOQ02701":
            return us_deposit_response()
        if tr == "g3104":
            return us_quote_response()
        raise AssertionError(f"드라이런인데 {tr} 이 나갔다")

    code, _ = run_us(
        us_config(live=True, dry_run=True),
        handler=handler, live_trading=True,
        store=FakeUSStore(live_trading=True, us_fingerprint=US_CREDS.fingerprint),
    )
    assert code == 0


def test_주문시장코드는_시세가_나온_쪽을_쓴다():
    """82(나스닥)가 비면 81(뉴욕)로 넘어가고, **성공한 코드가 주문에 들어간다.**"""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("tr_cd") == "g3104"
        block = json_body(request)["g3104InBlock"]
        if block["exchcd"] == "82":
            return httpx.Response(
                200, json={"rsp_cd": "00000", "rsp_msg": "[g3104] 해당종목이 없습니다."}
            )
        return us_quote_response()

    client = make_us_client(handler, live_trading=False)
    quote = fetch_quote_us(client, "SNAP")
    assert quote is not None
    assert quote.market_code == "81"


def test_미장_시세는_호가를_주지_않는다():
    """``bidlotsize``/``asklotsize`` 는 호가 **수량 단위**다. 이게 bid/ask 로
    새어 들어가면 $1 짜리 지정가가 나간다."""
    def handler(_: httpx.Request) -> httpx.Response:
        return us_quote_response(close=8.65)

    client = make_us_client(handler, live_trading=False)
    quote = fetch_quote_us(client, "WEN")
    assert quote is not None
    assert quote.bid == 0.0 and quote.ask == 0.0
    # 그래서 기준가는 현재가로 물러선다.
    assert reference_price(quote, Side.BUY) == 8.65


def test_거래정지_종목은_확인을_받는다():
    def handler(_: httpx.Request) -> httpx.Response:
        return us_quote_response(suspend="Y")

    client = make_us_client(handler, live_trading=False)
    quote = fetch_quote_us(client, "WEN")
    assert quote is not None
    assert "거래정지" in quote.halt


def json_body(request: httpx.Request) -> dict:
    import json

    return json.loads(request.content.decode("utf-8"))
