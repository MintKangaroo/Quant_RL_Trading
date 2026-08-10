"""결정론 — 같은 as_of 로 두 번 실행하면 주문이 바이트 단위로 동일하다.

M1 의 핵심 산출물이다. 이게 깨지면 백테스트 결과를 두 번 재현할 수 없고,
재현되지 않는 성적표로는 무엇이 통했는지 알 수 없다.

벽시계(ts_wall)는 실행마다 당연히 다르다. 비교 대상은 주문·체결과
payload_hash 이지 ts_wall 이 아니다.
"""

from __future__ import annotations

import pandas as pd
import pytest

from lattice.replay import FillParams, MarketState, ReplayClock, run_session
from lattice.schemas.order import Order, Side

pytestmark = pytest.mark.invariant

PARAMS = FillParams(
    impact_k=0.1, participation_cap=0.03, liquidation_days=3, min_order_value=100_000.0
)
UNIVERSE = ("KR:005930", "KR:000660", "US:AAPL")


@pytest.fixture
def priced(store, ts):  # type: ignore[no-untyped-def]
    rows = []
    for offset, entity in enumerate(UNIVERSE):
        for day in range(1, 11):
            rows.append(
                {
                    "entity_id": entity,
                    "valid_from": ts(2024, 3, day),
                    "observed_at": ts(2024, 3, day, 9),
                    "source": "test",
                    "market": entity.split(":")[0],
                    "close": 1000.0 + day + offset * 100,
                    "volume": 1_000_000.0,
                }
            )
    store.append("prices", rows, ingest_run_id="prices")
    return store


def top_two_by_close(observation: pd.DataFrame, as_of) -> list[Order]:  # type: ignore[no-untyped-def]
    """결정론 검증용 최소 전략. M3 에서 Selector→Allocator→Executor 가 대신한다."""
    latest = observation.sort_values(["entity_id", "valid_from"]).groupby("entity_id").last()
    picks = latest.sort_values("close", ascending=False).head(2)
    return [
        Order(entity_id=str(entity), side=Side.BUY, quantity=500, reason="top-close")
        for entity in picks.index
    ]


def build_state(observation: pd.DataFrame, entity_id: str) -> MarketState:
    rows = observation[observation["entity_id"] == entity_id].sort_values("valid_from")
    last = rows.iloc[-1]
    return MarketState(
        entity_id=entity_id,
        close=float(last["close"]),
        volume=float(last["volume"]),
        adv=float(rows["volume"].mean()),
        volatility=0.02,
        lot_size=1,
        tick_size=1.0,
    )


def _run(store, as_of, run_id: str, wall=None):  # type: ignore[no-untyped-def]
    return run_session(
        store=store,
        clock=ReplayClock(as_of),
        run_id=run_id,
        strategy=top_two_by_close,
        market_state=build_state,
        params=PARAMS,
        # 벽시계까지 고정해야 테스트가 실행 시각에 의존하지 않는다.
        wall_clock=ReplayClock(wall) if wall else None,
    )


def test_same_as_of_yields_byte_identical_orders(priced, ts) -> None:  # type: ignore[no-untyped-def]
    as_of = ts(2024, 3, 10, 18)

    first = _run(priced, as_of, "run-1")
    second = _run(priced, as_of, "run-2")

    assert first.serialized() == second.serialized()
    assert first.serialized().encode("utf-8") == second.serialized().encode("utf-8")
    assert first.digest() == second.digest()


def test_different_as_of_yields_different_result(priced, ts) -> None:
    """결정론이 '항상 같은 답' 을 뜻하는 것은 아니다.

    이 테스트가 없으면, 아무 입력에나 빈 주문을 내는 구현도 결정론 테스트를
    통과한다. 그건 결정론이 아니라 고장이다.
    """
    early = _run(priced, ts(2024, 3, 3, 18), "run-early")
    late = _run(priced, ts(2024, 3, 10, 18), "run-late")

    assert early.orders, "이른 시점에도 주문은 나와야 한다"
    assert early.digest() != late.digest()


def test_event_payload_hashes_match_across_runs(priced, ts) -> None:  # type: ignore[no-untyped-def]
    as_of = ts(2024, 3, 10, 18)
    wall = ts(2026, 1, 1, 12)
    _run(priced, as_of, "run-a", wall)
    _run(priced, as_of, "run-b", wall)

    # 이벤트의 observed_at 은 ts_wall 이다. 리플레이를 언제 돌렸는지가 남는다.
    events = priced.get("events", as_of=wall)
    first = events[events["entity_id"] == "run-a"].sort_values("seq")
    second = events[events["entity_id"] == "run-b"].sort_values("seq")

    assert list(first["stage"]) == ["observe", "decide", "execute"]
    assert list(first["payload_hash"]) == list(second["payload_hash"])


def test_replay_clock_does_not_drift(ts) -> None:
    clock = ReplayClock(ts(2024, 3, 10, 18))

    assert clock.now() == clock.now() == ts(2024, 3, 10, 18)
