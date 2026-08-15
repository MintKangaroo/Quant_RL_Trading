"""broker/fills.py — 체결 확인·기록.

네트워크는 쓰지 않는다 — ``httpx.MockTransport`` 로 t0425 응답을 스텁한다.
고정하는 것 넷:

1. 부분체결이 정상 경로다 — 누적치의 차이만 새 체결로 적힌다.
2. 같은 체결을 두 번 관측해도 장부가 두 배로 늘지 않는다.
3. 조회 실패·응답에 없음·필드 파싱 실패는 "체결 0" 이 아니라 "모른다" 다.
4. 실전 체결 행이 백테스트 ``trades`` 와 같은 스키마로 들어가 회계가 읽는다.
"""

from __future__ import annotations

import json

import httpx
import pytest

from quant_rl_trading.accounting import ledger as ledger_module
from quant_rl_trading.accounting.book import Side as BookSide
from quant_rl_trading.accounting.rates import Rates
from quant_rl_trading.broker.fills import FillState, PendingFill, sync_fills
from quant_rl_trading.collectors.ls_client import LSClient, LSCredentials
from quant_rl_trading.replay.clock import ReplayClock
from quant_rl_trading.schemas.order import Side

CREDS = LSCredentials(appkey="key", appsecret="secret", base_url="https://api.test")


def token_response() -> httpx.Response:
    return httpx.Response(
        200, json={"access_token": "tok", "token_type": "Bearer", "expires_in": 86400}
    )


