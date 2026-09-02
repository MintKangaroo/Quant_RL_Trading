

def test_최소주문금액은_시장_통화로_나눈다(store) -> None:
    """`execution.min_order_value` 는 원 단위다. 미장은 환율로 나눠야 $100,000 미만이
    전부 소액으로 걸러지는 일이 없다 (2026-09-02 미장 shadow 주문 0건)."""
    from datetime import UTC, datetime

    import pytest

    from quant_rl_trading.executor.sizing import SizingParams

    store.seed_config_defaults()
    as_of = datetime(2026, 9, 2, 0, 0, tzinfo=UTC)
    krw = SizingParams.from_store(store, as_of=as_of)
    usd = SizingParams.from_store(store, as_of=as_of, fx_rate=1_377.0)
    assert usd.min_order_value == pytest.approx(krw.min_order_value / 1_377.0)
    assert usd.max_adv_ratio == krw.max_adv_ratio
    with pytest.raises(ValueError):
        SizingParams.from_store(store, as_of=as_of, fx_rate=0.0)
