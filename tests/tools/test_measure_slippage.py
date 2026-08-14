"""tools/measure_slippage.py — M3 완료 기준 5번의 측정기.

여기서 못 박는 것 셋:

1. **시뮬레이션 체결로는 PASS 가 나오지 않는다.** `replay/fills.py` 가 만든
   체결로 그 모델을 검증하면 정의상 오차 0 이다. 자기 자신과 비교해 놓고
   모델이 맞았다고 적는 일을 막는다.
2. **종가 0 세션이 예측값을 조용히 지우지 않는다.** close=0 이 창에 섞이면
   pct_change 가 inf 를 내고 std 가 NaN 이 된다. NaN 은 예외를 내지 않고
   퍼지므로, 막아 두지 않으면 "비교 불가" 가 "기준 밖" 으로 둔갑한다.
3. **방향 부호.** 비싸게 산 것과 싸게 판 것이 둘 다 양의 비용으로 잡혀야 한다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from quant_rl_trading.store import Store
from tools.measure_slippage import measure, render

AS_OF = datetime(2026, 8, 20, tzinfo=UTC)


def _sessions(n: int) -> list[datetime]:
    return [datetime(2026, 8, 3, tzinfo=UTC) + timedelta(days=i) for i in range(n)]


def seed_prices(store: Store, *, closes: list[float] | None = None) -> list[datetime]:
    sessions = _sessions(10)
    values = closes or [1000.0 + i * 5 for i in range(len(sessions))]
    rows = [
        {
            "entity_id": "KR:000890",
            "valid_from": session,
            "observed_at": session,
            "source": "test",
            "market": "KR",
            "open": value,
            "high": value * 1.01,
            "low": value * 0.99,
            "close": value,
            "volume": 100_000.0,
            "value": value * 100_000.0,
            "adj_factor": 1.0,
        }
        for session, value in zip(sessions, values, strict=True)
    ]
    store.append("prices", rows, ingest_run_id="prices-test", source="test")
    return sessions


def seed_trade(
    store: Store, *, session: datetime, source: str, price: float, side: str = "buy"
) -> None:
    store.append(
        "trades",
        [
            {
                "entity_id": "KR:000890",
                "valid_from": session,
                "observed_at": session,
                "source": source,
                "market": "KR",
                "side": side,
                "quantity": 1_000.0,
                "price": price,
                "currency": "KRW",
                "fee": 0.0,
                "tax": 0.0,
                "order_id": f"{source}-1",
            }
        ],
        ingest_run_id=f"trades-{source}-{side}",
        source=source,
    )


def seed_config(store: Store) -> None:
    """실제 기본 임계치를 쓴다. 손으로 심은 값으로 재면 모델이 아니라 픽스처를 재는 것이다."""
    store.seed_config_defaults()


def test_시뮬레이션_체결만_있으면_통과를_내지_않는다(store: Store) -> None:
    seed_config(store)
    sessions = seed_prices(store)
    seed_trade(store, session=sessions[-1], source="backtest", price=1_046.0)

    measurements, notes = measure(
        store, as_of=AS_OF, market="KR", lookback=30, source="broker"
    )

    assert measurements == []
    assert any("자기순환" in note for note in notes)

    _, code = render(measurements, notes, source="broker")
    # 0 이면 안 된다. 실계좌 체결이 없는데 완료 기준이 켜졌다고 읽힌다.
    assert code == 2


def test_종가0_세션이_예측값을_NaN_으로_지우지_않는다(store: Store) -> None:
    seed_config(store)
    closes = [1000.0 + i * 5 for i in range(10)]
    closes[3] = 0.0  # 실제 창고에 있는 고장 — inf 하나가 std 를 통째로 NaN 으로 만든다
    sessions = seed_prices(store, closes=closes)
    seed_trade(store, session=sessions[-1], source="broker", price=1_046.0)

    measurements, _ = measure(
        store, as_of=AS_OF, market="KR", lookback=30, source="broker"
    )

    assert len(measurements) == 1
    predicted = measurements[0].predicted_bps
    assert predicted == predicted, "예측이 NaN 이다 — 종가 0 세션이 새어 들어왔다"
    assert predicted > 0


@pytest.mark.parametrize(
    ("side", "fill_price"),
    # 기준가는 결정일(= 체결 전날) 종가 1,040 이다.
    # 비싸게 산 것과 싸게 판 것, 둘 다 우리에게 불리한 체결이다.
    [("buy", 1_050.0), ("sell", 1_030.0)],
)
def test_방향과_무관하게_불리한_체결이_양의_비용이다(
    store: Store, side: str, fill_price: float
) -> None:
    seed_config(store)
    sessions = seed_prices(store)
    seed_trade(store, session=sessions[-1], source="broker", price=fill_price, side=side)

    measurements, _ = measure(
        store, as_of=AS_OF, market="KR", lookback=30, source="broker"
    )

    assert len(measurements) == 1
    assert measurements[0].reference == pytest.approx(1_040.0)
    assert measurements[0].realized_bps > 0, "불리한 체결이 음의 비용으로 잡혔다 — 부호가 뒤집혔다"