def make_client(handler, ts, *, live_trading: bool = True) -> LSClient:  # type: ignore[no-untyped-def]
    def routed(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return token_response()
        return handler(request)

    return LSClient(
        credentials=CREDS,
        clock=ReplayClock(ts(2026, 8, 14, 10)),
        transport=httpx.MockTransport(routed),
        live_trading=live_trading,
        sleep=lambda _: None,
    )


def t0425_response(rows: list[dict]) -> httpx.Response:
    return httpx.Response(200, json={"rsp_cd": "00000", "t0425OutBlock1": rows})


def pending(*, order_id: str = "order-1", broker_order_no: str = "700001") -> PendingFill:
    return PendingFill(
        order_id=order_id,
        # 종목 코드가 아니라 **시장 접두어가 붙은 entity_id** 다. 프로덕션은
        # `planned.order.entity_id`(=`KR:005930`)를 그대로 나른다 —
        # 접두어 없는 값은 창고가 거부한다(TableSpec.market_prefixed_entity).
        entity_id="KR:005930",
        side=Side.BUY,
        market="KR",
        broker_order_no=broker_order_no,
        requested_quantity=100,
    )


@pytest.fixture
def funded_store(store):  # type: ignore[no-untyped-def]
    store.seed_config_defaults()
    return store


# -- 부분체결: 누적치의 차이만 적힌다 ----------------------------------------


def test_partial_fill_records_only_the_delta(funded_store, ts) -> None:  # type: ignore[no-untyped-def]
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return t0425_response(
            [{"ordno": "0700001", "cheqty": "30", "cheprice": "70000", "ordqty": "100"}]
        )

    client = make_client(handler, ts)
    as_of = ts(2026, 8, 14, 10)

    result = sync_fills(funded_store, client, ReplayClock(as_of), as_of=as_of, pending=[pending()])

    assert len(calls) == 1
    assert result.rows_written == 1
    [outcome] = result.outcomes
    assert outcome.state is FillState.RECORDED
    assert outcome.fill is not None
    assert outcome.fill.quantity == 30
    assert outcome.fill.price == 70000
    # 매수는 거래세가 없다 — 수수료만.
    rates = Rates.from_store(funded_store, as_of=as_of)
    expected_fee, expected_tax = rates.costs(
        side=BookSide.BUY, gross=30 * 70000, currency="KRW"
    )
    assert outcome.fill.fee == pytest.approx(expected_fee)
    assert outcome.fill.tax == pytest.approx(expected_tax) == 0.0

    frame = funded_store.get("trades", as_of=as_of)
    assert len(frame) == 1
    assert frame.iloc[0]["order_id"] == "order-1#30"
    assert frame.iloc[0]["quantity"] == 30.0


def test_next_poll_records_only_the_new_quantity(funded_store, ts) -> None:  # type: ignore[no-untyped-def]
    """체결이 30 → 100 으로 늘면, 두 번째 폴링은 70 만 새로 적는다."""
    cumulative = {"qty": 30}

    def handler(request: httpx.Request) -> httpx.Response:
        return t0425_response(
            [
                {
                    "ordno": "0700001",
                    "cheqty": str(cumulative["qty"]),
                    "cheprice": "70000",
                    "ordqty": "100",
                }
            ]
        )

    client = make_client(handler, ts)
    as_of = ts(2026, 8, 14, 10)
    clock = ReplayClock(as_of)

    first = sync_fills(funded_store, client, clock, as_of=as_of, pending=[pending()])
    assert first.recorded[0].fill.quantity == 30

    cumulative["qty"] = 100
    second = sync_fills(funded_store, client, clock, as_of=as_of, pending=[pending()])
    assert second.rows_written == 1
    assert second.recorded[0].fill.quantity == 70

    # 장부는 100주 매수만 안다 — 30+70 이 30+100 으로 겹쳐 세어지지 않는다.
    book = ledger_module.build_book(
        funded_store, as_of=as_of, rates=Rates.from_store(funded_store, as_of=as_of)
    )
    assert book.positions["KR:005930"].quantity == 100.0


def test_repeated_observation_of_same_cumulative_state_does_not_double_count(
    funded_store, ts
) -> None:  # type: ignore[no-untyped-def]
    """재시작·재시도로 같은 누적 상태를 두 번 관측해도 장부는 한 번만 늘어난다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return t0425_response(
            [{"ordno": "0700001", "cheqty": "30", "cheprice": "70000", "ordqty": "100"}]
        )

    client = make_client(handler, ts)
    as_of = ts(2026, 8, 14, 10)
    clock = ReplayClock(as_of)

    first = sync_fills(funded_store, client, clock, as_of=as_of, pending=[pending()])
    second = sync_fills(funded_store, client, clock, as_of=as_of, pending=[pending()])

    assert first.rows_written == 1
    assert second.rows_written == 0
    assert second.outcomes[0].state is FillState.UNCHANGED

    frame = funded_store.get("trades", as_of=as_of)
    assert len(frame) == 1  # 두 번째 관측은 아무 행도 더 만들지 않았다


# -- "모른다" ------------------------------------------------------------------


def test_query_failure_is_unknown_not_zero(funded_store, ts) -> None:  # type: ignore[no-untyped-def]
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    client = make_client(handler, ts)
    as_of = ts(2026, 8, 14, 10)

    result = sync_fills(funded_store, client, ReplayClock(as_of), as_of=as_of, pending=[pending()])

    assert result.rows_written == 0
    assert len(result.unknown) == 1
    assert result.unknown[0].state is FillState.UNKNOWN
    assert funded_store.get("trades", as_of=as_of).empty


def test_order_absent_from_broker_response_is_unknown(funded_store, ts) -> None:  # type: ignore[no-untyped-def]
    def handler(request: httpx.Request) -> httpx.Response:
        return t0425_response([])  # 브로커가 이 주문을 모른다

    client = make_client(handler, ts)
    as_of = ts(2026, 8, 14, 10)

    result = sync_fills(funded_store, client, ReplayClock(as_of), as_of=as_of, pending=[pending()])

    assert result.rows_written == 0
    assert result.unknown[0].order_id == "order-1"


def test_unparseable_fields_are_unknown(funded_store, ts) -> None:  # type: ignore[no-untyped-def]
    def handler(request: httpx.Request) -> httpx.Response:
        return t0425_response([{"ordno": "0700001"}])  # cheqty/cheprice 자체가 없다

    client = make_client(handler, ts)
    as_of = ts(2026, 8, 14, 10)

    result = sync_fills(funded_store, client, ReplayClock(as_of), as_of=as_of, pending=[pending()])

    assert result.rows_written == 0
    assert result.unknown[0].state is FillState.UNKNOWN


def test_paper_mode_is_unknown_and_never_touches_network(funded_store, ts) -> None:  # type: ignore[no-untyped-def]
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return t0425_response([])

    client = make_client(handler, ts, live_trading=False)
    as_of = ts(2026, 8, 14, 10)

    result = sync_fills(funded_store, client, ReplayClock(as_of), as_of=as_of, pending=[pending()])

    assert calls == []  # t0425 는 PAPER_ALLOWED_TR 에 없다 — 나가지도 않는다
    assert result.unknown[0].detail.startswith("paper")


def test_zero_filled_quantity_is_unchanged_not_unknown(funded_store, ts) -> None:  # type: ignore[no-untyped-def]
    """아직 하나도 안 채워진 주문 — 0 은 결측이 아니라 유효한 사실이다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return t0425_response(
            [{"ordno": "0700001", "cheqty": "0", "cheprice": "0", "ordqty": "100"}]
        )

    client = make_client(handler, ts)
    as_of = ts(2026, 8, 14, 10)

    result = sync_fills(funded_store, client, ReplayClock(as_of), as_of=as_of, pending=[pending()])

    assert result.rows_written == 0
    assert result.outcomes[0].state is FillState.UNCHANGED


# -- 스키마: 실전 체결도 backtest 와 같은 표에 같은 모양으로 -----------------


def test_live_fill_row_matches_backtest_trades_schema(funded_store, ts) -> None:  # type: ignore[no-untyped-def]
    """회계(``accounting/ledger.py``)가 그대로 읽을 수 있어야 한다 — 불변식 5."""

    def handler(request: httpx.Request) -> httpx.Response:
        return t0425_response(
            [{"ordno": "0700001", "cheqty": "10", "cheprice": "50000", "ordqty": "10"}]
        )

    client = make_client(handler, ts)
    as_of = ts(2026, 8, 14, 10)

    sync_fills(funded_store, client, ReplayClock(as_of), as_of=as_of, pending=[pending()])

    frame = funded_store.get("trades", as_of=as_of)
    required = {"market", "side", "quantity", "price", "currency", "fee", "tax", "order_id"}
    assert required.issubset(set(frame.columns))

    # ledger 가 예외 없이 접는다는 것 자체가 스키마 호환의 증거다.
    book = ledger_module.build_book(
        funded_store, as_of=as_of, rates=Rates.from_store(funded_store, as_of=as_of)
    )
    assert book.positions["KR:005930"].quantity == 10.0


def test_fee_and_tax_come_from_config_not_broker_response(funded_store, ts) -> None:  # type: ignore[no-untyped-def]
    """t0425 응답에는 수수료·세금 필드가 없다 — 매도는 거래세가 붙어야 한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return t0425_response(
            [{"ordno": "0700002", "cheqty": "10", "cheprice": "50000", "ordqty": "10"}]
        )

    client = make_client(handler, ts)
    as_of = ts(2026, 8, 14, 10)
    sell = pending(order_id="sell-1", broker_order_no="700002")
    sell = PendingFill(
        order_id=sell.order_id,
        entity_id=sell.entity_id,
        side=Side.SELL,
        market=sell.market,
        broker_order_no=sell.broker_order_no,
        requested_quantity=sell.requested_quantity,
    )

    result = sync_fills(funded_store, client, ReplayClock(as_of), as_of=as_of, pending=[sell])

    rates = Rates.from_store(funded_store, as_of=as_of)
    from quant_rl_trading.accounting.book import Side as BookSide

    expected_fee, expected_tax = rates.costs(side=BookSide.SELL, gross=10 * 50000, currency="KRW")
    assert expected_tax > 0.0  # 매도라 거래세가 붙는다는 전제 확인
    fill = result.recorded[0].fill
    assert fill.fee == pytest.approx(expected_fee)
    assert fill.tax == pytest.approx(expected_tax)
