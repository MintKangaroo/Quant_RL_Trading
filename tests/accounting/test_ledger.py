"""장부 재구성과 일간 스냅샷 — 진짜 창고 위에서.

여기서 고정하는 것은 **장부가 기록의 함수라는 성질**이다. 같은 as_of 로 두 번
접으면 같은 장부가 나와야 하고, 그래야 리플레이가 회계까지 재현한다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from lattice.accounting import KRW, USD, Rates, snapshot
from lattice.accounting import ledger as ledger_module
from lattice.replay.clock import ReplayClock

DAY1 = datetime(2026, 3, 2, 6, 40, tzinfo=UTC)   # 한국시간 15:40
DAY2 = DAY1 + timedelta(days=1)
DAY3 = DAY1 + timedelta(days=2)


def rates(store, as_of):  # type: ignore[no-untyped-def]
    return Rates.from_store(store, as_of=as_of)


@pytest.fixture
def funded(store):  # type: ignore[no-untyped-def]
    """1,000만원 입금 + 원달러 환율."""
    store.seed_config_defaults()
    store.append(
        "fx",
        [{
            "entity_id": "FX:USDKRW", "valid_from": day, "observed_at": day,
            "source": "test", "rate": 1_350.0,
        } for day in (DAY1, DAY2, DAY3)],
        ingest_run_id="fx-seed",
    )
    store.append(
        "capital_flows",
        [{
            "entity_id": "FUND", "valid_from": DAY1, "observed_at": DAY1,
            "source": "test", "currency": KRW, "amount": 10_000_000.0, "kind": "deposit",
        }],
        ingest_run_id="flow-seed",
    )
    return store


def price_rows(entity: str, day: datetime, close: float) -> list[dict]:
    return [{
        "entity_id": entity, "valid_from": day, "observed_at": day,
        "source": "test", "market": "KR",
        "open": close, "high": close, "low": close, "close": close,
        "volume": 1_000.0, "value": close * 1_000.0, "adj_factor": None,
    }]


def test_장부는_기록의_함수다(funded) -> None:
    """같은 as_of 로 두 번 접으면 같은 장부. 리플레이가 회계까지 재현한다."""
    funded.append(
        "trades",
        [{
            "entity_id": "KR:005930", "valid_from": DAY1, "observed_at": DAY1,
            "source": "test", "market": "KR", "side": "buy",
            "quantity": 100.0, "price": 70_000.0, "currency": KRW,
            "fee": 1_050.0, "tax": 0.0, "order_id": "o-1",
        }],
        ingest_run_id="t-1",
    )

    first = ledger_module.build_book(funded, as_of=DAY2, rates=rates(funded, DAY2))
    second = ledger_module.build_book(funded, as_of=DAY2, rates=rates(funded, DAY2))

    assert first == second
    assert first.positions["KR:005930"].quantity == 100.0
    assert first.cash[KRW] == pytest.approx(10_000_000.0 - 7_000_000.0 - 1_050.0)


def test_환율이_없으면_1로_때우지_않는다(store) -> None:
    """1.0 으로 때우면 해외분이 1/1350 로 평가되어 NAV 가 무너지고, 그 낙폭이
    킬스위치를 건다. 없으면 없다고 말하는 편이 낫다."""
    store.seed_config_defaults()

    with pytest.raises(LookupError, match="환율이 없다"):
        ledger_module.fx_rate(store, as_of=DAY1)


def test_배당락일에_안_갖고_있었으면_배당이_없다(funded) -> None:
    funded.append(
        "dividends",
        [{
            "entity_id": "KR:005930", "valid_from": DAY1, "observed_at": DAY1,
            "source": "test", "market": "KR", "currency": KRW,
            "per_share": 1_000.0, "tax_rate": 0.154, "pay_date": None,
        }],
        ingest_run_id="d-1",
    )

    book = ledger_module.build_book(funded, as_of=DAY2, rates=rates(funded, DAY2))
    assert book.accrued_dividend[KRW] == 0.0


def test_첫날은_지수가_기준값이고_수익률이_0이다(funded) -> None:
    clock = ReplayClock(DAY1)
    result = snapshot.take(funded, clock, as_of=DAY1)

    assert result.twr_return == 0.0
    assert result.index_value == 100.0
    assert result.valuation.nav == pytest.approx(10_000_000.0)


def test_입금한_날_TWR이_0이고_지수가_그대로다(funded) -> None:
    """**이 프로젝트에서 입금은 반드시 일어난다** (자본 증액 게이트).

    입금일을 수익으로 잡으면 성과 곡선 전체가 거짓이 된다.
    """
    clock = ReplayClock(DAY1)
    snapshot.write(funded, clock, snapshot=snapshot.take(funded, clock, as_of=DAY1))

    # 다음 날 500만원 추가 입금. 가격 변동은 없다.
    funded.append(
        "capital_flows",
        [{
            "entity_id": "FUND", "valid_from": DAY2, "observed_at": DAY2,
            "source": "test", "currency": KRW, "amount": 5_000_000.0, "kind": "deposit",
        }],
        ingest_run_id="flow-2",
    )

    result = snapshot.take(funded, ReplayClock(DAY2), as_of=DAY2)

    assert result.valuation.nav == pytest.approx(15_000_000.0)
    assert result.inflow == pytest.approx(5_000_000.0)
    assert result.twr_return == pytest.approx(0.0)
    assert result.index_value == pytest.approx(100.0)


def test_평가익은_지수에_반영된다(funded) -> None:
    funded.append(
        "trades",
        [{
            "entity_id": "KR:005930", "valid_from": DAY1, "observed_at": DAY1,
            "source": "test", "market": "KR", "side": "buy",
            "quantity": 100.0, "price": 100_000.0, "currency": KRW,
            "fee": 0.0, "tax": 0.0, "order_id": "o-1",
        }],
        ingest_run_id="t-1",
    )
    funded.append("prices", price_rows("KR:005930", DAY1, 100_000.0), ingest_run_id="p-1")
    clock = ReplayClock(DAY1)
    snapshot.write(funded, clock, snapshot=snapshot.take(funded, clock, as_of=DAY1))

    # +10%. 보유 1,000만원 중 1,000만원이 주식이므로 NAV 도 +10%.
    funded.append("prices", price_rows("KR:005930", DAY2, 110_000.0), ingest_run_id="p-2")
    result = snapshot.take(funded, ReplayClock(DAY2), as_of=DAY2)

    assert result.twr_return == pytest.approx(0.10)
    assert result.index_value == pytest.approx(110.0)
    assert result.drawdown == pytest.approx(0.0)


def test_낙폭은_누적지수로_잰다(funded) -> None:
    """떨어진 다음 날 입금해도 낙폭은 지워지지 않는다."""
    funded.append(
        "trades",
        [{
            "entity_id": "KR:005930", "valid_from": DAY1, "observed_at": DAY1,
            "source": "test", "market": "KR", "side": "buy",
            "quantity": 100.0, "price": 100_000.0, "currency": KRW,
            "fee": 0.0, "tax": 0.0, "order_id": "o-1",
        }],
        ingest_run_id="t-1",
    )
    funded.append("prices", price_rows("KR:005930", DAY1, 100_000.0), ingest_run_id="p-1")
    clock = ReplayClock(DAY1)
    snapshot.write(funded, clock, snapshot=snapshot.take(funded, clock, as_of=DAY1))

    # −30%
    funded.append("prices", price_rows("KR:005930", DAY2, 70_000.0), ingest_run_id="p-2")
    day2 = snapshot.take(funded, ReplayClock(DAY2), as_of=DAY2)
    snapshot.write(funded, ReplayClock(DAY2), snapshot=day2)
    assert day2.drawdown == pytest.approx(-0.30)

    # 다음 날 큰돈을 넣는다. NAV 는 회복되지만 **낙폭은 그대로여야 한다.**
    funded.append(
        "capital_flows",
        [{
            "entity_id": "FUND", "valid_from": DAY3, "observed_at": DAY3,
            "source": "test", "currency": KRW, "amount": 100_000_000.0, "kind": "deposit",
        }],
        ingest_run_id="flow-3",
    )
    funded.append("prices", price_rows("KR:005930", DAY3, 70_000.0), ingest_run_id="p-3")
    day3 = snapshot.take(funded, ReplayClock(DAY3), as_of=DAY3)

    assert day3.valuation.nav > 100_000_000.0
    assert day3.drawdown == pytest.approx(-0.30)


def test_같은_날을_두_번_쓰지_않는다(funded) -> None:
    clock = ReplayClock(DAY1)
    result = snapshot.take(funded, clock, as_of=DAY1)

    assert snapshot.write(funded, clock, snapshot=result) == 1
    assert snapshot.write(funded, clock, snapshot=result) == 0
