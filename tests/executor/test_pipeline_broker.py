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
from tests.account_fixture import fund_account

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
    fund_account(store, NOW)
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


def test_BrokerError_의_내용이_킬스위치_사유와_주문_행에_남는다(seeded) -> None:
    """2026-09-22 10:00:41 — 킬스위치는 걸렸는데 무엇이 실패했는지 어디에도 없었다. 원인을 적는다.

    주문 행은 여전히 ``submitting``(나갔는지 모른다)이어야 한다 — 내용을 적는다고 상태를 확정 짓지 않는다.
    """
    from quant_rl_trading.executor import guards

    order_id = _first_order_id()
    broker = FakeBroker(raises={order_id: BrokerError("ReadTimeout — 응답 없음")})
    pipeline.run(
        seeded, ReplayClock(NOW), as_of=NOW, market="KR", targets=targets(),
        holdings={}, equity=10_000_000.0, broker=broker,
    )
    state, reason = guards.killswitch_state(seeded, as_of=NOW + timedelta(minutes=1))
    assert str(state) == "engaged"
    assert order_id in reason and "ReadTimeout — 응답 없음" in reason and "KR:A" in reason

    orders = seeded.get("orders", as_of=NOW + timedelta(minutes=1), lookback=3)
    orders["oid"] = [client_order_id(session=a, entity_id=b, slice_seq=int(c))
                     for a, b, c in zip(orders["session_id"], orders["entity_id"], orders["slice_seq"], strict=True)]
    mine = orders[orders["oid"] == order_id].sort_values(["observed_at", "revision"])
    assert mine.iloc[-1]["status"] == "submitting"
    assert "ReadTimeout — 응답 없음" in mine.iloc[-1]["reason"]


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


def test_거부_사유가_주문_행에_남는다(seeded) -> None:
    """2026-09-23 KR 세션 — rejected 70건의 reason 이 전부 빈 문자열이었고,
    진짜 사유(``rsp_cd=01410 모의투자 영업일이 아닙니다``)는 릴리스 로그에만
    있었다. 로그는 순환 삭제되고 장부는 남는다."""
    order_id = _first_order_id()
    detail = "rsp_cd=01410 msg=모의투자 영업일이 아닙니다"
    broker = FakeBroker(raises={order_id: RejectedOrder(detail)})

    pipeline.run(
        seeded, ReplayClock(NOW), as_of=NOW, market="KR", targets=targets(),
        holdings={}, equity=10_000_000.0, broker=broker,
    )

    orders = seeded.get("orders", as_of=NOW + timedelta(minutes=1), lookback=3)
    orders["oid"] = [client_order_id(session=a, entity_id=b, slice_seq=int(c))
                     for a, b, c in zip(orders["session_id"], orders["entity_id"],
                                        orders["slice_seq"], strict=True)]
    mine = orders[orders["oid"] == order_id].sort_values(["observed_at", "revision"])
    assert mine.iloc[-1]["status"] == "rejected"
    assert detail in str(mine.iloc[-1]["reason"])


def test_전송하지_않은_Ack_의_사유도_장부에_남는다(seeded) -> None:
    """``live_trading`` 이 꺼져 안 나간 것과 거래소가 막은 것은 다른 사건이다.
    구분하려면 응답 코드·메시지가 장부에 있어야 한다."""

    @dataclass
    class NotLiveBroker:
        def submit(self, order: PlannedOrder, *, as_of: datetime) -> Ack:
            return Ack(
                order_id=order.order_id, accepted=True, sent=False,
                rsp_cd="00000", rsp_msg="execution.live_trading 꺼짐 — 전송하지 않았다",
            )

        def cancel(self, *, broker_order_no: str, entity_id: str, quantity: int) -> Ack:
            return Ack(order_id=broker_order_no, accepted=True, sent=False)

        def modify(
            self, *, broker_order_no: str, entity_id: str, quantity: int, price: float
        ) -> Ack:
            return Ack(order_id=broker_order_no, accepted=True, sent=False)

    pipeline.run(
        seeded, ReplayClock(NOW), as_of=NOW, market="KR", targets=targets(),
        holdings={}, equity=10_000_000.0, broker=NotLiveBroker(),
    )

    orders = seeded.get("orders", as_of=NOW + timedelta(minutes=1), lookback=3)
    latest = orders.sort_values(["observed_at", "revision"]).iloc[-1]
    assert "live_trading" in str(latest["reason"]) and "rsp_cd=00000" in str(latest["reason"])


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



def test_사이징_예산은_계좌_예약과_같은_식으로_깎인다(seeded, monkeypatch) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    """사이징은 기준가로 예산을 재고 계좌 예약은 지정가(기준가 × (1+슬리피지)) × (1+슬리피지, 추격 여유) × (1+수수료)로 잡아
    약 1% 가 어긋났다 — 현금이 빠듯한 세션마다 분할의 마지막 조각이 "insufficient unreserved KRW cash" 로 막혔다
    (7세션 중 4, 2026-09-26 점검). 사이징에 넘기는 주문가능금액을 같은 비율로 줄인다."""
    from quant_rl_trading.executor import pipeline as pipeline_module

    seen: dict[str, float] = {}
    original = pipeline_module.size_orders

    def spy(**kwargs):  # type: ignore[no-untyped-def]
        seen["cash"] = kwargs["cash"]
        return original(**kwargs)

    monkeypatch.setattr(pipeline_module, "size_orders", spy)
    pipeline.run(
        seeded, ReplayClock(NOW), as_of=NOW, market="KR", targets=targets(), holdings={},
        equity=10_000_000.0, cash=1_000_000.0, broker=FakeBroker(),
    )
    slip = float(seeded.config("execution.max_slippage", as_of=NOW))
    fee = float(seeded.config("accounting.fee_kr", as_of=NOW))
    assert seen["cash"] == pytest.approx(1_000_000.0 / ((1 + slip) ** 2 * (1 + fee)))
