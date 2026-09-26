"""Actual exposure follows partial fills, and repeated reconciliation is idempotent."""

from datetime import UTC, datetime, timedelta

import pytest

from quant_rl_trading.accounting import weights
from quant_rl_trading.backtest.loop import seed_capital
from quant_rl_trading.executor import pipeline
from quant_rl_trading.replay.clock import ReplayClock

NOW = datetime(2026, 9, 9, 1, tzinfo=UTC)
SESSION = "KR-2026-09-09"


def test_partial_fill_updates_realized_exposure_without_counting_unfilled_quantity(store):
    store.seed_config_defaults()
    clock = ReplayClock(NOW)
    seed_capital(store, clock, amount=1e6, as_of=NOW - timedelta(days=1))
    store.append(
        "fx",
        [
            {
                "entity_id": "FX:USDKRW",
                "valid_from": NOW,
                "observed_at": NOW,
                "source": "test",
                "rate": 1350.0,
            }
        ],
        ingest_run_id="fx",
    )
    store.append(
        "prices",
        [
            {
                "entity_id": "KR:A",
                "valid_from": NOW,
                "observed_at": NOW,
                "source": "test",
                "market": "KR",
                "close": 1000.0,
            }
        ],
        ingest_run_id="price",
    )
    pipeline.record_realized_weights(
        store,
        clock,
        holdings={},
        equity=1e6,
        targets=[pipeline.Target("KR:A", weight=0.1, price=1000, adv_value=1e9)],
        as_of=NOW,
        market="KR",
        session=SESSION,
    )

    def trade(side, quantity, number):
        clock.advance(timedelta(seconds=1))
        store.append(
            "trades",
            [
                {
                    "entity_id": "KR:A",
                    "valid_from": clock.now(),
                    "observed_at": clock.now(),
                    "source": "test",
                    "market": "KR",
                    "side": side,
                    "quantity": quantity,
                    "price": 1000.0,
                    "currency": "KRW",
                    "fee": 0.0,
                    "tax": 0.0,
                    "order_id": f"{SESSION}|KR:A|{number}",
                }
            ],
            ingest_run_id=f"fill-{number}",
        )

    trade("buy", 40.0, 1)  # 100 intended shares, only 40 executed
    assert weights.refresh(store, clock, as_of=clock.now(), sessions={SESSION}) == 1
    actual = store.get("realized_weights", as_of=clock.now()).iloc[0]
    assert actual["realized_weight"] == pytest.approx(0.04)
    assert pipeline.action_reflection_rate(store, as_of=clock.now()) == pytest.approx(0.4)
    assert weights.refresh(store, clock, as_of=clock.now(), sessions={SESSION}) == 0
    trade("buy", 60.0, 2)
    assert weights.refresh(store, clock, as_of=clock.now(), sessions={SESSION}) == 1
    assert store.get("realized_weights", as_of=clock.now()).iloc[0][
        "realized_weight"
    ] == pytest.approx(0.1)
    trade("sell", 60.0, 3)
    assert weights.refresh(store, clock, as_of=clock.now(), sessions={SESSION}) == 1
    assert store.get("realized_weights", as_of=clock.now()).iloc[0][
        "realized_weight"
    ] == pytest.approx(0.04)


def test_시세가_NaN_인_보유는_건너뛰고_대사를_죽이지_않는다(store, caplog):
    """**이게 대사 도구를 죽였다.** `snapshot.last_prices` 는 창 안에 쓸 수 있는
    종가가 없는 종목을 아예 빼고 돌려준다. 그걸 `prices[entity]` 로 바로 꺼내면
    KeyError, NaN 이면 `json.dumps(allow_nan=False)` 가 ValueError 다.

    `weights.refresh` 는 `broker.fills.sync` 가 trades 를 적은 **뒤에** 불리므로,
    여기서 터지면 장부는 남고 주문 상태만 낡은 채 남는다.

    0 으로 채우지 않는다 — 그러면 "그 종목을 안 들고 있다" 는 거짓이 된다.
    건너뛰고 그 사실을 로그로 내보낸다.
    """
    import logging

    store.seed_config_defaults()
    clock = ReplayClock(NOW)
    seed_capital(store, clock, amount=1e6, as_of=NOW - timedelta(days=1))
    store.append(
        "fx",
        [{"entity_id": "FX:USDKRW", "valid_from": NOW, "observed_at": NOW,
          "source": "test", "rate": 1350.0}],
        ingest_run_id="fx",
    )
    store.append(
        "prices",
        [
            {"entity_id": "KR:A", "valid_from": NOW, "observed_at": NOW,
             "source": "test", "market": "KR", "close": 1000.0},
            # 거래정지로 종가가 비었다. 0 이 아니라 NaN 이다 — 걸러지지 않는다.
            {"entity_id": "KR:HALT", "valid_from": NOW, "observed_at": NOW,
             "source": "test", "market": "KR", "close": float("nan")},
        ],
        ingest_run_id="price",
    )
    for entity in ("KR:A", "KR:HALT"):
        pipeline.record_realized_weights(
            store, clock, holdings={}, equity=1e6,
            targets=[pipeline.Target(entity, weight=0.1, price=1000, adv_value=1e9)],
            as_of=NOW, market="KR", session=f"{SESSION}-{entity}",
        )
    for number, entity in enumerate(("KR:A", "KR:HALT"), start=1):
        clock.advance(timedelta(seconds=1))
        store.append(
            "trades",
            [{"entity_id": entity, "valid_from": clock.now(), "observed_at": clock.now(),
              "source": "test", "market": "KR", "side": "buy", "quantity": 100.0,
              "price": 1000.0, "currency": "KRW", "fee": 0.0, "tax": 0.0,
              "order_id": f"{SESSION}-{entity}|{entity}|{number}"}],
            ingest_run_id=f"fill-{number}",
        )

    sessions = {f"{SESSION}-KR:A", f"{SESSION}-KR:HALT"}
    with caplog.at_level(logging.WARNING, logger="quant_rl_trading.accounting.weights"):
        written = weights.refresh(store, clock, as_of=clock.now(), sessions=sessions)

    # 갱신은 포기했지만 예외로 죽지 않았다 — 대사는 계속 돈다.
    assert written == 0
    frame = store.get("realized_weights", as_of=clock.now())
    assert weights.SOURCE not in set(frame["source"])  # 조용히 0 을 적지 않았다
    # 사실이 남는다.
    assert "KR:HALT" in caplog.text and "포기" in caplog.text

    # 집행 직전 기록(0%)이 그대로 남는다 — 되먹임이 거짓 숫자로 덮이지 않았다.
    detail = pipeline.action_reflection_detail(store, as_of=clock.now())
    assert detail.rate == pytest.approx(0.0)
