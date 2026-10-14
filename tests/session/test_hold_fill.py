"""보유일 잔여 채움 (selector.md §5 7번 5항, 2026-10-04) — 진짜 창고 위에서.

증명하는 것:

(a) 보유일·미체결 잔여 있음 → **그 수량만** 주문한다(다른 보유는 안 건드린다)
(b) 잔여 없음 → 주문 0 (옛 동작 그대로)
(c) 킬스위치가 걸려 있으면 잔여 매수를 안 낸다
(d) 재조정일엔 잔여 채움이 아니라 새 목표 / 직전 재조정 기록이 없으면 옛 목표로 물러서지 않는다
(e) 백테스트(체결이 다 되는 경로)는 잔여 채움이 있어도 결과가 같다 — 분기 없이(불변식 5)
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from quant_rl_trading.backtest import loop
from quant_rl_trading.collectors.market_hours import Market, trading_days
from quant_rl_trading.executor import guards
from quant_rl_trading.replay.clock import ReplayClock
from quant_rl_trading.schemas.order import Side
from quant_rl_trading.selector import cadence, hold_fill
from quant_rl_trading.session import daily
from quant_rl_trading.store import Store

NOW = datetime(2026, 8, 12, 7, 0, tzinfo=UTC)          # 한국시간 16:00
SESSIONS = [NOW - timedelta(days=offset) for offset in range(400, -1, -1)]
ENTITIES = ["KR:000100", "KR:000200", "KR:000300"]
REBALANCE = NOW - timedelta(days=3)
SESSION = f"KR-{REBALANCE.date().isoformat()}"
EQUITY = 100_000_000.0


def _close(index: int, offset: int) -> float:
    return 10_000.0 + index * 5 + offset * 500


def _seed(store: Store) -> Store:
    store.seed_config_defaults()
    store.append("fx", [{"entity_id": "FX:USDKRW", "valid_from": NOW, "observed_at": NOW, "source": "test",
                         "rate": 1_350.0}], ingest_run_id="fx-seed")
    store.append("capital_flows", [{"entity_id": "FUND", "valid_from": SESSIONS[0], "observed_at": SESSIONS[0],
                                    "source": "test", "currency": "KRW", "amount": EQUITY, "kind": "deposit"}],
                 ingest_run_id="flow-seed")
    universe, prices = [], []
    for index, day in enumerate(SESSIONS):
        for offset, entity in enumerate(ENTITIES):
            universe.append({"entity_id": entity, "valid_from": day, "observed_at": day, "source": "test",
                             "market": "KR", "name": entity, "is_listed": True, "is_tradable": True,
                             "delisted_on": None})
            close = _close(index, offset)
            prices.append({"entity_id": entity, "valid_from": day, "observed_at": day, "source": "test",
                           "market": "KR", "open": close, "high": close, "low": close, "close": close,
                           "volume": 100_000.0, "value": 5_000_000_000.0, "adj_factor": None})
    store.append("universe", universe, ingest_run_id="u-seed")
    store.append("prices", prices, ingest_run_id="p-seed")
    store.append("analyst_weights", [{"entity_id": "fundamental", "valid_from": NOW, "observed_at": NOW,
                                      "source": "test", "market": "KR", "ic": 0.077, "weight": 1.0}],
                 ingest_run_id="w-seed")
    store.append("signals", [{
        "entity_id": entity, "valid_from": NOW, "observed_at": NOW, "source": "test", "analyst": "fundamental",
        "analyst_version": "fundamental-v0.1.0", "score": score, "confidence": 1.0, "horizon_days": 5,
        "features_hash": "x", "evidence_json": "[]", "latency_ms": 1.0,
    } for entity, score in zip(ENTITIES, [0.9, 0.5, 0.2], strict=True)], ingest_run_id="s-seed")
    return store


def _record_rebalance(store: Store, weights: dict[str, float], *, scale: float = 1.0) -> None:
    """직전 재조정 세션이 남기는 기록 둘 — 목표(realized_weights)와 그 세션의 자본·배수(events)."""
    store.append("realized_weights", [{
        "entity_id": entity, "valid_from": REBALANCE, "observed_at": REBALANCE, "source": "executor_holdings_v2",
        "market": "KR", "session_id": SESSION, "target_weight": weight, "realized_weight": 0.0,
    } for entity, weight in weights.items()], ingest_run_id=f"realized-{SESSION}")
    store.append("events", [
        {"entity_id": f"session-{SESSION}", "valid_from": REBALANCE, "observed_at": REBALANCE, "source": "test",
         "seq": seq, "stage": stage, "actor": actor, "payload_hash": "x", "payload": json.dumps(payload)}
        for seq, stage, actor, payload in (
            (0, "observe", "accounting", {"nav": EQUITY, "available_cash": EQUITY, "currency": "KRW"}),
            (3, "exposure", "full", {"driver": "full", "notes": [], "scale": scale}),
        )
    ], ingest_run_id=f"events-{SESSION}")


def _target_shares(weight: float, offset: int) -> int:
    """재조정 사이징이 냈을 주식 수를 오늘 가격으로 — 테스트가 기대값을 따로 계산한다."""
    then, now = _close(len(SESSIONS) - 4, offset), _close(len(SESSIONS) - 1, offset)
    return int(weight * EQUITY * (now / then) / now + 1e-9)


@pytest.fixture
def fund(store, monkeypatch):  # type: ignore[no-untyped-def]
    _seed(store)
    monkeypatch.setattr(cadence, "for_session",
                        lambda store, *, as_of, market: cadence.Cadence(rebalance=False, every=10, index=3))
    monkeypatch.setattr(hold_fill, "last_rebalance_day", lambda store, *, as_of, market: REBALANCE.date())
    return store


def _orders(result: daily.DailySession) -> dict[tuple[str, Side], int]:
    out: dict[tuple[str, Side], int] = {}
    for item in result.orders:
        key = (item.order.entity_id, item.order.side)
        out[key] = out.get(key, 0) + item.order.quantity
    return out


# ------------------------------------------------------------------ 순수 함수


def test_잔여는_주식_수로_재고_하한_밑은_둔다() -> None:
    target = hold_fill.RebalanceTarget(session_id="KR-x", as_of=NOW, equity=1e8, scale=1.0,
                                       weights={"A": 0.10, "B": 0.05, "C": 0.0, "D": 0.02})
    shares = hold_fill.residual_shares(
        target,
        holdings={"A": 500, "B": 499, "C": 300, "D": 100, "E": 50},
        growth={"A": 1.0, "B": 1.0, "C": 1.0, "E": 1.0},          # D 는 가격 비율 모름
        prices={"A": 10_000.0, "B": 10_000.0, "C": 10_000.0, "D": 10_000.0, "E": 10_000.0},
        equity=1e8, min_weight=0.002,
    )
    assert shares == {"A": 1_000, "C": 0}   # B 는 1주(0.01%) 차라 둔다 · D 는 가격 모름 · E 는 재조정 기록에 없음


def test_가격이_올라도_목표_주식_수는_그대로다() -> None:
    """비중으로 다시 맞추면 표류를 매일 되돌린다 — 주식 수로 고정하면 오른 종목을 팔지 않는다."""
    target = hold_fill.RebalanceTarget(session_id="KR-x", as_of=NOW, equity=1e8, scale=1.0, weights={"A": 0.10})
    assert hold_fill.residual_shares(target, holdings={"A": 1_000}, growth={"A": 1.5}, prices={"A": 15_000.0},
                                     equity=1.2e8, min_weight=0.002) == {}


def test_지금_배수로_목표_주식_수를_고친다() -> None:
    """2026-10-12 결함 — 재조정은 배수 1.0, 보유일에 0.7 로 내렸는데 축소 매도가 취소됐다. 재조정 배수 기준으로 비교하면
    보유 = 목표라 다시 안 냈다. 지금 배수 기준이면 0.7 배 수량까지 판다."""
    target = hold_fill.RebalanceTarget(session_id="KR-x", as_of=NOW, equity=1e8, scale=1.0, weights={"A": 0.10})
    held = {"A": 1_000}
    kwargs = dict(holdings=held, growth={"A": 1.0}, prices={"A": 10_000.0}, equity=1e8, min_weight=0.002)
    assert hold_fill.residual_shares(target, **kwargs) == {}                    # 옛 동작(scale 없음)
    assert hold_fill.residual_shares(target, scale=0.7, **kwargs) == {"A": 700}
    assert hold_fill.residual_shares(target, scale=1.0, **kwargs) == {}
    # 재조정이 이미 0.7 이었다면 target_weight 에 0.7 이 들어 있다 — 지금도 0.7 이면 그대로
    shrunk = hold_fill.RebalanceTarget(session_id="KR-x", as_of=NOW, equity=1e8, scale=0.7, weights={"A": 0.07})
    assert hold_fill.residual_shares(shrunk, scale=0.7, holdings={"A": 700}, growth={"A": 1.0},
                                     prices={"A": 10_000.0}, equity=1e8, min_weight=0.002) == {}
    # 배수가 오른 쪽도 대칭 — 못 산 늘림을 마저 산다
    assert hold_fill.residual_shares(shrunk, scale=1.0, holdings={"A": 700}, growth={"A": 1.0},
                                     prices={"A": 10_000.0}, equity=1e8, min_weight=0.002) == {"A": 1_000}


# ------------------------------------------------------------------ 세션 경로


def test_a_보유일_미체결_잔여만_주문한다(fund) -> None:
    weights = {ENTITIES[0]: 0.10, ENTITIES[1]: 0.05}
    _record_rebalance(fund, weights)
    want_0, want_1 = _target_shares(0.10, 0), _target_shares(0.05, 1)
    holdings = {ENTITIES[0]: want_0 - 300, ENTITIES[1]: want_1}   # 0번은 300주 덜 샀다 · 1번은 다 샀다

    result = daily.run(fund, ReplayClock(NOW), as_of=NOW, market="KR", holdings=holdings)

    assert _orders(result) == {(ENTITIES[0], Side.BUY): 300}
    assert any("보유일 잔여 채움" in note for note in result.notes)


def test_a_매도_잔여도_목표_주식_수까지_판다(fund) -> None:
    _record_rebalance(fund, {ENTITIES[0]: 0.10, ENTITIES[2]: 0.0})
    holdings = {ENTITIES[0]: _target_shares(0.10, 0), ENTITIES[2]: 400}   # 2번은 팔기로 했는데 못 팔았다

    result = daily.run(fund, ReplayClock(NOW), as_of=NOW, market="KR", holdings=holdings)

    assert _orders(result) == {(ENTITIES[2], Side.SELL): 400}


def test_b_잔여가_없으면_주문_0(fund) -> None:
    weights = {ENTITIES[0]: 0.10, ENTITIES[1]: 0.05}
    _record_rebalance(fund, weights)
    holdings = {ENTITIES[0]: _target_shares(0.10, 0), ENTITIES[1]: _target_shares(0.05, 1)}

    result = daily.run(fund, ReplayClock(NOW), as_of=NOW, market="KR", holdings=holdings)

    assert not result.orders
    assert not any("보유일 잔여 채움" in note for note in result.notes)


def test_c_킬스위치면_잔여_매수를_안_낸다(fund) -> None:
    _record_rebalance(fund, {ENTITIES[0]: 0.10})
    guards.engage(fund, as_of=NOW - timedelta(days=1), observed_at=NOW - timedelta(days=1), reason="test", by="test")
    holdings = {ENTITIES[0]: _target_shares(0.10, 0) - 300}

    result = daily.run(fund, ReplayClock(NOW), as_of=NOW, market="KR", holdings=holdings)

    assert not any(item.order.side is Side.BUY for item in result.orders)
    assert any("킬스위치" in note for note in result.notes)


def test_d_재조정일엔_새_목표로_고른다(fund, monkeypatch) -> None:
    _record_rebalance(fund, {ENTITIES[0]: 0.10})
    monkeypatch.setattr(cadence, "for_session",
                        lambda store, *, as_of, market: cadence.Cadence(rebalance=True, every=10, index=10))
    called: list[bool] = []
    monkeypatch.setattr(hold_fill, "plan", lambda *a, **k: called.append(True) or hold_fill.HoldFill())

    result = daily.run(fund, ReplayClock(NOW), as_of=NOW, market="KR", holdings={ENTITIES[0]: 10})

    assert not called, "재조정일에 잔여 채움이 돌았다"
    assert result.candidates and result.weights


def test_d_직전_재조정_기록이_없으면_채우지_않는다(fund) -> None:
    """정지로 재조정이 안 돌았으면 더 옛 목표로 물러서지 않는다 — 주문 0, 사유는 남긴다."""
    result = daily.run(fund, ReplayClock(NOW), as_of=NOW, market="KR", holdings={ENTITIES[0]: 10})

    assert not result.orders
    assert any("기록이 없다" in note for note in result.notes)


# ------------------------------------------------------------------ (e) 백테스트 불변

SEOUL = ZoneInfo("Asia/Seoul")
START, END = date(2026, 8, 3), date(2026, 8, 7)


def _backtest_store(root) -> Store:  # type: ignore[no-untyped-def]
    store = Store(root=root)
    store.seed_config_defaults()
    moment = lambda day: datetime.combine(day, loop.DEFAULT_SNAPSHOT_TIME, tzinfo=SEOUL)  # noqa: E731
    history = [START - timedelta(days=offset) for offset in range(400, -1, -1)]
    store.append("fx", [{"entity_id": "FX:USDKRW", "valid_from": moment(day), "observed_at": moment(day),
                         "source": "test", "rate": 1_350.0}
                        for day in [START - timedelta(days=o) for o in range(400, -10, -1)]], ingest_run_id="fx")
    universe, prices = [], []
    for index, day in enumerate(history + trading_days(Market.KR, START, END)):
        for offset, entity in enumerate(ENTITIES):
            universe.append({"entity_id": entity, "valid_from": moment(day), "observed_at": moment(day),
                             "source": "test", "market": "KR", "name": entity, "is_listed": True,
                             "is_tradable": True, "delisted_on": None})
            close = 10_000.0 + index * (3 + offset) + offset * 500
            prices.append({"entity_id": entity, "valid_from": moment(day), "observed_at": moment(day),
                           "source": "test", "market": "KR", "open": close, "high": close, "low": close,
                           "close": close, "volume": 500_000.0, "value": close * 500_000.0, "adj_factor": None})
    store.append("universe", universe, ingest_run_id="u")
    store.append("prices", prices, ingest_run_id="p")
    store.append("signals", [{
        "entity_id": entity, "valid_from": moment(day), "observed_at": moment(day), "source": "test",
        "analyst": "fundamental", "analyst_version": "fundamental-v0.1.0", "score": 0.2 + 0.3 * offset,
        "confidence": 1.0, "horizon_days": 5, "features_hash": "x", "evidence_json": "[]", "latency_ms": 1.0,
    } for day in trading_days(Market.KR, START - timedelta(days=140), START) if day < START
        for offset, entity in enumerate(ENTITIES)], ingest_run_id="sig")
    measured = moment(START - timedelta(days=30))
    store.append("analyst_weights", [{"entity_id": "fundamental", "valid_from": measured, "observed_at": measured,
                                      "source": "test", "market": "KR", "ic": 0.077, "weight": 1.0}],
                 ingest_run_id="w")
    return store


def test_e_백테스트는_잔여_채움이_있어도_결과가_같다(tmp_path, monkeypatch) -> None:
    """2세션 주기(8/3 재조정 · 8/4 보유 · 8/5 재조정 …). 백테스트는 재조정 주문이 다 체결되므로 보유일 잔여가 0 — 잔여 채움을
    끈 실행과 지문·거래가 같아야 한다. 같은 코드에 분기 없이(불변식 5).

    이 하네스는 3종목이라 score 배분이 목표를 상한 15% 에 붙인다. 예전엔 그 마지막 조각이 위험 한도(지정가 평가)에 막혀
    백테스트에서도 잔여가 생겨, 상한을 0.95 로 올려 우회했다. 사이징이 위험 한도 잣대로 자르게 된 뒤(agents.md §7, 2026-10-04)
    우회 없이 차단 0 이다 — 아래가 그것도 확인한다."""
    monkeypatch.setattr(cadence, "settings", lambda store, *, as_of, market: (2, START))
    calls: list[int] = []
    original = hold_fill.plan

    def counting(*args, **kwargs):  # type: ignore[no-untyped-def]
        out = original(*args, **kwargs)
        calls.append(len(out.shares))
        return out

    monkeypatch.setattr(hold_fill, "plan", counting)
    on_store = _backtest_store(tmp_path / "on")
    on = loop.run(on_store, start=START, end=END, market="KR", capital=EQUITY)
    assert calls, "보유일 경로를 한 번도 안 탔다 — 시험이 아무것도 증명하지 않는다"
    assert sum(calls) == 0, "백테스트 보유일에 잔여가 생겼다"

    monkeypatch.setattr(hold_fill, "plan", lambda *a, **k: hold_fill.HoldFill())
    off = loop.run(_backtest_store(tmp_path / "off"), start=START, end=END, market="KR", capital=EQUITY)

    assert on.digest() == off.digest()
    assert [day.nav for day in on.days] == [day.nav for day in off.days]
    assert sum(day.filled for day in on.days) > 0, "체결이 하나도 없으면 비교가 비어 있다"
    orders = on_store.get("orders", as_of=datetime.combine(END, loop.DEFAULT_SNAPSHOT_TIME, tzinfo=SEOUL), lookback=30)
    assert not orders["reason"].astype(str).str.contains("maximum position size").any(), "상한에 붙은 마지막 조각이 막혔다"


def _record_held_scale(store: Store, scale: float) -> None:
    """어제 보유일 세션이 적은 노출 배수 — ``exposure.held_scale`` 이 읽는다."""
    day = NOW - timedelta(days=1)
    session = f"KR-{day.date().isoformat()}"
    store.append("events", [{
        "entity_id": f"session-{session}", "valid_from": day, "observed_at": day, "source": "test", "seq": 3,
        "stage": "exposure", "actor": "regime", "payload_hash": "x",
        "payload": json.dumps({"driver": "regime", "notes": [], "scale": scale}),
    }], ingest_run_id=f"events-{session}")


def test_a_배수가_내려간_뒤_취소된_축소를_다시_낸다(fund, monkeypatch) -> None:
    """2026-10-12 모의계좌: 재조정(배수 1.0) 뒤 보유일에 배수 0.7 — 축소 매도가 취소돼 보유가 1.0 배 수량 그대로. 오늘도 0.7 이라
    '배수 불변' 경로(잔여 종목만 넘김)인데, 잔여를 1.0 배 수량과 비교하면 주문 0 이었다. 지금 배수 기준이면 0.7 배까지 판다."""
    from quant_rl_trading.selector import exposure

    weights = {ENTITIES[0]: 0.10, ENTITIES[1]: 0.05}
    _record_rebalance(fund, weights, scale=1.0)
    _record_held_scale(fund, 0.7)
    monkeypatch.setattr(exposure, "decide", lambda *a, **k: exposure.ExposureDecision(scale=0.7, driver="regime"))
    full_0, full_1 = _target_shares(0.10, 0), _target_shares(0.05, 1)
    want_0, want_1 = _target_shares(0.07, 0), _target_shares(0.035, 1)
    holdings = {ENTITIES[0]: full_0, ENTITIES[1]: want_1}   # 0번 축소 매도는 취소됐다 · 1번은 줄였다

    result = daily.run(fund, ReplayClock(NOW), as_of=NOW, market="KR", holdings=holdings)

    orders = _orders(result)
    assert set(orders) == {(ENTITIES[0], Side.SELL)}, orders
    assert abs(orders[(ENTITIES[0], Side.SELL)] - (full_0 - want_0)) <= 1
