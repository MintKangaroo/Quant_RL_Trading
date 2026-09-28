"""E1 집행 계획 읽기 계약 (``executor/plans.py`` · execution-safety.md "E1 집행 계획 계약").

고정하는 것:

1. **기본(plan_source=rule)은 지금 실전 동작 그대로** — 계획 행이 창고에 있어도 무시하고, 표를 읽지도 않는다.
2. 계획이 없거나 12시간 넘게 낡았거나 모르는 팔이면 규칙으로 물러선다.
3. 계획이 있으면 조각 수·간격만 바뀐다 — 지정가 여유는 언제나 설정값.
4. 시장가(청산·킬스위치)는 계획을 타지 않는다.
5. 집행기 소스에 모델·LLM import 가 없다(불변식 6).
"""

from __future__ import annotations

import ast
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest
from tests.account_fixture import fund_account

from quant_rl_trading.executor import Target, pipeline, plans
from quant_rl_trading.executor.orders import PlannedOrder, SliceParams, due_slices
from quant_rl_trading.schemas.order import Order, Side

NOW = datetime(2026, 8, 12, 1, 0, tzinfo=UTC)   # 한국시간 10:00
BASE = SliceParams(slice_count=4, slice_interval_sec=3600, max_slippage=0.005)


def _config(store, name: str, value: object) -> None:  # type: ignore[no-untyped-def]
    store.append(
        "config",
        [{"entity_id": name, "valid_from": NOW - timedelta(days=30), "observed_at": NOW - timedelta(days=30),
          "source": "test", "value_json": json.dumps(value)}],
        ingest_run_id=f"cfg-{name}-{value}",
    )


def _plan(store, entity: str, arm: str, *, at: datetime = NOW, source: str = "e1-bandit") -> None:  # type: ignore[no-untyped-def]
    store.append(
        plans.PLANS,
        [{"entity_id": entity, "valid_from": at, "observed_at": at, "source": source, "market": "KR",
          "arm": arm, "propensity": 0.25, "detail": "{}"}],
        ingest_run_id=f"plan-{entity}-{arm}-{at.isoformat()}-{source}",
    )


@pytest.fixture
def seeded(store):  # type: ignore[no-untyped-def]
    store.seed_config_defaults()
    fund_account(store, NOW)
    rows = []
    for entity in ("KR:A", "KR:B"):
        for offset in range(5):
            day = NOW - timedelta(days=offset)
            rows.append({
                "entity_id": entity, "valid_from": day, "observed_at": day, "source": "test", "market": "KR",
                "open": 1_000.0, "high": 1_000.0, "low": 1_000.0, "close": 1_000.0,
                "volume": 1e6, "value": 1e9, "adj_factor": None,
            })
    store.append("prices", rows, ingest_run_id="p-seed")
    return store


def _targets() -> list[Target]:
    return [Target("KR:A", weight=0.10, price=1_000.0, adv_value=1e9),
            Target("KR:B", weight=0.10, price=1_000.0, adv_value=1e9)]


def _run(store):  # type: ignore[no-untyped-def]
    from quant_rl_trading.replay.clock import ReplayClock

    return pipeline.run(store, ReplayClock(NOW), as_of=NOW, market="KR", targets=_targets(),
                        holdings={}, equity=10_000_000.0, record=False)


def _shape(result) -> list[tuple[str, int, float, float | None]]:  # type: ignore[no-untyped-def]
    return [(p.order.entity_id, p.slice_seq, float(p.order.quantity), p.order.limit_price) for p in result.planned]


# -- 1. 기본은 규칙 — 실전 동작 불변 ----------------------------------------------------


def test_기본_설정은_rule_이고_C0_팔은_설정_기본값과_같다(seeded) -> None:
    assert plans.plan_source(seeded, as_of=NOW) == plans.RULE
    base = SliceParams.from_store(seeded, as_of=NOW)
    assert plans.ARMS["4x60"] == (base.slice_count, base.slice_interval_sec), "C0 가 지금 규칙이 아니게 된다"


