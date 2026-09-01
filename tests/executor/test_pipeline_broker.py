"""파이프라인 ↔ 브로커 배선 계약 테스트.

`pipeline.py` 가 이제 ``Broker.submit`` 을 부른다. 여기서 고정하는 것은 넷이다.

1. **기본은 paper** — ``broker`` 를 안 주면 아무것도 나가지 않는다.
2. **적고 나서 보낸다** — 같은 세션을 두 번 돌려도 전송은 한 번만 나간다.
3. **BrokerError 뒤에는 재전송하지 않는다** — "나갔는지 모른다" 를 재시도로
   덮지 않는다.
4. **킬스위치가 걸리면 신규매수는 브로커까지 가지도 못한다** — 애초에
   ``planned`` 가 비므로 ``submit`` 이 불리지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from quant_rl_trading.broker import Ack, BrokerError, PaperBroker, RejectedOrder
from quant_rl_trading.executor import Target, engage, pipeline
from quant_rl_trading.executor.orders import PlannedOrder, client_order_id, session_id
from quant_rl_trading.replay.clock import ReplayClock

NOW = datetime(2026, 8, 12, 1, 0, tzinfo=UTC)   # 한국시간 10:00


@dataclass
class FakeBroker:
    """호출을 그대로 기록하는 스텁. 실제 네트워크는 어디서도 건드리지 않는다."""

    #: 여기 없는 order_id 는 정상 승인(sent=True). 있으면 그 예외를 던진다.
    raises: dict[str, Exception] = field(default_factory=dict)
    submitted: list[str] = field(default_factory=list)

    def submit(self, order: PlannedOrder, *, as_of: datetime) -> Ack:
        self.submitted.append(order.order_id)
        if order.order_id in self.raises:
            raise self.raises[order.order_id]
        return Ack(order_id=order.order_id, accepted=True, sent=True, broker_order_no="1")

    def cancel(self, *, broker_order_no: str, entity_id: str, quantity: int) -> Ack:
        return Ack(order_id=broker_order_no, accepted=True, sent=False)

    def modify(
        self, *, broker_order_no: str, entity_id: str, quantity: int, price: float
    ) -> Ack:
        return Ack(order_id=broker_order_no, accepted=True, sent=False)


@pytest.fixture
def seeded(store):  # type: ignore[no-untyped-def]
    store.seed_config_defaults()
    # **조각을 한꺼번에 내보내는 모드로 고정한다.** 이 파일의 시험 대상은
    # 멱등성과 거부 격리이고, 그 둘은 "여러 조각이 같은 회차에 나간다" 를
    # 전제로만 검증된다. 시간 분할(slice_interval_sec>0)이 켜지면 세션에서
    # 0번 조각만 나가 그 성질을 아예 못 본다 — 분할 자체는
    # test_slice_release.py 가 따로 지킨다.
    store.append(
        "config",
        [{
            "entity_id": "execution.slice_interval_sec", "valid_from": NOW - timedelta(days=30),
            "observed_at": NOW - timedelta(days=30), "source": "test",
            "value_json": "0",
        }],
        ingest_run_id="cfg-slice-interval-0",
    )
    rows = []
    for offset in range(5):
        day = NOW - timedelta(days=offset)
        rows.append({
            "entity_id": "KR:A", "valid_from": day, "observed_at": day,
            "source": "test", "market": "KR",
            "open": 1_000.0, "high": 1_000.0, "low": 1_000.0, "close": 1_000.0,
            "volume": 1e6, "value": 1e9, "adj_factor": None,
        })
    store.append("prices", rows, ingest_run_id="p-seed")
    return store


def targets() -> list[Target]:
    return [Target("KR:A", weight=0.10, price=1_000.0, adv_value=1e9)]


# -- 기본은 paper -------------------------------------------------------------


def test_broker를_안_주면_paper다_아무것도_안_나간다(seeded) -> None:
    clock = ReplayClock(NOW)
    result = pipeline.run(
        seeded, clock, as_of=NOW, market="KR", targets=targets(),
        holdings={}, equity=10_000_000.0,
    )

    assert result.planned
    assert result.acks
    assert all(not ack.sent for ack in result.acks)  # PaperBroker 는 sent=False


def test_paper_broker를_명시해도_같다(seeded) -> None:
    clock = ReplayClock(NOW)
    result = pipeline.run(
        seeded, clock, as_of=NOW, market="KR", targets=targets(),
        holdings={}, equity=10_000_000.0, broker=PaperBroker(),
    )

    assert all(not ack.sent for ack in result.acks)


# -- 적고 나서 보낸다 -----------------------------------------------------------


def test_같은_세션을_두_번_돌려도_전송은_한_번만_나간다(seeded) -> None:
    """**프로세스가 죽었다 살아나는 것은 반드시 일어난다.** 재시작 후 같은
    session_id 로 다시 돌려도 브로커에는 두 번째로 닿지 않는다."""
    broker = FakeBroker()
    clock = ReplayClock(NOW)

    first = pipeline.run(
        seeded, clock, as_of=NOW, market="KR", targets=targets(),
        holdings={}, equity=10_000_000.0, broker=broker,
    )
    assert first.acks
    assert len(broker.submitted) == len(first.planned)

    # 재시작을 흉내낸다 — 새 Clock, 같은 as_of/targets. record_orders 가
    # 이미 기록했으므로 planned 는 그대로 재구성되지만, submit_orders 는
    # 이미 "제출 시도" 기록이 있는 order_id 를 건너뛴다.
    second = pipeline.run(
        seeded, ReplayClock(NOW), as_of=NOW, market="KR", targets=targets(),
        holdings={}, equity=10_000_000.0, broker=broker,
    )

    assert second.acks == ()  # 이미 다 시도됐다 — 새로 보낸 것이 없다
    assert len(broker.submitted) == len(first.planned)  # 브로커 호출 횟수 불변


# -- BrokerError 뒤에는 재전송하지 않는다 -----------------------------------------


def _first_order_id(*, market: str = "KR", entity_id: str = "KR:A") -> str:
    session = session_id(as_of=NOW, market=market)
    return client_order_id(session=session, entity_id=entity_id, slice_seq=0)


def test_BrokerError_뒤에는_같은_주문을_다시_보내지_않는다(seeded) -> None:
    """**나갔는지 모르면 다시 보내면 안 된다.** 재시작해도 마찬가지다."""
    order_id = _first_order_id()
    broker = FakeBroker(raises={order_id: BrokerError("타임아웃 — 나갔는지 모른다")})

    pipeline.run(
        seeded, ReplayClock(NOW), as_of=NOW, market="KR", targets=targets(),
        holdings={}, equity=10_000_000.0, broker=broker,
    )
    assert order_id in broker.submitted
    calls_after_first = len(broker.submitted)

    # "재시작" — 새 Clock, 같은 세션. 다시 돌려도 이 주문은 브로커에 두 번째로
    # 닿지 않는다. (나머지 슬라이스는 이미 첫 호출에서 다 나갔으므로 이번에는
    # 아무것도 새로 불리지 않는다.)
    pipeline.run(
        seeded, ReplayClock(NOW), as_of=NOW, market="KR", targets=targets(),
        holdings={}, equity=10_000_000.0, broker=broker,
    )
    assert len(broker.submitted) == calls_after_first


def test_RejectedOrder는_거부로_기록되고_다른_슬라이스는_계속_나간다(seeded) -> None:
    """확실히 안 나간 주문만 거부로 갈리고, 나머지 슬라이스는 막히지 않는다."""
    order_id = _first_order_id()
    broker = FakeBroker(raises={order_id: RejectedOrder("증거금 부족")})

    result = pipeline.run(
        seeded, ReplayClock(NOW), as_of=NOW, market="KR", targets=targets(),
        holdings={}, equity=10_000_000.0, broker=broker,
    )

    assert len(result.acks) == len(result.planned)  # 거부도 ack 로 남는다
    rejected = [ack for ack in result.acks if ack.order_id == order_id]
    assert rejected and rejected[0].accepted is False and not rejected[0].sent
    # 나머지 슬라이스는 정상 전송됐다.
    assert any(ack.order_id != order_id and ack.sent for ack in result.acks)


# -- 킬스위치는 브로커까지 가지도 못하게 막는다 -----------------------------------


def test_킬스위치가_걸리면_브로커가_불리지_않는다(seeded) -> None:
    clock = ReplayClock(NOW)
    engage(
        seeded, as_of=NOW - timedelta(days=1), observed_at=NOW - timedelta(days=1),
        reason="낙폭 한계 초과", by="test",
    )
    broker = FakeBroker()

    result = pipeline.run(
        seeded, clock, as_of=NOW, market="KR", targets=targets(),
        holdings={}, equity=10_000_000.0, broker=broker,
    )

    assert result.planned == ()
    assert broker.submitted == []


# -- 8단계는 그대로 지켜진다 -----------------------------------------------------


def test_전송_배선_후에도_실현_비중_기록은_유지된다(seeded) -> None:
    clock = ReplayClock(NOW)
    result = pipeline.run(
        seeded, clock, as_of=NOW, market="KR", targets=targets(),
        holdings={}, equity=10_000_000.0, broker=PaperBroker(),
    )

    assert result.planned
    stored = seeded.get("realized_weights", as_of=NOW)
    assert len(stored) == 1
