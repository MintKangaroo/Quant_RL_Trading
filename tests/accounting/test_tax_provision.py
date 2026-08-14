"""해외 양도세 충당금 — 세후 NAV 가 세전의 복사본이 아니라는 것을 고정한다.

## 무엇이 틀렸었나

`Rates.capital_gains_provision` 도 `Book.with_tax_provision` 도 있었지만
`ledger.build_book` 이 **한 번도 부르지 않았다.** `Book.tax_provision` 이
영원히 0 이라 `nav_after_tax` 가 `nav` 를 그대로 베껴 보고했다. 미장 실현손익이
거의 없어 안 드러났을 뿐, 미장이 돌기 시작하면 화면·리포트의 "세후" 숫자가
전부 거짓이 된다.

그래서 이 파일의 첫 테스트는 **배선 전이라면 실패하는 모양**으로 쓴다 —
"충당금이 0 이 아니다" 가 사고의 재현이다.

나머지는 틀리는 방식들을 하나씩 막는다: 공제를 안 주는 것, 공제를 전 기간에
한 번만 주는 것, 국내분에 붙이는 것, NAV 에서 빼 버리는 것, 손실인 해에
음수로 가는 것.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from quant_rl_trading.accounting import KRW, USD, Rates, snapshot
from quant_rl_trading.accounting import ledger as ledger_module

DAY1 = datetime(2026, 3, 2, 6, 40, tzinfo=UTC)   # 한국시간 15:40
DAY2 = DAY1 + timedelta(days=1)
DAY3 = DAY1 + timedelta(days=2)

FX = 1_350.0
ALLOWANCE = 2_500_000.0
TAX_RATE = 0.22


def rates(store, as_of):  # type: ignore[no-untyped-def]
    return Rates.from_store(store, as_of=as_of)


@pytest.fixture
def funded(store):  # type: ignore[no-untyped-def]
    """원화 1,000만 + 달러 10만. 환율은 전 구간 1,350 으로 고정한다.

    환율을 날마다 바꾸면 "충당금이 왜 이 값인가" 를 눈으로 검산할 수 없다.
    환율 시점 규칙은 별도 테스트에서 따로 잡는다.
    """
    store.seed_config_defaults()
    store.append(
        "fx",
        [{
            "entity_id": "FX:USDKRW", "valid_from": day, "observed_at": day,
            "source": "test", "rate": FX,
        } for day in (DAY1, DAY2, DAY3)],
        ingest_run_id="fx-seed",
    )
    store.append(
        "capital_flows",
        [
            {
                "entity_id": "FUND", "valid_from": DAY1, "observed_at": DAY1,
                "source": "test", "currency": KRW, "amount": 10_000_000.0,
                "kind": "deposit",
            },
            {
                # 같은 계좌·같은 ``valid_from`` 은 창고에서 한 행으로 접힌다
                # (자연키). 통화가 둘이면 시각을 갈라 줘야 둘 다 남는다.
                "entity_id": "FUND", "valid_from": DAY1 + timedelta(minutes=1),
                "observed_at": DAY1 + timedelta(minutes=1),
                "source": "test", "currency": USD, "amount": 100_000.0,
                "kind": "deposit",
            },
        ],
        ingest_run_id="flow-seed",
    )
    return store


def trade_row(
    entity: str,
    day: datetime,
    *,
    side: str,
    quantity: float,
    price: float,
    currency: str,
    order_id: str,
) -> dict:
    return {
        "entity_id": entity, "valid_from": day, "observed_at": day,
        "source": "test", "market": "US" if currency == USD else "KR",
        "side": side, "quantity": quantity, "price": price, "currency": currency,
        # 수수료 0. 비용이 섞이면 충당금이 손으로 검산되지 않는다.
        "fee": 0.0, "tax": 0.0, "order_id": order_id,
    }


def round_trip(store, *, buy_price: float, sell_price: float, currency: str = USD) -> None:
    """1,000 주를 사고 판다. 실현손익 = (매도가 − 매수가) × 1,000."""
    store.append(
        "trades",
        [
            trade_row(
                "US:AAPL" if currency == USD else "KR:005930",
                DAY1, side="buy", quantity=1_000.0, price=buy_price,
                currency=currency, order_id="o-buy",
            ),
            trade_row(
                "US:AAPL" if currency == USD else "KR:005930",
                DAY2, side="sell", quantity=1_000.0, price=sell_price,
                currency=currency, order_id="o-sell",
            ),
        ],
        ingest_run_id="t-round-trip",
    )


# -- 사고 재현 --------------------------------------------------------------------


def test_미장_실현이익이_나면_충당금이_쌓인다(funded) -> None:
    """**배선 전에는 이 테스트가 깨진다** — `tax_provision` 이 영원히 0 이었다.

    실현이익 $10,000 × 1,350 = 13,500,000원. 공제 250만원을 빼면 1,100만원이
    과세대상이고, 22% 는 2,420,000원이다.
    """
    round_trip(funded, buy_price=100.0, sell_price=110.0)

    book = ledger_module.build_book(funded, as_of=DAY3, rates=rates(funded, DAY3))

    expected = (10_000.0 * FX - ALLOWANCE) * TAX_RATE
    assert book.realized_pnl[USD] == pytest.approx(10_000.0)
    assert book.tax_provision == pytest.approx(expected)
    assert book.tax_provision > 0.0


def test_세후_NAV가_세전의_복사본이_아니다(funded) -> None:
    """화면·리포트가 읽는 값이 실제로 갈라지는지 — 사고가 드러나는 지점이다."""
    round_trip(funded, buy_price=100.0, sell_price=110.0)

    result = snapshot.take(funded, None, as_of=DAY3)  # type: ignore[arg-type]

    expected = (10_000.0 * FX - ALLOWANCE) * TAX_RATE
    assert result.valuation.tax_provision == pytest.approx(expected)
    assert result.valuation.nav_after_tax == pytest.approx(result.valuation.nav - expected)
    assert result.valuation.nav_after_tax < result.valuation.nav


# -- 공제 -------------------------------------------------------------------------


def test_공제_한도_안이면_충당금이_0이다(funded) -> None:
    """실현이익 $1,000 × 1,350 = 1,350,000원 < 250만원. 낼 세금이 없다."""
    round_trip(funded, buy_price=100.0, sell_price=101.0)

    book = ledger_module.build_book(funded, as_of=DAY3, rates=rates(funded, DAY3))

    assert book.realized_pnl[USD] == pytest.approx(1_000.0)
    assert book.tax_provision == 0.0


def test_한도를_넘으면_넘은_만큼에만_세율이_붙는다(funded) -> None:
    """전액에 세율을 매기면 공제가 없는 것과 같다 — 250만원어치 과대계상."""
    round_trip(funded, buy_price=100.0, sell_price=110.0)

    book = ledger_module.build_book(funded, as_of=DAY3, rates=rates(funded, DAY3))
    realized_krw = 10_000.0 * FX

    assert book.tax_provision == pytest.approx((realized_krw - ALLOWANCE) * TAX_RATE)
    assert book.tax_provision != pytest.approx(realized_krw * TAX_RATE)


def test_공제는_해마다_새로_준다(funded) -> None:
    """전 기간 누적에 공제를 한 번만 주면 이듬해 이익이 통째로 과세된다.

    2026 년과 2027 년에 각각 $10,000 을 실현한다. 연도별로 공제를 받으므로
    충당금은 **한 해치의 두 배**여야 한다.
    """
    next_year = datetime(2027, 3, 2, 6, 40, tzinfo=UTC)
    funded.append(
        "fx",
        [{
            "entity_id": "FX:USDKRW", "valid_from": day, "observed_at": day,
            "source": "test", "rate": FX,
        } for day in (next_year, next_year + timedelta(days=1))],
        ingest_run_id="fx-2027",
    )
    round_trip(funded, buy_price=100.0, sell_price=110.0)
    funded.append(
        "trades",
        [
            trade_row("US:MSFT", next_year, side="buy", quantity=1_000.0,
                      price=100.0, currency=USD, order_id="o-buy-2"),
            trade_row("US:MSFT", next_year + timedelta(days=1), side="sell",
                      quantity=1_000.0, price=110.0, currency=USD, order_id="o-sell-2"),
        ],
        ingest_run_id="t-2027",
    )

    as_of = next_year + timedelta(days=2)
    book = ledger_module.build_book(funded, as_of=as_of, rates=rates(funded, as_of))

    one_year = (10_000.0 * FX - ALLOWANCE) * TAX_RATE
    assert book.tax_provision == pytest.approx(2 * one_year)


# -- 대상 -------------------------------------------------------------------------


def test_국내_실현이익에는_붙지_않는다(funded) -> None:
    """국내 양도차익은 (대주주가 아닌 한) 비과세다. 매도 시점에 증권거래세가
    이미 붙었고, 여기에 22% 를 또 매기면 이중과세다."""
    round_trip(funded, buy_price=100_000.0, sell_price=110_000.0, currency=KRW)

    book = ledger_module.build_book(funded, as_of=DAY3, rates=rates(funded, DAY3))

    assert book.realized_pnl[KRW] == pytest.approx(10_000_000.0)
    assert book.tax_provision == 0.0


# -- 손실 -------------------------------------------------------------------------


def test_손실이_나도_충당금이_음수로_가지_않는다(funded) -> None:
    """손실 난 해에 세금을 돌려받지는 않는다. 음수면 세후 NAV 가 세전보다
    커져서, 세금이 수익원인 것처럼 보인다."""
    round_trip(funded, buy_price=110.0, sell_price=100.0)

    book = ledger_module.build_book(funded, as_of=DAY3, rates=rates(funded, DAY3))

    assert book.realized_pnl[USD] == pytest.approx(-10_000.0)
    assert book.tax_provision == 0.0


def test_뒤에_난_손실이_그_해_충당금을_되돌린다(funded) -> None:
    """양도세는 **연간 통산**이다. 앞선 이익에 쌓은 충당금을 뒤의 손실이
    지우지 못하면, 본전으로 끝난 해에 세금이 남아 있게 된다."""
    funded.append(
        "trades",
        [
            trade_row("US:AAPL", DAY1, side="buy", quantity=1_000.0,
                      price=100.0, currency=USD, order_id="o-1"),
            trade_row("US:AAPL", DAY2, side="sell", quantity=1_000.0,
                      price=110.0, currency=USD, order_id="o-2"),
            trade_row("US:MSFT", DAY2, side="buy", quantity=1_000.0,
                      price=100.0, currency=USD, order_id="o-3"),
            trade_row("US:MSFT", DAY3, side="sell", quantity=1_000.0,
                      price=90.0, currency=USD, order_id="o-4"),
        ],
        ingest_run_id="t-win-then-loss",
    )

    # 이익만 실현된 시점(DAY2). 손실 체결은 아직 관측되지 않았다.
    mid = ledger_module.build_book(funded, as_of=DAY2, rates=rates(funded, DAY2))
    assert mid.tax_provision > 0.0

    as_of = DAY3 + timedelta(days=1)
    funded.append(
        "fx",
        [{
            "entity_id": "FX:USDKRW", "valid_from": as_of, "observed_at": as_of,
            "source": "test", "rate": FX,
        }],
        ingest_run_id="fx-day4",
    )
    final = ledger_module.build_book(funded, as_of=as_of, rates=rates(funded, as_of))

    assert final.realized_pnl[USD] == pytest.approx(0.0)
    assert final.tax_provision == 0.0


# -- NAV 불변 --------------------------------------------------------------------


def test_충당금은_NAV를_줄이지_않는다(funded) -> None:
    """일간 NAV 에 넣으면 매도할 때마다 NAV 가 튀고, 보상 함수가 그 계단을
    실제 손실로 오인한다 (accounting.md §5). 학습은 세전을 본다."""
    round_trip(funded, buy_price=100.0, sell_price=110.0)

    book = ledger_module.build_book(funded, as_of=DAY3, rates=rates(funded, DAY3))
    taxed = snapshot.take(funded, None, as_of=DAY3)  # type: ignore[arg-type]

    # 현금만 남았다. NAV 는 원화 1,000만 + 달러(10만 + 실현이익 1만) × 1,350.
    expected_nav = 10_000_000.0 + 110_000.0 * FX
    assert book.positions == {}
    assert taxed.valuation.nav == pytest.approx(expected_nav)
    assert taxed.valuation.tax_provision > 0.0


# -- 결정론 -----------------------------------------------------------------------


def test_같은_기록에서_같은_충당금이_나온다(funded) -> None:
    """충당금이 누적 상태(연도별 실현손익)를 들고 계산되므로, 접는 순서가
    새면 값이 달라진다. 장부는 기록의 함수여야 한다 (불변식 5)."""
    round_trip(funded, buy_price=100.0, sell_price=110.0)

    first = ledger_module.build_book(funded, as_of=DAY3, rates=rates(funded, DAY3))
    second = ledger_module.build_book(funded, as_of=DAY3, rates=rates(funded, DAY3))

    assert first == second
    assert first.tax_provision == second.tax_provision


def test_공제액은_설정에서_읽는다(funded) -> None:
    """하드코딩하면 화면·리포트가 다른 공제로 계산한다 (불변식 10)."""
    read = rates(funded, DAY3)

    assert read.capital_gains_allowance_krw == pytest.approx(ALLOWANCE)
    assert (
        funded.config("accounting.capital_gains_allowance_krw", as_of=DAY3)
        == pytest.approx(ALLOWANCE)
    )
