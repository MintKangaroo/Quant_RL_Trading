"""선정 신호 신선도 — 덮어쓰기 알파·켜진 하한이 묵은 신호로 조용히 돌지 않는다 (selector.md §5 0번, 2026-10-11 점검).

지키는 것:
 ① 지금 실전(덮어쓰기 {} · 하한 끔)은 **동작이 안 바뀐다** — 측정표 알파가 묵어도 fault 없이 예전처럼 고른다.
 ② 덮어쓰기 알파(BE2)의 신호가 없거나 묵으면 fault ``alpha_stale`` · 후보 0 (전날 보유 유지는 세션이 한다).
 ③ 켜진 하한·상한(tsfm·ta)의 신호가 없거나 묵으면 fault ``constraint_stale`` — "신호 0건 = 통과" 로 읽지 않는다.
    다음세션 규약(전날 밤 계산분)은 신선하다.
 ④ 알파 confidence 전원 0 이면 fault ``alpha_no_confidence`` — 측정표 경로에서도. 일부라도 0 이 아니면 그대로.
 ⑤ 미장 키 ``weights_override_us: {}`` 가 있으면 국장 BE2 덮어쓰기가 미장에 안 샌다.
 ⑥ fault 는 세션 rc 3 이다.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from quant_rl_trading.collectors.market_hours import Market
from quant_rl_trading.selector import freshness, pipeline
from quant_rl_trading.selector import weights as weights_module
from quant_rl_trading.store import OVERRIDES_FILE

#: 2026-10-14(수) 16:00 KST — 국장 세션 공표 시각(세션 실행기 as_of).
NOW = datetime(2026, 10, 14, 7, 0, tzinfo=UTC)
SESSIONS = [NOW - timedelta(days=offset) for offset in range(400, -1, -1)]
ENTITIES = [f"KR:{i:06d}" for i in range(100, 1100, 100)]   # 10종목
EQUITY = 100_000_000.0


def _signals(analyst: str, when: datetime, *, confidence: float = 1.0, reverse: bool = False,
             entities: list[str] | None = None) -> list[dict]:
    names = entities or ENTITIES
    n = len(names)
    return [{
        "entity_id": e, "valid_from": when, "observed_at": when, "source": "test", "analyst": analyst,
        "analyst_version": f"{analyst}-t", "score": ((n - 1 - i) if reverse else i) / (n - 1) * 2 - 1,
        "confidence": confidence, "horizon_days": 5, "features_hash": "x", "evidence_json": "[]", "latency_ms": 1.0,
    } for i, e in enumerate(names)]


@pytest.fixture
def seeded(store):  # type: ignore[no-untyped-def]
    """10종목 400세션 · 측정표 {ranker: 1, risk: 1}(KR·US) · 그 세션 ranker·risk 신호."""
    store.seed_config_defaults()
    universe, prices = [], []
    for index, day in enumerate(SESSIONS):
        for offset, entity in enumerate(ENTITIES):
            universe.append({"entity_id": entity, "valid_from": day, "observed_at": day, "source": "test",
                             "market": "KR", "name": entity, "is_listed": True, "is_tradable": True,
                             "delisted_on": None})
            close = 10_000.0 + index * 10 + offset * 100
            prices.append({"entity_id": entity, "valid_from": day, "observed_at": day, "source": "test",
                           "market": "KR", "open": close, "high": close, "low": close, "close": close,
                           "volume": 100_000.0, "value": 5_000_000_000.0, "adj_factor": None})
    store.append("universe", universe, ingest_run_id="u")
    store.append("prices", prices, ingest_run_id="p")
    store.append("analyst_weights", [
        # 자연키가 (entity_id, valid_from) 라 시장마다 시각을 달리한다(같으면 한 시장이 다른 시장을 덮는다).
        {"entity_id": n, "valid_from": NOW - timedelta(days=3, hours=h), "observed_at": NOW - timedelta(days=3, hours=h),
         "source": "t", "market": m, "ic": 1.0, "weight": 1.0} for n in ("ranker", "risk") for m, h in (("KR", 0), ("US", 1))
    ], ingest_run_id="w")
    store.append("signals", _signals("ranker", NOW) + _signals("risk", NOW), ingest_run_id="s-today")
    return store


def _override(store, text: str) -> None:  # type: ignore[no-untyped-def]
    (Path(store.root) / OVERRIDES_FILE).write_text(text, encoding="utf-8")


BE2 = "selector.weights_override:\n  be2: 1.0\n  risk: 1.0\n"


# ── ① 지금 실전은 그대로 ─────────────────────────────────────────────────────

def test_live_path_unchanged_when_ranker_fresh(seeded) -> None:  # type: ignore[no-untyped-def]
    result = pipeline.run(seeded, as_of=NOW, market="KR", equity=EQUITY)
    assert result.fault == "" and result.candidates
    assert result.weights == {"ranker": 1.0}


def test_live_path_does_not_check_measured_alpha_freshness(store) -> None:  # type: ignore[no-untyped-def]
    """측정표 알파(ranker)가 묵어도 fault 없이 예전처럼 그 점수로 고른다 — 측정 경로엔 신선도를 안 건다(현행 유지)."""
    seeded = _seed_without_signals(store)
    seeded.append("signals", _signals("ranker", NOW - timedelta(days=2)) + _signals("risk", NOW - timedelta(days=2)),
                  ingest_run_id="s-old")
    result = pipeline.run(seeded, as_of=NOW, market="KR", equity=EQUITY)
    assert result.fault == "" and result.candidates


def test_live_path_ignores_constraint_signals_when_floors_off(seeded) -> None:  # type: ignore[no-untyped-def]
    """하한이 꺼져 있으면 tsfm·ta 가 없어도·묵어도 아무 일 없다."""
    seeded.append("signals", _signals("tsfm", NOW - timedelta(days=4)), ingest_run_id="tsfm-old")
    result = pipeline.run(seeded, as_of=NOW, market="KR", equity=EQUITY)
    assert result.fault == "" and result.candidates


# ── ② 덮어쓰기 알파 ──────────────────────────────────────────────────────────

def test_override_without_be2_signals_faults(seeded) -> None:  # type: ignore[no-untyped-def]
    _override(seeded, BE2)
    result = pipeline.run(seeded, as_of=NOW, market="KR", equity=EQUITY)
    assert result.fault == freshness.ALPHA_STALE and result.candidates == ()
    assert any("be2(최근 5일 신호 없음, 세션 2026-10-14)" in note for note in result.trace.notes)


def test_override_with_stale_be2_faults(seeded) -> None:  # type: ignore[no-untyped-def]
    """4일 묵은 be2 로 조용히 채점하지 않는다(재현 test_stale_be2_used_silently)."""
    _override(seeded, BE2)
    seeded.append("signals", _signals("be2", NOW - timedelta(days=4)), ingest_run_id="be2-old")
    result = pipeline.run(seeded, as_of=NOW, market="KR", equity=EQUITY)
    assert result.fault == freshness.ALPHA_STALE and result.candidates == ()
    assert any("마지막 신호 2026-10-10" in note for note in result.trace.notes)


def test_override_with_yesterdays_be2_faults(seeded) -> None:  # type: ignore[no-untyped-def]
    """전 세션 공표분(하루 전 16:00) — 이번 세션 마감(15:30) 전이라 묵은 것이다."""
    _override(seeded, BE2)
    seeded.append("signals", _signals("be2", NOW - timedelta(days=1)), ingest_run_id="be2-y")
    assert pipeline.run(seeded, as_of=NOW, market="KR", equity=EQUITY).fault == freshness.ALPHA_STALE


def test_override_with_fresh_be2_selects_by_be2(seeded) -> None:  # type: ignore[no-untyped-def]
    _override(seeded, BE2)
    seeded.append("signals", _signals("be2", NOW, reverse=True), ingest_run_id="be2-today")
    result = pipeline.run(seeded, as_of=NOW, market="KR", equity=EQUITY)
    assert result.fault == "" and result.weights == {"be2": 1.0}
    # be2 가 뒤집은 순서 — 위험 하한(risk 하위 20%)이 ENTITIES[0]·[1] 을 자르고 남은 be2 1위. ranker 면 ENTITIES[-1] 이다
    assert result.candidates[0].entity_id == ENTITIES[2]


def test_override_fault_reaches_dashboard_screen(seeded) -> None:  # type: ignore[no-untyped-def]
    """화면(why.py)도 같은 screen 을 부른다 — 장 중 시각으로 불러도 결정 세션(전날)의 신호를 기준으로 본다."""
    _override(seeded, BE2)
    seeded.append("signals", _signals("be2", NOW), ingest_run_id="be2-today")
    next_morning = NOW + timedelta(hours=18)   # 10/15 10:00 KST — 결정 세션은 10/14
    assert pipeline.screen(seeded, as_of=next_morning, market="KR", equity=EQUITY).fault == ""


# ── ③ 켜진 하한·상한 ─────────────────────────────────────────────────────────

FLOORS = {
    "extra": "selector.extra_floor_analyst: tsfm\nselector.extra_floor_percentile: 0.2\n",
    "ceiling": "selector.ceiling_analyst: tsfm\nselector.ceiling_percentile: 0.1\n",
    "combo": "selector.combo_floor_analyst: tsfm\nselector.combo_floor_percentile: 0.1\n",
}
#: tsfm 은 밤에 마지막 종가까지로 계산 — 10/14 결정에 쓰는 것은 10/14 00:15 KST(= 10/13 15:15 UTC) 계산분.
TSFM_LAST_NIGHT = datetime(2026, 10, 13, 15, 15, tzinfo=UTC)


@pytest.mark.parametrize("kind", sorted(FLOORS))
def test_enabled_floor_without_signals_faults(seeded, kind: str) -> None:  # type: ignore[no-untyped-def]
    _override(seeded, FLOORS[kind])
    result = pipeline.run(seeded, as_of=NOW, market="KR", equity=EQUITY)
    assert result.fault == freshness.CONSTRAINT_STALE and result.candidates == ()
    assert any("tsfm(최근 5일 신호 없음" in note for note in result.trace.notes)


@pytest.mark.parametrize("kind", sorted(FLOORS))
def test_enabled_floor_with_last_nights_signals_runs(seeded, kind: str) -> None:  # type: ignore[no-untyped-def]
    """다음세션 규약 — 전날 밤 계산분이 정상이다(하루 늦춤은 설계)."""
    _override(seeded, FLOORS[kind])
    seeded.append("signals", _signals("tsfm", TSFM_LAST_NIGHT), ingest_run_id="tsfm-night")
    result = pipeline.run(seeded, as_of=NOW, market="KR", equity=EQUITY)
    assert result.fault == "" and result.candidates


def test_enabled_floor_with_two_nights_old_signals_faults(seeded) -> None:  # type: ignore[no-untyped-def]
    """10/13 00:15 계산분(10/12 종가까지)은 10/13 결정용 — 10/14 결정엔 묵었다."""
    _override(seeded, FLOORS["extra"])
    seeded.append("signals", _signals("tsfm", TSFM_LAST_NIGHT - timedelta(days=1)), ingest_run_id="tsfm-old")
    result = pipeline.run(seeded, as_of=NOW, market="KR", equity=EQUITY)
    assert result.fault == freshness.CONSTRAINT_STALE
    assert any("마지막 신호 2026-10-13" in note for note in result.trace.notes)


def test_enabled_floor_actually_cuts(seeded) -> None:  # type: ignore[no-untyped-def]
    """신선하면 하한이 실제로 자른다 — 하위 20% = tsfm 최하위 2종목(뒤집어 넣어 위험 하한과 안 겹치게)."""
    _override(seeded, FLOORS["extra"])
    seeded.append("signals", _signals("tsfm", TSFM_LAST_NIGHT, reverse=True), ingest_run_id="tsfm-night")
    result = pipeline.screen(seeded, as_of=NOW, market="KR", equity=EQUITY)
    assert result.fault == ""
    assert ENTITIES[-1] not in result.scores.index and ENTITIES[-2] not in result.scores.index
    assert ENTITIES[-3] in result.scores.index


# ── ④ confidence 전원 0 ─────────────────────────────────────────────────────

def test_all_zero_confidence_faults_on_measured_path(store) -> None:  # type: ignore[no-untyped-def]
    seeded = _seed_without_signals(store)
    seeded.append("signals", _signals("ranker", NOW, confidence=0.0) + _signals("risk", NOW), ingest_run_id="s0")
    result = pipeline.run(seeded, as_of=NOW, market="KR", equity=EQUITY)
    assert result.fault == freshness.ALPHA_NO_CONFIDENCE and result.candidates == ()
    assert any("confidence 가 전원 0" in note for note in result.trace.notes)


def test_all_zero_confidence_faults_on_override_path(seeded) -> None:  # type: ignore[no-untyped-def]
    _override(seeded, BE2)
    seeded.append("signals", _signals("be2", NOW, confidence=0.0), ingest_run_id="be2-0")
    assert pipeline.run(seeded, as_of=NOW, market="KR", equity=EQUITY).fault == freshness.ALPHA_NO_CONFIDENCE


def test_some_confidence_is_not_a_fault(store) -> None:  # type: ignore[no-untyped-def]
    """두 알파 중 하나만 0 이면 다른 하나로 고른다 — 예전 그대로."""
    seeded = _seed_without_signals(store)
    seeded.append("analyst_weights", [{"entity_id": "fundamental", "valid_from": NOW - timedelta(days=3),
                                       "observed_at": NOW - timedelta(days=3), "source": "t", "market": "KR",
                                       "ic": 1.0, "weight": 1.0}], ingest_run_id="w2")
    seeded.append("signals", _signals("ranker", NOW, confidence=0.0) + _signals("fundamental", NOW, confidence=0.3)
                  + _signals("risk", NOW), ingest_run_id="s1")
    result = pipeline.run(seeded, as_of=NOW, market="KR", equity=EQUITY)
    assert result.fault == "" and result.candidates


# ── ⑤ 미장 키 ───────────────────────────────────────────────────────────────

def test_us_key_keeps_be2_out_of_us(seeded) -> None:  # type: ignore[no-untyped-def]
    """체크인 yaml 의 `weights_override_us: {}` 가 심겨 있으면 미장은 측정표 그대로."""
    _override(seeded, BE2)
    assert weights_module.analyst_weights(seeded, as_of=NOW, market="KR") == {"be2": 1.0}
    assert weights_module.analyst_weights(seeded, as_of=NOW, market="US") == {"ranker": 1.0}
    assert not weights_module.weight_census(seeded, as_of=NOW, market="US").overridden


def test_without_us_key_us_falls_back_to_common(store) -> None:  # type: ignore[no-untyped-def]
    """미장 키가 창고에 없으면 미장이 공통 키로 물러선다 — 이 위험 때문에 yaml·창고에 `weights_override_us: {}` 를 심었다."""
    Path(store.root).mkdir(parents=True, exist_ok=True)
    _override(store, BE2)
    assert weights_module.analyst_weights(store, as_of=NOW, market="US") == {"be2": 1.0}


# ── 결정 세션 · rc ──────────────────────────────────────────────────────────

@pytest.mark.parametrize(("moment", "expected"), [
    (NOW, date(2026, 10, 14)),                                          # 수 16:00 KST — 그날
    (datetime(2026, 10, 14, 1, 0, tzinfo=UTC), date(2026, 10, 13)),     # 수 10:00 KST(장 중) — 전날
    (datetime(2026, 10, 17, 3, 0, tzinfo=UTC), date(2026, 10, 16)),     # 토 — 금요일
    (datetime(2026, 10, 9, 8, 0, tzinfo=UTC), date(2026, 10, 8)),       # 한글날 휴장 — 전 거래일
])
def test_decision_session(moment: datetime, expected: date) -> None:
    assert freshness.decision_session(Market.KR, moment) == expected


@pytest.mark.parametrize("code", [freshness.ALPHA_STALE, freshness.CONSTRAINT_STALE, freshness.ALPHA_NO_CONFIDENCE])
def test_fault_is_rc_3(code: str, capsys: pytest.CaptureFixture[str]) -> None:
    from tools.run_session import exit_code

    assert exit_code(SimpleNamespace(blocked_by="", fault=code, unsent=())) == 3   # type: ignore[arg-type]
    assert code in capsys.readouterr().err


def _seed_without_signals(store):  # type: ignore[no-untyped-def]
    """seeded 와 같되 신호 없이 — 테스트가 신호를 직접 넣는다."""
    store.seed_config_defaults()
    universe, prices = [], []
    for index, day in enumerate(SESSIONS):
        for offset, entity in enumerate(ENTITIES):
            universe.append({"entity_id": entity, "valid_from": day, "observed_at": day, "source": "test",
                             "market": "KR", "name": entity, "is_listed": True, "is_tradable": True,
                             "delisted_on": None})
            close = 10_000.0 + index * 10 + offset * 100
            prices.append({"entity_id": entity, "valid_from": day, "observed_at": day, "source": "test",
                           "market": "KR", "open": close, "high": close, "low": close, "close": close,
                           "volume": 100_000.0, "value": 5_000_000_000.0, "adj_factor": None})
    store.append("universe", universe, ingest_run_id="u")
    store.append("prices", prices, ingest_run_id="p")
    store.append("analyst_weights", [
        # 자연키가 (entity_id, valid_from) 라 시장마다 시각을 달리한다(같으면 한 시장이 다른 시장을 덮는다).
        {"entity_id": n, "valid_from": NOW - timedelta(days=3, hours=h), "observed_at": NOW - timedelta(days=3, hours=h),
         "source": "t", "market": m, "ic": 1.0, "weight": 1.0} for n in ("ranker", "risk") for m, h in (("KR", 0), ("US", 1))
    ], ingest_run_id="w")
    return store
