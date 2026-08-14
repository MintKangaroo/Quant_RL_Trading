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
from zoneinfo import ZoneInfo

import pytest

from quant_rl_trading.backtest import loop
from quant_rl_trading.collectors.market_hours import Market, trading_days

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
    opening = _moment(history[0])

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
                "observed_at": _moment(day), "source": "test", "analyst": "risk",
                "analyst_version": "risk-v0.1.0",
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
            "entity_id": "risk", "valid_from": measured, "observed_at": measured,
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
