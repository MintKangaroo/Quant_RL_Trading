"""휴장일 거부는 다음 거래일에 다시 낸다 — 다른 거부는 그대로 막는다 (2026-09-28).

9/24 추석에 LS 모의투자가 전부 ``rsp_cd=01410 모의투자 영업일이 아닙니다`` 로 거부했다. 9/28 재조정은
같은 세션(KR-2026-09-23)이라 ``submit-<order_id>`` 중복 가드가 "이미 시도했다" 로 70건을 조용히 건너뛰었고
세션은 rc=0 으로 끝났다. 여기서 고정하는 것:

1. 휴장일 거부로 끝난 조각은 **거부된 현지 날짜보다 뒤인 날에** 다시 나간다 (세션·``release_slices`` 둘 다).
2. 같은 날엔 다시 안 낸다 — 같은 이유로 또 거부될 뿐이다.
3. 다른 거부(증거금 부족 등)는 여전히 다시 안 낸다.
4. 건너뛴 조각이 시장에 닿지 않은 것이면 ``ExecutionResult.unsent`` 에 담긴다(실행기가 rc 5 로 낸다).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from tests.account_fixture import fund_account

from quant_rl_trading.broker import Ack, RejectedOrder
from quant_rl_trading.executor import Target, pipeline
from quant_rl_trading.executor.orders import PlannedOrder, session_id
from quant_rl_trading.replay.clock import ReplayClock

NOW = datetime(2026, 8, 12, 1, 0, tzinfo=UTC)  # 한국시간 수 10:00
NEXT_DAY = NOW + timedelta(days=1)             # 목 10:00
HOLIDAY = "TR CSPAT00601 rsp_cd=01410 msg=모의투자 영업일이 아닙니다"


@dataclass
class Broker:
    """``reject`` 가 있으면 모든 주문을 그 사유로 거부한다. 없으면 정상 전송."""

    reject: str = ""
    submitted: list[str] = field(default_factory=list)

    def submit(self, order: PlannedOrder, *, as_of: datetime) -> Ack:
        self.submitted.append(order.order_id)
        if self.reject:
            raise RejectedOrder(self.reject)
        return Ack(order_id=order.order_id, accepted=True, sent=True, broker_order_no="7")

    def cancel(self, *, broker_order_no: str, entity_id: str, quantity: int) -> Ack:
        return Ack(order_id=broker_order_no, accepted=True, sent=False)

    def modify(self, *, broker_order_no: str, entity_id: str, quantity: int, price: float) -> Ack:
        return Ack(order_id=broker_order_no, accepted=True, sent=False)


@pytest.fixture
def seeded(store):  # type: ignore[no-untyped-def]
    store.seed_config_defaults()
    fund_account(store, NOW)
    store.append(
        "config",
        [{
            "entity_id": "execution.slice_interval_sec", "valid_from": NOW - timedelta(days=30),
            "observed_at": NOW - timedelta(days=30), "source": "test", "value_json": "0",
        }],
        ingest_run_id="cfg-slice-interval-0",
    )
    rows = []
    for offset in range(5):
        day = NOW - timedelta(days=offset)
        rows.append({
            "entity_id": "KR:A", "valid_from": day, "observed_at": day, "source": "test",
            "market": "KR", "open": 1_000.0, "high": 1_000.0, "low": 1_000.0, "close": 1_000.0,
            "volume": 1e6, "value": 1e9, "adj_factor": None,
        })
    store.append("prices", rows, ingest_run_id="p-seed")
    return store


def _run(store, clock_at: datetime, broker: Broker) -> pipeline.ExecutionResult:  # type: ignore[no-untyped-def]
    # 세션 as_of 는 그대로(같은 세션 id) — 휴장을 건너뛴 재조정이 정확히 이 모양이었다.
    return pipeline.run(
        store, ReplayClock(clock_at), as_of=NOW, market="KR",
        targets=[Target("KR:A", weight=0.10, price=1_000.0, adv_value=1e9)],
        holdings={}, equity=10_000_000.0, broker=broker,
    )


def _latest(store, at: datetime) -> dict[int, dict]:  # type: ignore[no-untyped-def]
    frame = store.get("orders", as_of=at, lookback=5)
    frame = frame[frame["session_id"] == session_id(as_of=NOW, market="KR")]
    return {int(r["slice_seq"]): r for r in frame.to_dict(orient="records")}


def test_휴장일_거부는_다음_거래일에_다시_나간다(seeded) -> None:
    first = _run(seeded, NOW, Broker(reject=HOLIDAY))
    planned = [item.order_id for item in first.planned]
    assert planned and all(not ack.accepted for ack in first.acks)

    broker = Broker()
    second = _run(seeded, NEXT_DAY, broker)

    assert broker.submitted == planned
    assert second.unsent == ()
    assert second.acks and all(ack.sent for ack in second.acks)
    statuses = {row["status"] for row in _latest(seeded, NEXT_DAY + timedelta(minutes=1)).values()}
    assert statuses == {"sent"}


def test_재전송은_한_번만_나간다(seeded) -> None:
    """재전송 claim 은 revision 에 묶여 있다 — 같은 날 다시 돌려도 두 번 나가지 않는다."""
    _run(seeded, NOW, Broker(reject=HOLIDAY))
    broker = Broker()
    _run(seeded, NEXT_DAY, broker)
    again = _run(seeded, NEXT_DAY + timedelta(minutes=5), broker)

    assert len(broker.submitted) == len(again.planned)
    assert again.unsent == ()  # 이미 나갔다 — "안 보낸" 게 아니다


def test_같은_날엔_휴장일_거부를_다시_내지_않는다(seeded) -> None:
    first = _run(seeded, NOW, Broker(reject=HOLIDAY))
    broker = Broker()
    second = _run(seeded, NOW + timedelta(hours=2), broker)

    assert broker.submitted == []
    assert set(second.unsent) == {item.order_id for item in first.planned}


def test_다른_거부는_다음_날에도_다시_내지_않고_unsent_로_알린다(seeded) -> None:
    first = _run(seeded, NOW, Broker(reject="TR CSPAT00601 rsp_cd=02714 msg=주문가능금액 부족"))
    broker = Broker()
    second = _run(seeded, NEXT_DAY, broker)

    assert broker.submitted == []
    assert set(second.unsent) == {item.order_id for item in first.planned}
    assert any("계획했는데 안 보낸 조각" in note for note in second.notes)


def test_release_slices_도_휴장일_거부_조각을_다음_날_고른다(seeded) -> None:
    from tools.release_slices import _planned_rows

    _run(seeded, NOW, Broker(reject=HOLIDAY))
    session = session_id(as_of=NOW, market="KR")

    same_day = _planned_rows(seeded, as_of=NOW + timedelta(hours=1), session_id=session, market="KR")
    next_day = _planned_rows(seeded, as_of=NEXT_DAY, session_id=session, market="KR")

    assert same_day.empty
    assert not next_day.empty and set(next_day["status"]) == {"rejected"}


# -- 판별 --------------------------------------------------------------------------


def _row(reason: str, observed_at: datetime, status: str = "rejected") -> dict:
    return {"status": status, "reason": reason, "observed_at": observed_at, "entity_id": "KR:A"}


def test_휴장일_거부_판별() -> None:
    weekday = NOW  # 2026-08-12 수
    assert pipeline.is_holiday_rejection(_row(f"거부 — {HOLIDAY}", weekday))
    assert pipeline.is_holiday_rejection(_row("rsp_cd=01410", weekday))
    assert not pipeline.is_holiday_rejection(_row("거부 — rsp_cd=02714 주문가능금액 부족", weekday))
    assert not pipeline.is_holiday_rejection(_row(f"거부 — {HOLIDAY}", weekday, status="sent"))
    # 사유가 빈 옛 행(9/26 전)은 거부 시각의 현지 날짜가 휴장일인지로 가른다 — 2026-09-24 추석.
    chuseok = datetime(2026, 9, 23, 23, 40, tzinfo=UTC)  # 한국시간 9/24 08:40
    assert pipeline.is_holiday_rejection(_row("", chuseok))
    assert not pipeline.is_holiday_rejection(_row("", datetime(2026, 9, 22, 23, 40, tzinfo=UTC)))


def test_재전송_시점_판별() -> None:
    chuseok = datetime(2026, 9, 23, 23, 40, tzinfo=UTC)  # 한국시간 9/24 08:40
    row = _row(f"거부 — {HOLIDAY}", chuseok)
    assert not pipeline.holiday_retry_due(row, now=chuseok + timedelta(hours=3), market="KR")
    assert pipeline.holiday_retry_due(row, now=datetime(2026, 9, 27, 23, 40, tzinfo=UTC), market="KR")
    assert not pipeline.holiday_retry_due(None, now=chuseok, market="KR")
