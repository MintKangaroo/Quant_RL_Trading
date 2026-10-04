"""국내 상장 ETF 매도세 — 증권거래세 면제·농특세 없음(``accounting.transaction_tax_kr_etf``). 주식 요율은 그대로."""

from __future__ import annotations

from datetime import UTC, datetime

from quant_rl_trading.accounting.book import KRW, Side
from quant_rl_trading.accounting.rates import Rates
from quant_rl_trading.collectors.benchmark_etf import BENCHMARK_ETF

AS_OF = datetime(2026, 10, 5, 7, 0, tzinfo=UTC)


def test_ETF_매도엔_세금이_없고_주식은_그대로다(store) -> None:
    store.seed_config_defaults()
    rates = Rates.from_store(store, as_of=AS_OF)
    assert rates.transaction_tax_kr_etf == 0.0

    fee, tax = rates.costs(side=Side.SELL, gross=1_000_000.0, currency=KRW, entity_id=BENCHMARK_ETF)
    assert tax == 0.0 and fee == 1_000_000.0 * rates.fee_kr
    _, stock_tax = rates.costs(side=Side.SELL, gross=1_000_000.0, currency=KRW, entity_id="KR:005930")
    assert stock_tax == 1_000_000.0 * rates.transaction_tax_kr > 0
    _, untagged = rates.costs(side=Side.SELL, gross=1_000_000.0, currency=KRW)
    assert untagged == stock_tax, "ID 를 안 주는 옛 호출은 옛 요율"
    assert rates.priced(entity_id=BENCHMARK_ETF, side=Side.SELL, quantity=10, price=100.0).tax == 0.0


def test_키가_없는_시점이면_주식_요율로_본다() -> None:
    """모르면 비싼 쪽 — 숫자를 지어내지 않는다."""
    rates = Rates(fee_kr=0.0001, fee_us=0.0, transaction_tax_kr=0.0015, dividend_tax_kr=0.0, dividend_tax_us=0.0,
                  capital_gains_us=0.0, capital_gains_allowance_krw=0.0)
    _, tax = rates.costs(side=Side.SELL, gross=1_000.0, currency=KRW, entity_id=BENCHMARK_ETF)
    assert tax == 1_000.0 * 0.0015
