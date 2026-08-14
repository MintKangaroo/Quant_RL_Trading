"""**레버리지가 1.0 을 넘지 않는다.** 사고의 직접 회귀 테스트.

`tests/executor/test_cash_constraint.py` 가 계산을 못 박는다면, 여기는 그
계산이 **실제 루프에 배선돼 있는지**를 본다. 둘 다 필요하다 — 사고는 계산이
틀려서가 아니라 `session/daily.py` 가 주문가능금액을 아예 넘기지 않아서
났다. 순수 함수만 고치고 배선을 빠뜨리면 테스트는 통과하고 백테스트는 그대로
레버리지를 쓴다.

실제로 그랬던 구간(2026-01-02 ~ 03-13):

    2026-01-12   현금  -1,805,838   레버리지 1.02
    2026-03-13   현금 -199,627,918   레버리지 2.83

현금이 한 번 음수로 내려간 뒤 **한 번도 돌아오지 않았다.** 그래서 여기서는
"마지막에 괜찮다" 가 아니라 **매 세션** 괜찮은지를 본다.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from quant_rl_trading.accounting import ledger as ledger_module
from quant_rl_trading.accounting.book import KRW
from quant_rl_trading.accounting.rates import Rates
from quant_rl_trading.backtest import loop
from quant_rl_trading.collectors.market_hours import Market, trading_days

pytestmark = pytest.mark.invariant

SEOUL = ZoneInfo("Asia/Seoul")

#: 사고는 하루가 아니라 **쌓여서** 났다. 며칠만 돌리면 현금이 아직 남아 있어
#: 제약이 걸리지도 않고, 그러면 이 테스트는 아무것도 지키지 않는다.
START = date(2026, 6, 1)
END = date(2026, 7, 31)
CAPITAL = 100_000_000.0
ENTITIES = [f"KR:{index:06d}" for index in range(8)]


def _moment(day: date) -> datetime:
    return datetime.combine(day, loop.DEFAULT_SNAPSHOT_TIME, tzinfo=SEOUL)


@pytest.fixture
def warehouse(store):  # type: ignore[no-untyped-def]
    """8종목. 후보가 매 세션 갈려야 포지션이 쌓이는 모양이 나온다."""
    store.seed_config_defaults()
    history = [START - timedelta(days=offset) for offset in range(400, -1, -1)]

    store.append(
        "fx",
        [
            {
                "entity_id": "FX:USDKRW", "valid_from": _moment(day),
                "observed_at": _moment(day), "source": "test", "rate": 1_350.0,
            }
            for day in [START - timedelta(days=offset) for offset in range(400, -70, -1)]
        ],
        ingest_run_id="fx-seed",
    )

    universe_rows = []
    price_rows = []
    for index, day in enumerate(history + trading_days(Market.KR, START, END)):
        moment = _moment(day)
        for offset, entity in enumerate(ENTITIES):
            universe_rows.append({
                "entity_id": entity, "valid_from": moment, "observed_at": moment,
                "source": "test", "market": "KR", "name": entity,
                "is_listed": True, "is_tradable": True, "delisted_on": None,
            })
            # 종목마다 다른 주기로 흔들린다 — 순위가 바뀌어야 후보가 갈리고,
            # 후보가 갈려야 "빠진 종목이 남는" 사고의 모양이 재현된다.
            wave = 1.0 + 0.05 * ((index * (offset + 2)) % 17) / 17.0
            close = (10_000.0 + offset * 500) * wave
            price_rows.append({
                "entity_id": entity, "valid_from": moment, "observed_at": moment,
                "source": "test", "market": "KR",
                "open": close, "high": close, "low": close, "close": close,
                "volume": 500_000.0, "value": close * 500_000.0, "adj_factor": None,
            })
    store.append("universe", universe_rows, ingest_run_id="u-seed")
    store.append("prices", price_rows, ingest_run_id="p-seed")

    past = trading_days(Market.KR, START - timedelta(days=140), START)
    store.append(
        "signals",
        [
            {
                "entity_id": entity, "valid_from": _moment(day),
                "observed_at": _moment(day), "source": "test", "analyst": "risk",
                "analyst_version": "risk-v0.1.0",
                "score": 0.2 + 0.3 * offset, "confidence": 1.0, "horizon_days": 5,
                "features_hash": "x", "evidence_json": "[]", "latency_ms": 1.0,
            }
            for day in past
            if day < START
            for offset, entity in enumerate(ENTITIES)
        ],
        ingest_run_id="sig-seed",
    )

    measured = _moment(START - timedelta(days=30))
    store.append(
        "analyst_weights",
        [{
            "entity_id": "risk", "valid_from": measured, "observed_at": measured,
            "source": "test", "market": "KR", "ic": 0.077, "weight": 1.0,
        }],
        ingest_run_id="w-seed",
    )
    return store


def _cash_and_gross(store, day) -> tuple[float, float]:  # type: ignore[no-untyped-def]
    rates = Rates.from_store(store, as_of=day.as_of)
    book = ledger_module.build_book(store, as_of=day.as_of, rates=rates)
    return float(book.cash.get(KRW, 0.0)), day.nav - float(book.cash.get(KRW, 0.0))


def test_cash_never_goes_negative(warehouse) -> None:
    """**없는 돈으로 사지 않는다.** 사고에서 무너진 바로 그 성질이다."""
    result = loop.run(warehouse, start=START, end=END, market="KR", capital=CAPITAL)

    offenders = []
    for day in result.days:
        cash, _ = _cash_and_gross(warehouse, day)
        if cash < -1.0:  # 반올림 오차만 허용한다
            offenders.append((day.as_of.date(), round(cash)))

    assert not offenders, f"현금이 음수로 내려간 세션: {offenders[:5]}"


def test_leverage_never_exceeds_one(warehouse) -> None:
    """주식평가액이 NAV 를 넘지 않는다. 넘으면 빌린 것이다."""
    result = loop.run(warehouse, start=START, end=END, market="KR", capital=CAPITAL)

    worst = 0.0
    for day in result.days:
        _, gross = _cash_and_gross(warehouse, day)
        if day.nav > 0:
            worst = max(worst, gross / day.nav)

    assert worst <= 1.01, f"레버리지 {worst:.2f}배 — 자본을 넘겨 샀다"


def test_the_run_actually_traded(warehouse) -> None:
    """위 둘은 **아무것도 안 사면 저절로 통과한다.**

    매매 0건이면 현금은 자본금 그대로고 레버리지는 0 이다. 그건 완벽한
    성적이 아니라 미검증이다 — 실제로 샀는지 먼저 못 박는다.
    """
    result = loop.run(warehouse, start=START, end=END, market="KR", capital=CAPITAL)

    assert sum(day.filled for day in result.days) > 0, "체결이 0건이면 이 파일은 아무것도 지키지 않는다"
    assert any(day.candidates for day in result.days)
