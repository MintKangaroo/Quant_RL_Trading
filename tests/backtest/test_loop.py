"""백테스트 루프 계약 테스트 — 진짜 창고 위에서.

여기서 증명하는 것은 세 가지다.

1. **결정은 D, 체결은 D+1** — 첫날에는 체결이 있을 수 없다. 있으면 그건
   종가를 보고 그 종가에 산 것이다 (backtest.md §1)
2. **포지션은 장부에서만 온다** — 체결이 ``trades`` 로 남고, 다음 날 보유
   수량이 그 기록에서 다시 접힌다
3. **같은 구간을 두 번 돌리면 같은 지문** — 아니면 그 위의 MDD·IR 이 전부
   무의미해진다
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

import pytest

from quant_rl_trading.backtest import execution as execution_module
from quant_rl_trading.backtest import loop
from quant_rl_trading.collectors.market_hours import Market, trading_days
from quant_rl_trading.session import daily as daily_module

SEOUL = ZoneInfo("Asia/Seoul")

#: 국장 거래일만 고른다. 휴장일을 섞으면 "그날 봉이 없다" 와 구분이 안 된다.
START = date(2026, 8, 3)
END = date(2026, 8, 7)
ENTITIES = ["KR:000100", "KR:000200", "KR:000300"]


def _moment(day: date) -> datetime:
    return datetime.combine(day, loop.DEFAULT_SNAPSHOT_TIME, tzinfo=SEOUL)


@pytest.fixture
def warehouse(store):  # type: ignore[no-untyped-def]
    """3종목 · 400세션 · IC 가중치. 실제 Analyst 가 돌 만큼의 이력을 깐다."""
    store.seed_config_defaults()
    history = [START - timedelta(days=offset) for offset in range(400, -1, -1)]

    # 환율은 **매일** 있어야 한다. ledger.fx_rate 의 조회 창이 10일이라,
    # 한 행만 심어 두면 그 창을 벗어나는 순간 NAV 가 통째로 실패한다.
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
    for index, day in enumerate(history + trading_days(Market.KR, START, END)):
        moment = _moment(day)
        for offset, entity in enumerate(ENTITIES):
            universe_rows.append({
                "entity_id": entity, "valid_from": moment, "observed_at": moment,
                "source": "test", "market": "KR", "name": entity,
                "is_listed": True, "is_tradable": True, "delisted_on": None,
            })
            # 종목마다 다른 기울기 — 점수가 갈려야 후보 선정이 의미를 갖는다.
            close = 10_000.0 + index * (3 + offset) + offset * 500
            price_rows.append({
                "entity_id": entity, "valid_from": moment, "observed_at": moment,
                "source": "test", "market": "KR",
                "open": close, "high": close, "low": close, "close": close,
                "volume": 500_000.0, "value": close * 500_000.0, "adj_factor": None,
            })
    store.append("universe", universe_rows, ingest_run_id="u-seed")
    store.append("prices", price_rows, ingest_run_id="p-seed")

    # **신호 이력.** confidence 는 최근 60거래일 롤링 IC 라(analysts/ic.py),
    # 이력이 없으면 0 이고 0 이면 합성 점수가 통째로 비어 아무것도 사지 않는다.
    # 실전에서는 워밍업 구간(loop.run 의 warmup_days)이 이 자리를 채운다.
    past = trading_days(Market.KR, START - timedelta(days=140), START)
    store.append(
        "signals",
        [
            {
                "entity_id": entity, "valid_from": _moment(day),
                "observed_at": _moment(day), "source": "test", "analyst": "fundamental",
                "analyst_version": "fundamental-v0.1.0",
                # 오르는 종목에 높은 점수 — IC 가 양수로 나와야 confidence 가 선다.
                "score": 0.2 + 0.3 * offset, "confidence": 1.0, "horizon_days": 5,
                "features_hash": "x", "evidence_json": "[]", "latency_ms": 1.0,
            }
            for day in past
            if day < START
            for offset, entity in enumerate(ENTITIES)
        ],
        ingest_run_id="sig-seed",
    )

    # IC 측정 시점. 조회 창(400거래일)의 가장자리에 두면 하루 차이로 빠진다.
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


def test_하루씩_굴러가고_성적이_나온다(warehouse) -> None:
    result = loop.run(
        warehouse, start=START, end=END, market="KR", capital=100_000_000.0
    )

    sessions = trading_days(Market.KR, START, END)
    assert len(result.days) == len(sessions)
    assert result.performance is not None
    assert result.performance.days == len(sessions)
    # 낙폭은 0 이하다. 양수면 부호를 뒤집어 쓴 것이다.
    assert result.performance.max_drawdown <= 0.0
    # 자본이 들어왔으니 NAV 는 매일 양수여야 한다.
    assert all(day.nav > 0 for day in result.days)


def test_첫날에는_체결이_없다(warehouse) -> None:
    """결정과 체결이 같은 날이면 종가를 보고 그 종가에 산 것이 된다."""
    result = loop.run(
        warehouse, start=START, end=END, market="KR", capital=100_000_000.0
    )

    assert result.days[0].filled == 0
    assert result.days[0].requested == 0


def test_체결은_주문_다음_거래일에_적힌다(warehouse) -> None:
    result = loop.run(
        warehouse, start=START, end=END, market="KR", capital=100_000_000.0
    )
    if not any(day.filled for day in result.days):
        pytest.skip("이 표본에서는 체결이 없었다 — 순서 성질만 위 테스트가 본다")

    last = result.days[-1].as_of
    orders = warehouse.get("orders", as_of=last, lookback=30)
    trades = warehouse.get("trades", as_of=last, lookback=30)
    assert not trades.empty

    # trades.order_id 는 "{주문 세션}|{종목}|{방향}" 이다. 체결일은 그 세션의
    # 날짜보다 **뒤**여야 한다.
    for row in trades.to_dict(orient="records"):
        session_id = str(row["order_id"]).split("|")[0]
        ordered_on = date.fromisoformat(session_id.split("-", 1)[1])
        assert row["valid_from"].date() > ordered_on
    assert not orders.empty


def test_두_번_돌려도_같은_지문이다(warehouse) -> None:
    """결정론. 두 번째 실행은 창고에 아무것도 더 쓰지 않는다."""
    first = loop.run(
        warehouse, start=START, end=END, market="KR", capital=100_000_000.0
    )
    last = first.days[-1].as_of
    trades_before = len(warehouse.get("trades", as_of=last, lookback=30))

    second = loop.run(warehouse, start=START, end=END, market="KR", capital=0.0)

    assert second.digest() == first.digest()
    assert len(warehouse.get("trades", as_of=last, lookback=30)) == trades_before


def test_거래일이_없으면_조용히_0일이_아니라_이유를_남긴다(warehouse) -> None:
    result = loop.run(
        warehouse, start=date(2026, 1, 1), end=date(2026, 1, 1), market="KR"
    )
    assert result.days == []
    assert result.notes


def test_이미_적재된_세션을_다시_체결시키면_말을_한다(warehouse) -> None:
    """``ingest_run_id`` 가 막는 것은 옳다. **말없이 막는 것이 틀렸다.**

    막힌 자리에서 호출부는 방금 계산한 체결 수량을 보고하는데 회계는 창고의
    옛 행에서 장부를 접는다. 둘이 갈라져도 화면은 정상으로 보인다 — shadow
    2026-08-13 이 그렇게 20여 종목과 1종목 사이에서 갈라져 있었다.
    """
    first = loop.run(
        warehouse, start=START, end=END, market="KR", capital=100_000_000.0
    )
    if not any(day.filled for day in first.days):
        pytest.skip("이 표본에서는 체결이 없었다 — 막힐 적재 자체가 없다")

    second = loop.run(warehouse, start=START, end=END, market="KR", capital=0.0)

    notes = [note for day in second.days for note in day.notes]
    stale = [note for note in notes if "이미 적재돼 있다" in note]
    assert stale, f"재실행이 조용했다. 남은 말: {notes}"
    # 같은 입력을 다시 돌린 것이므로 창고와 재계산이 일치해야 한다.
    assert all("같다" in note for note in stale), stale


def test_창고가_낡았으면_같다고_하지_않는다(store) -> None:
    """재계산이 창고와 다르면 그 사실이 문구에 나와야 한다.

    실행기를 고친 뒤 옛 세션을 다시 돌리면 정확히 이 모양이 된다. "같다" 로
    뭉뚱그리면 고침이 반영됐는지 아닌지를 영영 알 수 없다.
    """
    moment = _moment(START)
    store.append(
        "trades",
        [{
            "entity_id": "KR:000890", "valid_from": moment, "observed_at": moment,
            "source": "backtest", "market": "KR", "side": "buy",
            "quantity": 1_004.0, "price": 1_484.12, "currency": "KRW",
            "fee": 223.5, "tax": 0.0, "order_id": "KR-2026-08-03|KR:000890|buy",
        }],
        ingest_run_id="backtest-trades-KR-2026-08-03",
    )

    note = execution_module._stale_note(
        store,
        as_of=moment,
        run_id="backtest-trades-KR-2026-08-03",
        rows=[
            {"quantity": 1_004.0},
            {"quantity": 492.0},
        ],
    )

    assert "낡았다" in note
    assert "1,004주" in note and "1,496주" in note


def test_워밍업_날에는_브로커를_주지_않는다(warehouse) -> None:
    """**틀리면 실제 돈이 나간다.**

    ``run_session.py`` 는 D+1 체결 단계를 돌리려고 항상 전날을 워밍업으로 같이
    굴린다. 그 전날의 주문은 이미 지나간 결정이라, 실제로 내보내면 어제 가격으로
    오늘 주문을 내는 것이 된다. 브로커가 워밍업 날에 닿으면 실전 세션마다 전날
    주문이 한 벌씩 더 나간다.
    """
    from quant_rl_trading.broker import PaperBroker

    class RecordingBroker(PaperBroker):
        """``PaperBroker`` 그대로 — 아무것도 안 보낸다. 닿았는지만 센다."""

        def __init__(self) -> None:
            super().__init__()
            self.submitted = 0

        def submit(self, order, *, as_of):  # type: ignore[no-untyped-def]
            self.submitted += 1
            return super().submit(order, as_of=as_of)

    broker = RecordingBroker()
    seen: list[tuple[date, object]] = []
    real_run = daily_module.run

    def spy(store, clock, **kwargs):  # type: ignore[no-untyped-def]
        seen.append((kwargs["as_of"].date(), kwargs.get("broker")))
        return real_run(store, clock, **kwargs)

    with mock.patch.object(daily_module, "run", spy):
        loop.run(
            warehouse, start=END, end=END, market="KR",
            capital=100_000_000.0, warmup_days=1, broker=broker,
        )

    assert len(seen) >= 2, f"워밍업이 안 돌았다: {seen}"
    # 마지막 날(보고 대상)만 브로커를 받는다.
    assert seen[-1][1] is broker
    # 그 앞은 전부 워밍업이다 — 하나도 브로커를 받으면 안 된다.
    assert all(item is None for _, item in seen[:-1]), seen
