"""매매 과정 탭 (dashboard.md §7-2) — 흐름도·후보 칩·AI 속.

진짜 창고 위에서:
- 흐름도 숫자가 세션이 쓴 선정 함수(`screen`)의 단계 수와 세션 기록에서 나오는지,
- 단계 켜짐/꺼짐이 **그 세션 시각의 설정**에서 나오는지(10/13 전환을 손 안 대고 따라가는 근거),
- 후보 칩이 판정일 전엔 "판정 전 — 결과 없음" 이고 대장 행은 관측 시각 ≤ as_of 일 때만 보이는지,
- AI 속 기여가 그 세션 모델의 `pred_contrib` 이고 막대 합 + 기준값 = 모델 원점수인지 본다.

종목 일곱(점수 순 G·A·B·C·D·E·F) — G 는 거래대금 하한 밑, C 는 위험 최하위. 상위 N=2. 세션 명단 [D, A].
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

import numpy as np
import pytest

from quant_rl_trading.dashboard import create_app
from quant_rl_trading.dashboard.services import alpha_ir, model_story, pipeline_view
from quant_rl_trading.dashboard.services import why as why_module
from quant_rl_trading.replay.clock import ReplayClock

SESSION = datetime(2026, 9, 28, 7, 0, tzinfo=UTC)           # 9/28 16:00 KST
NOW = datetime(2026, 9, 29, 6, 40, tzinfo=UTC)
DAYS = [SESSION - timedelta(days=offset) for offset in range(400, 0, -1)]
A, B, C, D, E, F, G = (f"KR:00{n}000" for n in range(1, 8))
FUNDAMENTAL = {G: 0.95, A: 0.9, B: 0.8, C: 0.7, D: 0.6, E: 0.5, F: 0.4}
RISK = {A: 0.5, B: 0.4, C: -0.9, D: 0.3, E: 0.2, F: 0.1, G: 0.3}
RUN = "session-KR-2026-09-28"


def _row(entity: str, moment: datetime, **extra: Any) -> dict[str, Any]:
    return {"entity_id": entity, "valid_from": moment, "observed_at": moment, "source": "test", **extra}


def _config(store, name: str, value: Any, moment: datetime | None = None, revision: int = 1) -> None:  # type: ignore[no-untyped-def]
    moment = moment or SESSION - timedelta(days=30)
    store.append("config", [_row(name, moment, revision=revision, value_json=json.dumps(value))],
                 ingest_run_id=f"cfg-{name}-{revision}")


def _event(seq: int, stage: str, actor: str, payload: dict[str, Any]) -> dict[str, Any]:
    return _row(RUN, SESSION, seq=seq, stage=stage, actor=actor, payload_hash="x",
                payload=json.dumps(payload, ensure_ascii=False))


def _desk(store, *, alpha: str = "fundamental") -> Any:  # type: ignore[no-untyped-def]
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
    store.append("events", [
        _event(0, "observe", "accounting", {"nav": 100_000_000.0, "available_cash": 60_000_000.0, "currency": "KRW"}),
        _event(1, "select", "selector", {"candidates": [D, A], "weights": {alpha: 1.0}}),
        _event(2, "allocate", "risk_parity:normal",
               {"weights": {D: 0.4, A: 0.5}, "cadence": "10세션 주기 재조정일 (anchor 뒤 0번째 세션)"}),
        _event(3, "exposure", "regime", {"scale": 0.8, "driver": "regime", "notes": ["약세 국면"]}),
        _event(4, "execute", "executor", {"orders": [
            {"entity_id": A, "side": "buy", "quantity": 100, "order_id": "x1"},
            {"entity_id": A, "side": "buy", "quantity": 100, "order_id": "x2"},
        ], "blocked_by": "", "notes": ["조각 분할 전송 — 지금 1건 · 나중에 1건"]}),
    ], ingest_run_id="events")
    return store


@pytest.fixture
def desk(store):  # type: ignore[no-untyped-def]
    return _desk(store)


@pytest.fixture(autouse=True)
def _fresh_cache():  # type: ignore[no-untyped-def]
    why_module._SCREEN_CACHE.clear()
    pipeline_view._CENSUS_CACHE.clear()
    pipeline_view._INSIDE_CACHE.clear()
    yield
    why_module._SCREEN_CACHE.clear()
    pipeline_view._CENSUS_CACHE.clear()
    pipeline_view._INSIDE_CACHE.clear()


def _get(store, path: str, as_of: datetime | None = None) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    client = create_app(store=store, clock=ReplayClock(NOW)).test_client()
    sep = "&" if "?" in path else "?"
    url = path + (f"{sep}as_of={as_of.isoformat().replace('+', '%2B')}" if as_of else "")
    response = client.get(url)
    assert response.status_code == 200, response.get_data(as_text=True)[:400]
    return response.get_json()


def _nodes(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {n["key"]: n for n in body["data"]["nodes"]}


# --------------------------------------------------------------------------- 표 정합


def test_단계_표와_설명_문구는_한_곳에서_온다() -> None:
    story_keys = {s["key"] for s in model_story.PIPELINE_STAGES}
    assert set(pipeline_view.STORY_OF.values()) <= story_keys     # 문구는 PIPELINE_STAGES 하나
    pair_labels = {label for *_, label in alpha_ir.PAIRS}
    from quant_rl_trading.dashboard.services.pipeline_view import REPO_ROOT
    from tools.apply_switch import COMPONENTS

    for cand in model_story.STAGE_CANDIDATES:
        assert cand["stage"] in pipeline_view.FLOW_KEYS, cand["id"]
        assert (REPO_ROOT / cand["doc"]).is_file(), cand["doc"]
        assert cand["pair"] is None or cand["pair"] in pair_labels, cand["pair"]
        assert cand["switch"] is None or cand["switch"] in COMPONENTS, cand["switch"]
        for j in cand["judge"]:
            assert j["date"] is None or date.fromisoformat(j["date"])


def test_전환_구성_요소가_바꾸는_키마다_흐름_단계가_있다() -> None:
    """apply_switch 가 바꾸는 키가 흐름도 어느 단계의 설정으로 보인다 — 켜지면 나타난다."""
    from tools.apply_switch import COMPONENTS

    shown = {key for keys in pipeline_view.STAGE_SETTINGS.values() for key, _ in keys}
    for part, values in COMPONENTS.items():
        for key in values:
            assert key in shown, f"{part}: {key} 가 흐름도 설정에 없다"


# --------------------------------------------------------------------------- 흐름도


def test_흐름도_숫자는_세션이_쓴_선정_함수와_세션_기록에서_온다(desk) -> None:  # type: ignore[no-untyped-def]
    body = _get(desk, "/api/pipeline")
    data = body["data"]
    assert data["session"]["run_id"] == RUN
    assert [n["key"] for n in data["nodes"]] == list(pipeline_view.FLOW_KEYS)
    n = _nodes(body)
    assert (n["universe"]["n_in"], n["universe"]["n_out"]) == (7, 6)       # G 거래대금 하한
    assert n["universe"]["drops"]["reasons"][0]["reason"] == "거래대금 하한 미달"
    assert (n["scored"]["n_in"], n["scored"]["n_out"]) == (6, 6)
    assert (n["risk_floor"]["n_in"], n["risk_floor"]["n_out"]) == (6, 5)   # C 위험 최하위
    assert n["risk_floor"]["drops"]["examples"][0]["entity_id"] == C
    assert n["select"]["n_out"] == 2                                        # 세션 기록 [D, A]
    assert n["exposure"]["scale"] == pytest.approx(0.8)
    assert (n["execute"]["n_out"], n["execute"]["buy"]) == (2, 2)
    assert n["model"]["kind"] == "rule" and n["model"]["model"]["alpha"] == ["fundamental"]
    assert n["verdicts"]["kind"] == "llm" and n["commentary"]["kind"] == "llm"
    # 꺼진 하한은 흐리게 — 설정 기본값 ''·0
    for key in ("extra_floor", "ceiling", "combo_floor"):
        assert n[key]["active"] is False and n[key]["n_out"] is None
    assert [s["entity_id"] for s in data["selected"]] == [D, A]


def test_단계_켜짐은_그_세션_시각의_설정이_말한다(desk) -> None:  # type: ignore[no-untyped-def]
    """전환(apply_switch)이 설정 정정본으로 들어오면 그 뒤 세션부터 켜지고, 앞 세션으로 되감으면 꺼져 있다."""
    after = SESSION + timedelta(hours=3)          # 세션 시각 뒤에 적힌 정정본 — 이 세션은 못 본다
    _config(desk, "selector.extra_floor_analyst", "tsfm", moment=after, revision=2)
    _config(desk, "selector.extra_floor_percentile", 0.2, moment=after, revision=2)
    n = _nodes(_get(desk, "/api/pipeline"))
    assert n["extra_floor"]["active"] is False

    before = SESSION - timedelta(days=1)          # 세션 전에 적힌 정정본 — 켜진다(TTM = AI)
    _config(desk, "selector.extra_floor_analyst", "tsfm", moment=before, revision=3)
    _config(desk, "selector.extra_floor_percentile", 0.2, moment=before, revision=3)
    why_module._SCREEN_CACHE.clear()
    n = _nodes(_get(desk, "/api/pipeline"))
    assert n["extra_floor"]["active"] is True and n["extra_floor"]["kind"] == "ai"
    assert n["extra_floor"]["analyst"] == "tsfm"


def test_되감으면_세션_전에는_모른다(desk) -> None:  # type: ignore[no-untyped-def]
    before = SESSION - timedelta(minutes=30)
    body = _get(desk, "/api/pipeline", as_of=before)
    assert body["as_of"] == before.isoformat()                 # 불변식 9 — as_of 를 되돌려준다
    assert body["data"]["session"] is None
    n = _nodes(body)
    assert n["universe"]["n_out"] is None and n["select"]["n_out"] is None
    inside = _get(desk, "/api/pipeline/inside", as_of=before)
    assert inside["as_of"] == before.isoformat()
    assert inside["data"]["session"] is None and "모름" in inside["data"]["reason"]


# --------------------------------------------------------------------------- 후보 칩


def test_후보는_판정일_전엔_결과가_없고_대장은_관측_뒤에만_보인다(desk) -> None:  # type: ignore[no-untyped-def]
    cands = {c["id"]: c for c in _get(desk, "/api/pipeline")["data"]["candidates"]}
    tf = cands["TF"]
    assert tf["status"] == "판정 전 — 결과 없음" and tf["results"] == []
    assert tf["stage"] == "extra_floor" and tf["applied"] is False
    assert tf["doc_state"] == "fixed" and len(tf["doc_hash"]) == 16

    judged = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)
    desk.append("research_trials", [_row("vault-early-additions-2026-10:TF", judged, market="KR", family="selection",
                                         n_trials=1, protocol_hash="175dadac51431be0",
                                         detail="기각 | ① 원리 t −1.1 × | 판정: 기각")], ingest_run_id="trial-tf")
    early = {c["id"]: c for c in _get(desk, "/api/pipeline", as_of=judged - timedelta(hours=1))["data"]["candidates"]}
    assert early["TF"]["results"] == []                         # 판정 전 시점에는 안 보인다
    late = {c["id"]: c for c in _get(desk, "/api/pipeline", as_of=judged + timedelta(hours=1))["data"]["candidates"]}
    assert late["TF"]["status"] == "기각"                       # 대장 원문 첫 말 그대로
    assert late["TF"]["results"][0]["detail"].startswith("기각 | ① 원리")


def test_전환_값이_설정에_들어오면_실전_적용으로_보인다(desk) -> None:  # type: ignore[no-untyped-def]
    later = SESSION + timedelta(days=16)
    _config(desk, "selector.extra_floor_analyst", "tsfm", moment=later, revision=4)
    _config(desk, "selector.extra_floor_percentile", 0.2, moment=later, revision=4)
    cands = {c["id"]: c for c in _get(desk, "/api/pipeline", as_of=later + timedelta(hours=1))["data"]["candidates"]}
    assert cands["TF"]["applied"] is True and cands["TF"]["status"] == "실전 적용"
    assert cands["TB"]["applied"] is False                     # TB 는 상한 키까지 같아야 한다


# --------------------------------------------------------------------------- AI 속


def _tiny_ranker(root, *, usable_from: str = "2026-07-01") -> None:  # type: ignore[no-untyped-def]
    """작은 LightGBM 랭커 — 실전과 같은 산출물 모양(부스터 + 사이드카)."""
    import lightgbm as lgb

    from quant_rl_trading.analysts.ranker import FEATURES, VERSION, model_dir

    rng = np.random.default_rng(0)
    x = rng.normal(size=(600, len(FEATURES))).astype(np.float32)
    y = x[:, 3] * 0.6 - x[:, 5] * 0.3 + rng.normal(scale=0.1, size=600)
    booster = lgb.train({"objective": "regression", "num_leaves": 7, "verbose": -1, "min_data_in_leaf": 5},
                        lgb.Dataset(x, y), num_boost_round=20)
    folder = model_dir(root)
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{VERSION}-20260630"
    booster.save_model(str(folder / f"{stem}.txt"))
    (folder / f"{stem}.json").write_text(json.dumps({
        "version": VERSION, "trained_through": "2026-06-30", "usable_from": usable_from,
        "features": list(FEATURES), "rows": 600, "gain": {"fundamental": 0.7, "risk": 0.3}}), encoding="utf-8")


def test_AI_속_기여는_세션_모델의_pred_contrib_이고_합이_원점수다(store) -> None:  # type: ignore[no-untyped-def]
    desk = _desk(store, alpha="ranker")
    _tiny_ranker(desk.root)
    body = _get(desk, f"/api/pipeline/inside?entity={A}")
    d = body["data"]
    assert d["session"]["run_id"] == RUN and d["alpha"] == ["ranker"]
    c = d["contrib"]
    assert c["available"] is True and c["model"] == "ranker"
    assert c["file"] == "ranker-v0.1.0-20260630.txt"
    total = sum(b["contrib"] for b in c["bars"]) + c["bias"]
    assert total == pytest.approx(c["raw"], abs=1e-6)          # 막대 합 + 기준값 = 모델 원점수
    assert 1 <= c["rank"] <= c["n"]
    # 흐름도의 점수 모델 칸도 같은 모델 파일을 말한다(AI 색)
    n = _nodes(_get(desk, "/api/pipeline"))
    assert n["model"]["kind"] == "ai" and n["model"]["model"]["model"]["file"] == "ranker-v0.1.0-20260630.txt"
    # TTM 저장 점수가 없으면 지어내지 않는다 — 빈 목록 + 저장 범위 안내
    assert d["ttm"]["points"] == [] and "저장되지 않는다" in d["ttm"]["stored_note"]


def test_모델을_쓸_수_없는_시점이면_기여는_이유와_함께_비어_있다(store) -> None:  # type: ignore[no-untyped-def]
    desk = _desk(store, alpha="ranker")
    _tiny_ranker(desk.root, usable_from="2026-12-01")         # 세션 뒤에야 쓸 수 있는 모델
    c = _get(desk, f"/api/pipeline/inside?entity={A}")["data"]["contrib"]
    assert c["available"] is False and "모델" in c["reason"]


def test_화면_페이지가_뜬다(desk) -> None:  # type: ignore[no-untyped-def]
    client = create_app(store=desk, clock=ReplayClock(NOW)).test_client()
    html = client.get("/pipeline").get_data(as_text=True)
    assert 'id="pl-flow"' in html and "pipeline.js" in html and "매매 과정" in html
