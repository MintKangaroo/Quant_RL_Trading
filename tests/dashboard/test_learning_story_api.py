"""학습 탭 ② '지금 매매에 쓰이는 모델' · ③ '과거 학습 내역'.

지켜야 하는 것:
- 화면의 설정 값은 **창고 config 의 그 시점 값**이다(불변식 10 · 9). 코드에 적힌 숫자가 아니다.
- 과거 시행은 카탈로그의 판정을 **그대로** 옮기고, 날짜가 as_of 뒤인 줄은 안 보인다(되감기).
- 시행 대장에만 있는 줄은 버리지 않고 '대장에만 있음' 으로 보인다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from flask import Flask

from quant_rl_trading.dashboard.api import learning as learning_api
from quant_rl_trading.dashboard.app import SafeJSONProvider
from quant_rl_trading.dashboard.services import model_story as story
from quant_rl_trading.replay.clock import ReplayClock

NOW = datetime(2026, 9, 29, 3, tzinfo=UTC)


def write_catalog(path: Path, trials: list[dict[str, Any]], lessons: list[dict[str, Any]] | None = None) -> Path:
    path.write_text(yaml.safe_dump({"lessons": lessons or [], "trials": trials}, allow_unicode=True), encoding="utf-8")
    return path


def trial(tid: str, day: str, **extra: Any) -> dict[str, Any]:
    return {"id": tid, "date": day, "category": "선정", "title": f"시행 {tid}", "what": "무엇",
            "result": "기각", "why": "이유", "doc": f"docs/protocols/x-{tid}.md", **extra}


def ledger_row(entity: str, at: datetime, detail: str, family: str = "selection") -> dict[str, Any]:
    return {"entity_id": entity, "valid_from": at, "observed_at": at, "source": "test", "market": "KR",
            "family": family, "n_trials": 1, "protocol_hash": "abc", "detail": detail}


# --------------------------------------------------------------------------- 카탈로그


def test_체크인된_카탈로그는_전부_읽힌다() -> None:
    """진짜 docs/trials-catalog.yaml — 읽지 못한 줄이 없고, 분류·결과가 정해진 말뿐이며, 문서 경로가 실재한다."""
    catalog = story.load_catalog()
    assert catalog.problems == []
    assert len(catalog.trials) > 20
    assert len(catalog.lessons) == 3
    for entry in catalog.trials:
        assert entry["category"] in story.CATEGORIES, entry["id"]
        assert entry["result"] in story.RESULTS, entry["id"]
        assert entry["what"] and entry["why"], entry["id"]
        assert not entry["doc"] or (story.REPO_ROOT / entry["doc"]).is_file(), entry["doc"]


def test_날짜가_as_of_뒤인_시행은_안_보인다(store: Any, tmp_path: Path) -> None:
    path = write_catalog(tmp_path / "c.yaml", [trial("A", "2026-09-01"), trial("B", "2026-10-05")],
                         lessons=[{"text": "옛 교훈", "date": "2026-09-01"}, {"text": "나중 교훈", "date": "2026-10-05"}])
    data = story.trial_history(store, as_of=NOW, catalog_path=path)
    assert [t["id"] for t in data["trials"]] == ["A"]
    assert [x["text"] for x in data["lessons"]] == ["옛 교훈"]
    later = story.trial_history(store, as_of=datetime(2026, 10, 6, tzinfo=UTC), catalog_path=path)
    assert [t["id"] for t in later["trials"]] == ["B", "A"]   # 최신이 위


def test_as_of_날짜는_한국시간으로_자른다(store: Any, tmp_path: Path) -> None:
    """9/30 00:30 KST 는 UTC 로 9/29 — 9/30 판정은 한국 날짜로 이미 보인다."""
    path = write_catalog(tmp_path / "c.yaml", [trial("A", "2026-09-30")])
    as_of = datetime(2026, 9, 29, 15, 30, tzinfo=UTC)
    assert [t["id"] for t in story.trial_history(store, as_of=as_of, catalog_path=path)["trials"]] == ["A"]


def test_모르는_분류_결과는_기타로_날짜_없는_줄은_버리고_말한다(store: Any, tmp_path: Path) -> None:
    path = write_catalog(tmp_path / "c.yaml", [trial("A", "2026-09-01", category="요리", result="대충"),
                                               {"id": "B", "category": "선정"}])
    data = story.trial_history(store, as_of=NOW, catalog_path=path)
    assert data["trials"][0]["category"] == "기타" and data["trials"][0]["result"] == "기타"
    assert data["problems"] == ["B: 날짜를 읽지 못함"]


def test_시행_대장과_합친다(store: Any, tmp_path: Path) -> None:
    at = NOW - timedelta(days=2)
    store.append("research_trials", [
        ledger_row("x-A:A", at, "기각 — 규약 이름으로 맞춘 줄"),
        ledger_row("retro-thing", at, "소급 기록", family="rl-config"),
        ledger_row("orphan-2026-09:Z9", at, "채택 — 카탈로그에 없는 줄", family="ranker"),
        ledger_row("orphan-2026-09:Z8", at + timedelta(days=5), "as_of 뒤"),
    ], ingest_run_id="ledger-1")
    path = write_catalog(tmp_path / "c.yaml", [trial("A", "2026-09-01"),
                                               trial("K", "2026-09-02", ledger=["retro-thing"])])
    data = story.trial_history(store, as_of=NOW, catalog_path=path)
    by_id = {t["id"]: t for t in data["trials"]}
    # 규약(문서이름:시행)으로도, 카탈로그가 적은 id 로도 맞는다.
    assert [x["entity_id"] for x in by_id["A"]["ledger"]] == ["x-A:A"]
    assert [x["entity_id"] for x in by_id["K"]["ledger"]] == ["retro-thing"]
    # 대장에만 있는 줄 — 판정 문구의 첫 말만 결과로, 분류는 family 로.
    orphan = by_id["Z9"]
    assert orphan["source"] == "ledger_only" and orphan["result"] == "채택" and orphan["category"] == "랭커"
    assert "Z8" not in by_id                                  # as_of 뒤의 대장 줄은 안 보인다
    assert data["summary"]["total"] == 3 and data["summary"]["ledger_only"] == 1
    assert data["summary"]["by_result"]["기각"] == 2 and data["summary"]["by_result"]["채택"] == 1


def test_대장_문구가_판정으로_시작하지_않으면_결과를_짐작하지_않는다(store: Any, tmp_path: Path) -> None:
    store.append("research_trials", [ledger_row("retro-ic", NOW - timedelta(days=1), "소급 집계 12회")],
                 ingest_run_id="ledger-2")
    data = story.trial_history(store, as_of=NOW, catalog_path=write_catalog(tmp_path / "c.yaml", []))
    assert data["trials"][0]["result"] is None and data["trials"][0]["id"] == "retro-ic"


# --------------------------------------------------------------------------- 지금 매매에 쓰이는 모델


def test_설정_값은_그_시점_창고_config_에서_온다(store: Any) -> None:
    store.seed_config_defaults()
    data = story.live_models(store, as_of=NOW)
    shown = {s["key"]: s for stage in data["stages"] for s in stage["settings"]}
    for key in ("selector.n_candidates", "selector.exit_rank", "selector.rebalance_every",
                "allocator.baseline", "execution.slice_count", "execution.slice_interval_sec"):
        assert shown[key]["found"] is True
        assert shown[key]["value"] == store.config(key, as_of=NOW), key
    # 창고에 없는 키는 지어내지 않는다.
    assert shown["exposure.source"]["found"] is False and shown["exposure.source"]["value"] is None


def test_단계_순서와_설명은_서비스_상수_한_곳에서(store: Any) -> None:
    store.seed_config_defaults()
    data = story.live_models(store, as_of=NOW)
    assert [s["key"] for s in data["stages"]] == [s["key"] for s in story.PIPELINE_STAGES]
    assert all(s["plain"] for s in data["stages"])


def test_채택_기록과_병행_트랙은_as_of_이전_것만(store: Any) -> None:
    store.seed_config_defaults()
    early = datetime(2026, 9, 2, 3, tzinfo=UTC)
    data = story.live_models(store, as_of=early)
    adopted = {a["trial"] for s in data["stages"] for a in s["adopted"]}
    assert "L" not in adopted and "AO" not in adopted      # 9/3 · 9/24 채택
    assert {t["name"] for t in data["tracks"]} == {"미장 shadow"}
    now = story.live_models(store, as_of=NOW)
    assert {"L", "AO", "AU"} <= {a["trial"] for s in now["stages"] for a in s["adopted"]}


def ranker_sidecar(folder: Path, through: str, usable: str) -> None:
    stem = f"ranker-v0.1.0-{through.replace('-', '')}"
    (folder / f"{stem}.txt").write_text("booster", encoding="utf-8")
    (folder / f"{stem}.json").write_text(json.dumps({
        "version": "ranker-v0.1.0", "trained_through": through, "usable_from": usable,
        "features": ["risk", "event"], "rows": 10, "rounds": 300, "params": {"objective": "regression"},
        "gain": {"event": 0.3, "risk": 0.7}, "protocol_hash": "h",
    }), encoding="utf-8")


def test_랭커_모델은_as_of_에_쓸_수_있는_최신_한_벌(store: Any, tmp_path: Path) -> None:
    store.seed_config_defaults()
    folder = tmp_path / "models" / "ranker"
    folder.mkdir(parents=True)
    ranker_sidecar(folder, "2026-05-31", "2026-06-01")
    ranker_sidecar(folder, "2026-06-30", "2026-07-01")
    model = story.live_models(store, as_of=datetime(2026, 6, 15, tzinfo=UTC), models_root=tmp_path)["ranker"]["model"]
    assert model["trained_through"] == "2026-05-31" and model["models_available"] == 1
    model = story.live_models(store, as_of=NOW, models_root=tmp_path)["ranker"]["model"]
    assert model["trained_through"] == "2026-06-30" and model["models_available"] == 2
    assert [g["feature"] for g in model["gain"]] == ["risk", "event"]     # 큰 기여부터
    # 폴더가 없으면 모름(None) — 지어내지 않는다.
    assert story.live_models(store, as_of=NOW, models_root=tmp_path / "없음")["ranker"]["model"] is None


def test_랭커_적중도는_시장별_최신_측정(store: Any) -> None:
    store.seed_config_defaults()
    rows = []
    for i, (market, ic) in enumerate([("KR", 0.10), ("KR", 0.095), ("US", 0.06)]):
        at = NOW - timedelta(days=10 - i)
        rows.append({"entity_id": "ranker", "valid_from": at, "observed_at": at, "source": "test",
                     "market": market, "analyst_version": "ranker-v0.1.0", "weight": 1.0, "ic": ic})
    store.append("analyst_weights", rows, ingest_run_id="w-1")
    markets = {m["market"]: m for m in story.live_models(store, as_of=NOW)["ranker"]["markets"]}
    assert markets["KR"]["ic"] == pytest.approx(0.095) and len(markets["KR"]["history"]) == 2
    assert markets["US"]["ic"] == pytest.approx(0.06)


# --------------------------------------------------------------------------- API


def make_app(store: Any) -> Flask:
    app = Flask(__name__)
    app.json = SafeJSONProvider(app)
    app.config["QUANT_RL_STORE"] = store
    app.config["QUANT_RL_CLOCK"] = ReplayClock(NOW)
    app.register_blueprint(learning_api.bp)
    app.config.update(TESTING=True)
    return app


@pytest.mark.parametrize("path", ["/api/learning/live-models", "/api/learning/trial-history"])
def test_API_는_as_of_를_받고_되돌려준다(store: Any, path: str) -> None:
    store.seed_config_defaults()
    client = make_app(store).test_client()
    response = client.get(path, query_string={"as_of": "2026-09-20T09:00:00+09:00"})
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body["as_of"].startswith("2026-09-20T09:00:00") and body["live"] is False
    assert client.get(path, query_string={"as_of": "2026-09-20T09:00:00"}).status_code == 400   # 시간대 없음 거부


def test_되감으면_과거_내역도_그때까지만(store: Any) -> None:
    store.seed_config_defaults()
    client = make_app(store).test_client()
    early = client.get("/api/learning/trial-history", query_string={"as_of": "2026-08-30T09:00:00+09:00"}).get_json()
    late = client.get("/api/learning/trial-history", query_string={"as_of": "2026-09-29T09:00:00+09:00"}).get_json()
    assert 0 < early["data"]["summary"]["total"] < late["data"]["summary"]["total"]
    assert all(t["date"] <= "2026-08-30" for t in early["data"]["trials"])
