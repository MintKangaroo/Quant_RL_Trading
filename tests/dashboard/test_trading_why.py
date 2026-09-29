"""AI 결정 패널 = "이 종목은 왜 샀나 / 왜 안 샀나" (2026-09-29).

진짜 창고 위에서 세션 기록(`events`)과 세션이 쓴 선정 함수(`selector.pipeline.screen`)를 읽는지 본다.
화면이 규칙을 따로 재현하면 이 테스트가 아니라 실제 장부와 어긋난다 — 그래서 명단·배수는 기록에서,
필터·순위는 같은 함수에서 나와야 한다.

종목 일곱(점수 순 G·A·B·C·D·E·F):
- G 거래대금이 하한 밑 → 관문 탈락
- C 위험 점수 최하위 → 위험 하위 20% 탈락
- 설정: 상위 N=2, 완충 M=3. 위험 필터 뒤 순위 A1·B2·D3·E4·F5
- 보유 D(3위, 완충 안 → 유지) · F(5위, 완충 밖 → 매도). 세션 명단 = [D, A] → B 는 순위 2위인데 명단 밖
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from quant_rl_trading.dashboard import create_app
from quant_rl_trading.dashboard.services import why as why_module
from quant_rl_trading.replay.clock import ReplayClock

# 세션 = 9/28 장 마감 뒤(16:00 KST), 화면 = 9/29 15:40 KST. 주문은 9/29 아침에 움직였다.
SESSION = datetime(2026, 9, 28, 7, 0, tzinfo=UTC)
NOW = datetime(2026, 9, 29, 6, 40, tzinfo=UTC)
DAYS = [SESSION - timedelta(days=offset) for offset in range(400, 0, -1)]
A, B, C, D, E, F, G = (f"KR:00{n}000" for n in range(1, 8))
FUNDAMENTAL = {G: 0.95, A: 0.9, B: 0.8, C: 0.7, D: 0.6, E: 0.5, F: 0.4}
RISK = {A: 0.5, B: 0.4, C: -0.9, D: 0.3, E: 0.2, F: 0.1, G: 0.3}
RUN = "session-KR-2026-09-28"


def _row(entity: str, moment: datetime, **extra: Any) -> dict[str, Any]:
    return {"entity_id": entity, "valid_from": moment, "observed_at": moment, "source": "test", **extra}


def _config(store, name: str, value: Any) -> None:  # type: ignore[no-untyped-def]
    moment = SESSION - timedelta(days=30)
    store.append("config", [_row(name, moment, revision=1, value_json=json.dumps(value))],
                 ingest_run_id=f"cfg-{name}")


def _event(seq: int, stage: str, actor: str, payload: dict[str, Any]) -> dict[str, Any]:
    return _row(RUN, SESSION, seq=seq, stage=stage, actor=actor, payload_hash="x",
                payload=json.dumps(payload, ensure_ascii=False))


@pytest.fixture
def desk(store):  # type: ignore[no-untyped-def]
    store.seed_config_defaults()
    _config(store, "selector.n_candidates", 2)
    _config(store, "selector.exit_rank", 3)
    _config(store, "selector.risk_floor_percentile", 0.2)
    _config(store, "selector.rebalance_every", 10)
    _config(store, "selector.rebalance_anchor", "2026-09-28")

    universe, prices = [], []
    for index, day in enumerate([*DAYS, NOW]):
        for offset, entity in enumerate(FUNDAMENTAL):
            universe.append(_row(entity, day, market="KR", name=f"종목{entity[-4]}", is_listed=True,
                                 is_tradable=True, delisted_on=None))
            close = 10_000.0 + index + offset * 100
            prices.append(_row(entity, day, market="KR", open=close, high=close, low=close, close=close,
                               volume=100_000.0, value=1_000_000.0 if entity == G else 5_000_000_000.0,
                               adj_factor=None))
    store.append("universe", universe, ingest_run_id="universe")
    store.append("prices", prices, ingest_run_id="prices")
    store.append("fx", [_row("FX:USDKRW", day, rate=1_350.0) for day in (SESSION - timedelta(days=2), NOW)],
                 ingest_run_id="fx")
    store.append("capital_flows", [_row("FUND", DAYS[0], currency="KRW", amount=100_000_000.0, kind="deposit")],
                 ingest_run_id="flow")
    store.append("analyst_weights", [_row("fundamental", SESSION - timedelta(days=3), market="KR", ic=0.077,
                                          weight=1.0)], ingest_run_id="weights")
    signal_time = SESSION - timedelta(hours=1)
    signals = [
        _row(entity, signal_time, analyst=analyst, analyst_version=f"{analyst}-v0", score=score, confidence=1.0,
             horizon_days=5, features_hash="x", evidence_json="[]", latency_ms=1.0)
        for analyst, table in (("fundamental", FUNDAMENTAL), ("risk", RISK))
        for entity, score in table.items()
    ]
    store.append("signals", signals, ingest_run_id="signals")

    # 보유: D 3,000주 · F 1,000주 (한 달 전 매수)
    bought = SESSION - timedelta(days=30)
    store.append("trades", [
        _row(D, bought, market="KR", side="buy", quantity=3_000.0, price=10_000.0, currency="KRW", fee=0.0,
             tax=0.0, order_id=f"KR-2026-08-28|{D}|buy"),
        _row(F, bought, market="KR", side="buy", quantity=1_000.0, price=10_000.0, currency="KRW", fee=0.0,
             tax=0.0, order_id=f"KR-2026-08-28|{F}|buy"),
    ], ingest_run_id="trades")

    # 세션 기록 — daily.run 이 남기는 모양 그대로.
    store.append("events", [
        _event(0, "observe", "accounting", {"nav": 100_000_000.0, "available_cash": 60_000_000.0, "currency": "KRW"}),
        _event(1, "select", "selector", {"candidates": [D, A], "weights": {"fundamental": 1.0}}),
        _event(2, "allocate", "risk_parity:normal",
               {"weights": {D: 0.4, A: 0.5}, "cadence": "10세션 주기 재조정일 (anchor 뒤 0번째 세션)"}),
        _event(3, "exposure", "trend", {"scale": 0.8, "driver": "trend", "notes": ["지수가 200일 이평 아래"]}),
        _event(4, "execute", "executor", {"orders": [], "blocked_by": "", "notes": []}),
    ], ingest_run_id="events")
    store.append("realized_weights", [
        _row(D, SESSION, market="KR", session_id="KR-2026-09-28", target_weight=0.32, realized_weight=0.30),
    ], ingest_run_id="realized")

    # 오늘 주문: F 매도 4조각(1조각 체결) · A 매수 2조각(미체결)
    moved = NOW - timedelta(hours=5)
    orders = [
        _row(F, SESSION, market="KR", session_id="KR-2026-09-28", slice_seq=seq, side="sell", quantity=250.0,
             limit_price=10_000.0, target_weight=0.0, status="filled" if seq == 0 else "reserved", reason="",
             revision=1) | {"observed_at": moved}
        for seq in range(4)
    ] + [
        _row(A, SESSION, market="KR", session_id="KR-2026-09-28", slice_seq=seq, side="buy", quantity=100.0,
             limit_price=10_000.0, target_weight=0.4, status="sent", reason="", revision=1) | {"observed_at": moved}
        for seq in range(2)
    ]
    store.append("orders", orders, ingest_run_id="orders")
    return store


@pytest.fixture(autouse=True)
def _fresh_cache():  # type: ignore[no-untyped-def]
    why_module._SCREEN_CACHE.clear()
    yield
    why_module._SCREEN_CACHE.clear()


def _decision(store, entity: str | None = None, as_of: datetime | None = None) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    client = create_app(store=store, clock=ReplayClock(NOW)).test_client()
    query = []
    if entity:
        query.append(f"entity={entity}")
    if as_of:
        query.append(f"as_of={as_of.isoformat().replace('+', '%2B')}")
    body = client.get("/api/trading" + ("?" + "&".join(query) if query else "")).get_json()
    return body["data"]["decision"]


def test_기본은_보유_비중_1위_완충으로_남은_이유를_말한다(desk) -> None:  # type: ignore[no-untyped-def]
    d = _decision(desk)
    assert d["entity_id"] == D                       # 보유 중 평가액 1위(점수 1위 G 가 아니다)
    assert d["rl_active"] is False
    assert d["engine_note"] == "RL(강화학습) 꺼짐 — 비중은 규칙이 정한다"
    w = d["why"]
    assert w["session"]["run_id"] == RUN

    score = w["score"]
    assert score["composite"] == pytest.approx(0.6)
    assert (score["rank_all"], score["n_all"]) == (5, 7)          # 신호 낸 전 종목(G·A·B·C 다음)
    assert (score["rank_tradable"], score["n_tradable"]) == (4, 6)  # G 가 관문에서 빠진 뒤
    assert score["smoothing_span"] == 5                            # config ranker.smoothing_span

    f = w["filters"]
    assert f["risk_status"] == "pass" and f["gate_reason"] is None
    assert f["risk_score"] == pytest.approx(0.3)
    assert f["risk_threshold"] == pytest.approx(0.1)               # 6종목 위험 점수의 20% 분위
    assert f["counts"]["risk_floor"] == 5

    r = w["rule"]
    assert r["verdict"] == "buffer_keep"
    assert (r["rank"], r["n_candidates"], r["exit_rank"]) == (3, 2, 3)
    assert r["held"] is True and r["selected"] is True
    assert r["rebalance_every"] == 10 and r["holding_day"] is False
    assert r["next_rebalance"] == "2026-10-14"                     # 9/28 anchor 뒤 10번째 거래일(개천절·한글날 휴장)

    wt = w["weight"]
    assert wt["allocator"] == "risk_parity:normal"
    assert wt["allocated"] == pytest.approx(0.4)
    assert wt["exposure_scale"] == pytest.approx(0.8)
    assert wt["target"] == pytest.approx(0.32)                     # 0.4 × 0.8 — exposure.apply
    assert wt["realized"] == pytest.approx(0.30)
    assert w["orders"] == {"count": 0}


def test_후보_밖_종목은_순위와_완충_자리를_말한다(desk) -> None:  # type: ignore[no-untyped-def]
    w = _decision(desk, B)["why"]
    r = w["rule"]
    assert r["verdict"] == "pushed_out"                  # 2위인데 명단 [D, A] 밖
    assert r["rank"] == 2 and r["buffer_slots"] == 1     # D 가 완충으로 한 자리를 지켰다
    assert w["weight"]["allocated"] is None and w["weight"]["target"] is None
    assert w["orders"] == {"count": 0}

    e = _decision(desk, E)["why"]["rule"]
    assert e["verdict"] == "not_top" and e["rank"] == 4


def test_위험_필터_탈락과_관문_탈락을_가른다(desk) -> None:  # type: ignore[no-untyped-def]
    c = _decision(desk, C)["why"]
    assert c["filters"]["risk_status"] == "cut"
    assert c["filters"]["risk_score"] == pytest.approx(-0.9)
    assert c["rule"]["verdict"] == "filtered" and c["rule"]["rank"] is None

    g = _decision(desk, G)["why"]
    assert g["filters"]["gate_reason"] == "거래대금 하한 미달"
    assert g["filters"]["risk_status"] == "not_reached"
    assert g["score"]["rank_all"] == 1 and g["score"]["rank_tradable"] is None
    assert g["rule"]["verdict"] == "filtered"


def test_보유_중_완충_밖이면_매도와_오늘_주문을_말한다(desk) -> None:  # type: ignore[no-untyped-def]
    w = _decision(desk, F)["why"]
    assert w["rule"]["verdict"] == "sell_out_of_buffer" and w["rule"]["rank"] == 5
    o = w["orders"]
    assert (o["count"], o["side"], o["filled_slices"]) == (4, "sell", 1)
    assert o["quantity"] == pytest.approx(1_000.0)

    a = _decision(desk, A)["why"]
    assert a["rule"]["verdict"] == "buy"
    assert (a["orders"]["count"], a["orders"]["side"], a["orders"]["filled_slices"]) == (2, "buy", 0)


def test_되감으면_세션_전에는_모른다(desk) -> None:  # type: ignore[no-untyped-def]
    """세션 기록이 관측되기 전으로 되감으면 명단·배수·필터를 **모른다** — 뒤에 적힌 기록을 끌어오지 않는다."""
    d = _decision(desk, D, as_of=SESSION - timedelta(minutes=30))
    w = d["why"]
    assert w["session"] is None
    assert w["filters"]["known"] is False
    assert w["rule"]["verdict"] == "unknown" and w["rule"]["next_rebalance"] is None
    assert w["weight"]["allocated"] is None and w["weight"]["exposure_scale"] is None
    assert w["orders"] == {"count": 0}
    # 점수는 그때 이미 관측된 신호로 말한다(세션 1시간 전 신호).
    assert w["score"]["composite"] == pytest.approx(0.6)


def test_rl_이_켜진_세션이면_그렇게_말한다(desk) -> None:  # type: ignore[no-untyped-def]
    """켜졌는지는 세션 기록(allocate 의 actor)이 말한다 — 화면이 설정을 짐작하지 않는다."""
    desk.append("events", [
        _row("session-KR-2026-09-29", NOW - timedelta(hours=1), seq=2, stage="allocate", actor="rl",
             payload_hash="x", payload=json.dumps({"weights": {D: 0.3}})),
    ], ingest_run_id="events-rl")
    d = _decision(desk, D)
    assert d["rl_active"] is True
    assert d["engine_note"].startswith("RL(강화학습) 켜짐")
