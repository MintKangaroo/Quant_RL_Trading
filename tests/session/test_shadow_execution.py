"""shadow 가 하루 한 프로세스로 나뉘어 돌 때도 체결이 되는지 — 회귀 테스트.

**사고 경위.** ``tools/run_session.py`` 는 하루마다 새 프로세스로 뜬다. 그
안에서 ``loop.run(start=day, end=day, warmup_days=0)`` 을 부르면 그 호출의
``previous_session`` 은 시작부터 끝까지 ``None`` 이다 — 체결 단계가 "어제 낸
주문" 을 찾을 ``previous_session`` 자체가 없어 **한 번도 불리지 않는다**
(``backtest/loop.py`` 의 4단계 순서 참고). 화면에는 "체결 0" 으로만 뜨고, 그
날짜 수만큼 반복돼도 사고가 날 코드(``execution.run``)를 한 번도 지나가지
않으니 "10거래일 무사고" 는 무사고가 아니라 미검증이었다.

고친 값은 ``warmup_days=1`` 이다 — 전날을 같이 굴려 그 호출 안에서
``previous_session`` 이 생기게 한다. 전날의 결정·신호는 이미 창고에 있어
``ingest_run_id`` 로 다시 쓰지 않는다(중복 무시), 그래서 비용은 얕고 부작용은
없다.
"""

from __future__ import annotations

from datetime import date, timedelta
from zoneinfo import ZoneInfo

import pytest

from quant_rl_trading.backtest import loop
from quant_rl_trading.collectors.market_hours import Market, trading_days

SEOUL = ZoneInfo("Asia/Seoul")

START = date(2026, 8, 3)
DAY_TWO = date(2026, 8, 4)
ENTITIES = ["KR:000100", "KR:000200", "KR:000300"]


def _moment(day: date):
    from datetime import datetime

    return datetime.combine(day, loop.DEFAULT_SNAPSHOT_TIME, tzinfo=SEOUL)


@pytest.fixture
def warehouse(store):  # type: ignore[no-untyped-def]
    """3종목 · 400세션 이력 — shadow 가 읽는 신호는 이미 창고에 있다고 가정한다."""
    store.seed_config_defaults()
    history = [START - timedelta(days=offset) for offset in range(400, -1, -1)]
    sessions = trading_days(Market.KR, START, DAY_TWO)

    store.append(
        "fx",
        [
            {
                "entity_id": "FX:USDKRW", "valid_from": _moment(day),
                "observed_at": _moment(day), "source": "test", "rate": 1_350.0,
            }
            for day in [START - timedelta(days=offset) for offset in range(400, -10, -1)]
        ],
        ingest_run_id="fx-seed",
    )

    universe_rows = []
    price_rows = []
    for index, day in enumerate(history + sessions):
        moment = _moment(day)
        for offset, entity in enumerate(ENTITIES):
            universe_rows.append({
                "entity_id": entity, "valid_from": moment, "observed_at": moment,
                "source": "test", "market": "KR", "name": entity,
                "is_listed": True, "is_tradable": True, "delisted_on": None,
            })
            close = 10_000.0 + index * (3 + offset) + offset * 500
            price_rows.append({
                "entity_id": entity, "valid_from": moment, "observed_at": moment,
                "source": "test", "market": "KR",
                "open": close, "high": close, "low": close, "close": close,
                "volume": 500_000.0, "value": close * 500_000.0, "adj_factor": None,
            })
    store.append("universe", universe_rows, ingest_run_id="u-seed")
    store.append("prices", price_rows, ingest_run_id="p-seed")

    # shadow 는 자기 신호를 만들지 않는다(produce_signals=False) — 일일
    # 실행기가 이미 쌓아 둔 것을 읽는다고 가정하고, START·DAY_TWO 몫까지
    # 미리 심어 둔다.
    past = [*trading_days(Market.KR, START - timedelta(days=140), START), *sessions]
    store.append(
        "signals",
        [
            {
                "entity_id": entity, "valid_from": _moment(day),
                "observed_at": _moment(day), "source": "test", "analyst": "fundamental",
                "analyst_version": "fundamental-v0.1.0",
                "score": 0.2 + 0.3 * offset, "confidence": 1.0, "horizon_days": 5,
                "features_hash": "x", "evidence_json": "[]", "latency_ms": 1.0,
            }
            for day in past
            for offset, entity in enumerate(ENTITIES)
        ],
        ingest_run_id="sig-seed",
    )

    measured = _moment(START - timedelta(days=30))
    store.append(
        "analyst_weights",
        [{
            "entity_id": "fundamental", "valid_from": measured, "observed_at": measured,
            "source": "test", "market": "KR", "ic": 0.077, "weight": 1.0,
        }],
        ingest_run_id="w-seed",
    )
    return store


def _run_day(store, day: date, *, warmup_days: int, capital: float = 0.0):
    """``tools/run_session.py`` 가 하는 것과 같은 모양 — 하루짜리 ``loop.run``."""
    return loop.run(
        store, start=day, end=day, market="KR", capital=capital,
        warmup_days=warmup_days, produce_signals=False,
    )


def test_워밍업_없이_하루씩_나눠_돌리면_영원히_체결_0이다(warehouse) -> None:
    """고치기 전 모양 — 사고가 재현되는지 먼저 확인한다."""
    day_one = _run_day(warehouse, START, warmup_days=0, capital=100_000_000.0)
    assert day_one.days[-1].planned_orders > 0  # 주문은 난다

    day_two = _run_day(warehouse, DAY_TWO, warmup_days=0)
    # 어제 주문이 있는데도, 이 호출의 previous_session 이 처음부터 None 이라
    # 체결 단계 자체가 안 불린다.
    assert day_two.days[-1].filled == 0
    assert day_two.days[-1].requested == 0


def test_전날을_워밍업으로_같이_돌리면_어제_주문이_오늘_체결된다(warehouse) -> None:
    """고친 모양 — ``run_session.py`` 가 실제로 쓰는 ``warmup_days=1``."""
    day_one = _run_day(warehouse, START, warmup_days=0, capital=100_000_000.0)
    assert day_one.days[-1].planned_orders > 0

    day_two = _run_day(warehouse, DAY_TWO, warmup_days=1)
    entry = day_two.days[-1]
    assert entry.as_of.date() == DAY_TWO
    assert entry.requested > 0
    assert entry.filled > 0

    trades = warehouse.get("trades", as_of=entry.as_of, lookback=30)
    assert not trades.empty
    assert (trades["valid_from"].dt.date == DAY_TWO).all()