def test_rule_이면_계획이_있어도_무시하고_표를_읽지도_않는다(seeded, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    before = _shape(_run(seeded))
    _plan(seeded, "KR:A", "1x0")
    _plan(seeded, "KR:B", "8x30")

    real_get = type(seeded).get
    touched: list[str] = []

    def spy(self, table, **kwargs):  # type: ignore[no-untyped-def]
        touched.append(table)
        return real_get(self, table, **kwargs)

    monkeypatch.setattr(type(seeded), "get", spy)
    after = _run(seeded)
    assert _shape(after) == before, "기본값에서 주문 모양이 바뀌면 실전 동작이 바뀐 것이다"
    assert plans.PLANS not in touched
    assert not any("집행 계획" in note for note in after.notes)


def test_설정이_없어도_rule_로_읽는다() -> None:
    from quant_rl_trading.store.errors import ConfigNotFound

    class _NoConfig:
        def config(self, name: str, *, as_of: datetime) -> object:
            raise ConfigNotFound(name)

    assert plans.plan_source(_NoConfig(), as_of=NOW) == plans.RULE  # type: ignore[arg-type]


# -- 2·3. 켰을 때: 계획 반영 · 없음/낡음/모름은 규칙 ----------------------------------------


def test_계획이_있으면_조각_수만_바뀌고_지정가는_그대로다(seeded) -> None:
    baseline = {(e, s): (q, lp) for e, s, q, lp in _shape(_run(seeded))}
    _config(seeded, "execution.plan_source", "e1-bandit")
    _plan(seeded, "KR:A", "2x30")
    result = _run(seeded)

    a = [p for p in result.planned if p.order.entity_id == "KR:A"]
    b = [p for p in result.planned if p.order.entity_id == "KR:B"]
    assert len(a) == 2 and sum(p.order.quantity for p in a) == sum(q for (e, _), (q, _) in baseline.items() if e == "KR:A")
    assert {p.order.limit_price for p in a} == {lp for (e, _), (_, lp) in baseline.items() if e == "KR:A"}
    assert [(p.slice_seq, p.order.quantity) for p in b] == [(s, q) for (e, s), (q, _) in baseline.items() if e == "KR:B"], \
        "계획 없는 종목은 규칙 그대로"
    assert any("집행 계획 1종목" in note for note in result.notes)


def test_계획이_없거나_낡았거나_다른_부품_것이면_규칙이다(seeded) -> None:
    baseline = _shape(_run(seeded))
    _config(seeded, "execution.plan_source", "e1-bandit")
    assert _shape(_run(seeded)) == baseline, "계획 없음 → 규칙"

    _plan(seeded, "KR:A", "1x0", at=NOW - timedelta(hours=13))
    _plan(seeded, "KR:B", "1x0", source="other")
    assert plans.read_arms(seeded, as_of=NOW, market="KR") == {}
    assert _shape(_run(seeded)) == baseline, "낡은 계획·다른 부품 계획 → 규칙"


def test_모르는_팔_이름은_규칙이다() -> None:
    frame = pd.DataFrame([{"entity_id": "KR:A", "valid_from": NOW, "observed_at": NOW, "source": "e1-bandit", "arm": "16x5"}])
    assert plans.fresh_arms(frame, as_of=NOW, source="e1-bandit") == {}
    assert plans.params_for(BASE, "16x5") == BASE
    assert plans.params_for(BASE, None) == BASE


def test_팔은_조각_수와_간격만_바꾼다() -> None:
    for arm, (count, interval) in plans.ARMS.items():
        got = plans.params_for(BASE, arm)
        assert (got.slice_count, got.slice_interval_sec) == (count, interval)
        assert got.max_slippage == BASE.max_slippage, f"{arm}: 지정가 여유는 고정"


# -- 4. 조각 시각 · 시장가 ---------------------------------------------------------------


def _slices(entity: str, count: int, *, limit: float | None = 1_000.0) -> list[PlannedOrder]:
    return [PlannedOrder(order=Order(entity_id=entity, side=Side.BUY, quantity=10, limit_price=limit),
                         order_id=f"{entity}{seq}", session_id="KR-2026-08-11", slice_seq=seq, target_weight=0.1)
            for seq in range(count)]


def test_계획이_없으면_조각_시각은_orders_due_slices_와_같다() -> None:
    planned = _slices("KR:A", 4) + _slices("KR:B", 4)
    for elapsed in (0.0, 1800.0, 3600.0, 99999.0):
        assert plans.due_slices(planned, base=BASE, arms={}, elapsed_sec=elapsed) == \
            due_slices(planned, params=BASE, elapsed_sec=elapsed)


def test_조각_시각은_종목마다_그_팔의_간격이다() -> None:
    planned = _slices("KR:A", 2) + _slices("KR:B", 4) + _slices("KR:C", 1)
    arms = {"KR:A": "2x30", "KR:C": "1x0"}
    due = plans.due_slices(planned, base=BASE, arms=arms, elapsed_sec=1800.0)
    assert [(p.order.entity_id, p.slice_seq) for p in due] == [("KR:A", 0), ("KR:A", 1), ("KR:B", 0), ("KR:C", 0)]


def test_시장가는_계획을_타지_않는다() -> None:
    assert plans.order_params(BASE, {"KR:A": "1x0"}, entity_id="KR:A", market_order=True) == BASE
    market = _slices("KR:A", 4, limit=None)
    due = plans.due_slices(market, base=BASE, arms={"KR:A": "1x0"}, elapsed_sec=0.0)
    assert [p.slice_seq for p in due] == [0], "시장가 조각은 규칙 간격(3600)으로 나간다"


# -- 5. 집행기에 AI 없음 ------------------------------------------------------------------

FORBIDDEN = ("torch", "sklearn", "lightgbm", "xgboost", "hmmlearn", "stable_baselines3", "gymnasium",
             "anthropic", "openai", "transformers", "quant_rl_trading.rl", "quant_rl_trading.allocator",
             "quant_rl_trading.analysts", "quant_rl_trading.modelops", "tools")


def test_집행기_소스에_모델_import_가_없다() -> None:
    """불변식 6. 계획은 창고에서 **문자열 팔 이름**으로만 들어온다 — 모델은 집행기 밖(세션 전 부품)에 있다."""
    root = Path(__file__).resolve().parents[2] / "quant_rl_trading" / "executor"
    offenders = []
    for path in sorted(root.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            offenders += [f"{path.name}: {n}" for n in names if any(n == f or n.startswith(f + ".") for f in FORBIDDEN)]
    assert offenders == []


# -- 보상 계산기(기록용) ------------------------------------------------------------------


def test_구현_손실_합성_예제() -> None:
    """매수 100주 = 4조각. 집행일 시가 1,000 · 종가 1,020. 두 조각 50주를 1,005 에 체결, 나머지 50주 미체결(만료).
    체결 몫 = 50×(1005−1000) = 250, 기회비용 = 50×(1020−1000) = 1,000, 분모 = 100×1000 → 125bps(손해)."""
    from tools.measure_execution import order_shortfall, required_orders_per_arm

    session = "KR-2026-09-01"
    orders = pd.DataFrame([
        {"session_id": session, "entity_id": "KR:A", "slice_seq": seq, "side": "buy", "quantity": 25.0,
         "limit_price": 1_005.0, "status": status, "revision": rev}
        for seq, final in enumerate(["filled", "filled", "expired", "expired"])
        for rev, status in ((1, "planned"), (2, final))
    ] + [  # 증권사에 안 간 종목, 상태 모름 종목
        {"session_id": session, "entity_id": "KR:B", "slice_seq": 0, "side": "buy", "quantity": 10.0,
         "limit_price": 500.0, "status": "risk_blocked", "revision": 1},
        {"session_id": session, "entity_id": "KR:C", "slice_seq": 0, "side": "sell", "quantity": 10.0,
         "limit_price": 500.0, "status": "cancel_unknown", "revision": 1},
    ])
    fills = pd.DataFrame([
        {"order_id": f"{session}|KR:A|0", "quantity": 25.0, "price": 1_005.0},
        {"order_id": f"{session}|KR:A|1#25", "quantity": 25.0, "price": 1_005.0},
    ])
    daily = pd.DataFrame([
        {"entity_id": "KR:A", "day": "2026-09-01", "open": 990.0, "close": 990.0},
        {"entity_id": "KR:A", "day": "2026-09-02", "open": 1_000.0, "close": 1_020.0},
    ])
    table = order_shortfall(orders, fills, daily).set_index("entity_id")

    a = table.loc["KR:A"]
    assert bool(a["measured"]) and str(a["exec_day"]) == "2026-09-02"
    assert a["q_plan"] == 100 and a["q_filled"] == 50
    assert a["exec_bps"] == pytest.approx(25.0) and a["opp_bps"] == pytest.approx(100.0)
    assert a["is_bps"] == pytest.approx(125.0)
    # 전 세션 종가(990) 대비: 50×15 + 50×30 = 2,250 / 99,000
    assert a["vs_prev_close_bps"] == pytest.approx(2_250 / 99_000 * 1e4)
    assert not bool(table.loc["KR:B", "measured"]) and table.loc["KR:B", "why"] == "증권사에 안 감"
    assert not bool(table.loc["KR:C", "measured"]) and "모름" in table.loc["KR:C", "why"]

    # 스냅샷 정정으로만 들어간 체결이 있는 자리는 모름이다
    from datetime import date

    masked = order_shortfall(orders, fills, daily, unknown_fills={(date(2026, 9, 2), "KR:A")}).set_index("entity_id")
    assert not bool(masked.loc["KR:A", "measured"])

    # 매도는 부호가 뒤집힌다 — 싸게 팔면 손해(+)
    sell = orders[orders["entity_id"] == "KR:A"].assign(side="sell")
    assert order_shortfall(sell, fills, daily).iloc[0]["exec_bps"] == pytest.approx(-25.0)

    assert required_orders_per_arm(100.0, 20.0) > required_orders_per_arm(100.0, 40.0)
