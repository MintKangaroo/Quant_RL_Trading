"""학습 탭 '마지막 모델 회차' 칸 — 진행 기록만 보이고 판정 창 성적은 안 보인다.

이 화면이 지켜야 하는 것은 밀도나 색이 아니라 **사전등록**이다. 학습이 도는 동안 사람이 볼 수 있는
숫자에 판정 창 수익·IC 가 섞이면, 그 뒤의 판정은 "안 본 구간" 이 아니다. 그래서 응답의 칸 목록을
테스트가 직접 센다 — 서비스에 한 줄을 더하는 순간 여기서 걸린다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import shutil

import pytest
from flask import Flask

from quant_rl_trading.dashboard.api import learning as learning_api
from quant_rl_trading.dashboard.app import SafeJSONProvider
from quant_rl_trading.dashboard.services import learning as service
from quant_rl_trading.replay.clock import ReplayClock

NOW = datetime(2026, 10, 6, 9, tzinfo=UTC)
#: 응답의 시행 칸에 있어도 되는 이름. **수익·IC·MDD·회전은 없다.**
ALLOWED_TRIAL_KEYS = {
    "trial", "kind", "axis", "markets", "metric", "seeds", "n_seeds", "units_per_seed",
    "units_done", "units_total", "progress", "mean_unit_s", "eta_seconds", "last_at",
    "early_share", "early_n", "curves", "last_note",
}


def progress_row(trial: str, seed: int, block: int, *, at: datetime, n_blocks: int = 41,
                 elapsed: float = 60.0, early: bool = False) -> dict[str, Any]:
    return {
        "entity_id": trial, "valid_from": at, "observed_at": at, "source": "test",
        "market": "KR+US", "seed": seed, "n_seeds": 5, "block": block, "n_blocks": n_blocks,
        "train_loss": 1.0 - 0.01 * block, "val_loss": 1.1 - 0.005 * block,
        "metric": "mse / spearman(−)", "stopped_early": early, "elapsed_s": elapsed,
        "note": f"블록 {block}",
    }


@pytest.fixture
def seeded(store):  # type: ignore[no-untyped-def]
    store.seed_config_defaults()   # envelope 이 신선도·임계치를 config 에서 읽는다
    rows = [progress_row("BE", seed, block, at=NOW - timedelta(hours=10 - block), early=(block == 1))
            for seed in (0, 1) for block in (0, 1, 2)]
    store.append("trial_progress", rows, ingest_run_id="progress-seed")
    return store


def make_app(store: Any, clock: Any) -> Flask:
    app = Flask(__name__)
    app.json = SafeJSONProvider(app)
    app.config["QUANT_RL_STORE"] = store
    app.config["QUANT_RL_CLOCK"] = clock
    app.register_blueprint(learning_api.bp)
    app.config.update(TESTING=True)
    return app


def test_기록이_없으면_0행이라고_말한다(store: Any) -> None:
    data = service.final_round_progress(store, as_of=NOW)
    assert data["has_data"] is False and data["trials"] == [] and data["verdicts"] == []


def test_진행률과_예상_완료를_기록이_말하는_분모로_낸다(seeded: Any) -> None:
    data = service.final_round_progress(seeded, as_of=NOW)
    assert data["has_data"] is True
    trial = next(t for t in data["trials"] if t["trial"] == "BE")
    assert trial["kind"] == "model" and trial["axis"] == "block"
    assert trial["seeds"] == [0, 1] and trial["n_seeds"] == 5
    # 분모는 기록의 n_seeds × n_blocks 다 — 관측된 시드 둘로 세면 진행률이 2.5배 뛴다.
    assert trial["units_total"] == 5 * 41 and trial["units_done"] == 6
    assert trial["eta_seconds"] == pytest.approx((5 * 41 - 6) * 60.0)
    assert trial["early_share"] == pytest.approx(2 / 6)
    # 곡선은 시드별로 갈라진다 — 한 줄로 이으면 시드 사이의 계단이 학습 곡선처럼 보인다.
    assert [c["seed"] for c in trial["curves"]] == [0, 1]
    assert trial["curves"][0]["x"] == [0, 1, 2]


def test_응답에_판정_창_수익_칸이_없다(seeded: Any) -> None:
    data = service.final_round_progress(seeded, as_of=NOW)
    for trial in data["trials"]:
        assert set(trial) == ALLOWED_TRIAL_KEYS
        assert set(trial["curves"][0]) == {"seed", "x", "train", "val"}


def test_as_of_이후의_행은_보이지_않는다(store: Any) -> None:
    later = NOW + timedelta(days=1)
    store.append("trial_progress", [progress_row("BF", 0, 0, at=later)], ingest_run_id="later")
    assert service.final_round_progress(store, as_of=NOW)["has_data"] is False
    assert service.final_round_progress(store, as_of=later + timedelta(minutes=1))["has_data"] is True


def test_같은_블록을_다시_돌리면_마지막_기록만_그린다(store: Any) -> None:
    first = NOW - timedelta(hours=5)
    store.append("trial_progress", [progress_row("BE", 0, 0, at=first, elapsed=10.0)],
                 ingest_run_id="run-1")
    store.append("trial_progress", [progress_row("BE", 0, 0, at=NOW - timedelta(hours=1), elapsed=99.0)],
                 ingest_run_id="run-2")
    trial = service.final_round_progress(store, as_of=NOW)["trials"][0]
    assert trial["units_done"] == 1 and trial["mean_unit_s"] == pytest.approx(99.0)


def test_폴드_축_시행은_폴드로_센다(store: Any) -> None:
    """BG 는 블록이 아니라 워크포워드 폴드로 돈다. 축 이름을 뭉개면 3/41 과 1/2 가 같은 칸에 섞인다."""
    row = {
        "entity_id": "BG", "valid_from": NOW - timedelta(hours=2), "observed_at": NOW - timedelta(hours=2),
        "source": "test", "market": "KR", "seed": 0, "n_seeds": 3, "fold": 1, "n_folds": 2,
        "train_loss": -0.001, "val_loss": -0.002, "metric": "reward(−) / valid edge(−)",
        "stopped_early": True, "elapsed_s": 1800.0, "note": "적합 결정 ~60",
    }
    store.append("trial_progress", [row], ingest_run_id="bg-1")
    trial = service.final_round_progress(store, as_of=NOW)["trials"][0]
    assert trial["axis"] == "fold" and trial["units_total"] == 6 and trial["units_done"] == 1


def test_대조군은_모델과_구분해_표시한다(store: Any) -> None:
    store.append("trial_progress", [progress_row("C1", 0, 0, at=NOW - timedelta(hours=1))],
                 ingest_run_id="c1")
    assert service.final_round_progress(store, as_of=NOW)["trials"][0]["kind"] == "control"


def test_판정_줄은_시행_대장에서_그대로_온다(seeded: Any) -> None:
    at = NOW - timedelta(hours=1)
    seeded.append("research_trials", [{
        "entity_id": "final-model-round-2026-10:BE", "valid_from": at, "observed_at": at,
        "source": "trial_final_transformer", "market": "KR", "family": "ranker", "n_trials": 1,
        "protocol_hash": "abc123", "detail": "기각 | 시드 평균 연수익 ...",
    }], ingest_run_id="verdict-be")
    verdicts = service.final_round_progress(seeded, as_of=NOW)["verdicts"]
    assert [v["entity_id"] for v in verdicts] == ["final-model-round-2026-10:BE"]
    assert verdicts[0]["detail"].startswith("기각")


def test_api_가_as_of_를_받는다(seeded: Any) -> None:
    client = make_app(seeded, ReplayClock(NOW)).test_client()
    body = client.get("/api/learning/final-round").get_json()
    assert body["as_of"].startswith("2026-10-06")     # 불변식 9 — 봉투가 as_of 를 되돌려준다
    assert body["data"]["has_data"] is True

    past = client.get("/api/learning/final-round?as_of=2026-10-01T00:00:00Z").get_json()
    assert past["data"]["has_data"] is False


# --------------------------------------------------------------------------- 렌더

#: 진행이 **차 있는** 응답. `tests/dashboard/payloads/learning.json` 의 그것은 창고에 아직 진행 행이
#: 없어 `has_data: false` 라 표·차트 경로를 한 줄도 안 지난다. 학습이 도는 밤에 화면이 죽는 것을
#: 잡으려면 채워진 모양으로도 한 번 돌려야 한다(test_tab_render.py 와 같은 이유, 대상만 이 칸이다).
FILLED = {
    "as_of": "2026-10-06T09:00:00+00:00", "live": True, "lookback_days": 90, "thresholds": {},
    "data": {
        "has_data": True,
        "trials": [
            {"trial": "BE", "kind": "model", "axis": "block", "markets": ["KR+US"],
             "metric": "mse / spearman(−)", "seeds": [0, 1], "n_seeds": 5, "units_per_seed": 41,
             "units_done": 6, "units_total": 205, "progress": 6 / 205, "mean_unit_s": 600.0,
             "eta_seconds": 119400.0, "last_at": "2026-10-06T08:40:00+00:00",
             "early_share": 0.33, "early_n": 6, "last_note": "재학습 2회",
             "curves": [{"seed": 0, "x": [0, 1, 2], "train": [1.0, 0.98, 0.97],
                         "val": [1.1, 1.09, 1.09]},
                        {"seed": 1, "x": [0, 1, 2], "train": [1.0, 0.99, 0.98],
                         "val": [1.1, 1.1, None]}]},
            {"trial": "C0", "kind": "control", "axis": "block", "markets": ["KR+US"], "metric": "",
             "seeds": [0], "n_seeds": 5, "units_per_seed": 41, "units_done": 41, "units_total": 205,
             "progress": 0.2, "mean_unit_s": 61.0, "eta_seconds": None,
             "last_at": "2026-10-05T23:10:00+00:00", "early_share": None, "early_n": 0,
             "last_note": "", "curves": [{"seed": 0, "x": [0], "train": [None], "val": [None]}]},
        ],
        "verdicts": [{"entity_id": "final-model-round-2026-10:BF", "family": "ranker",
                      "protocol_hash": "abc123", "at": "2026-10-05T22:00:00+00:00",
                      "detail": "기각 | ① 시드 평균 ..."}],
    },
}


@pytest.mark.skipif(shutil.which("node") is None, reason="node 가 없다")
def test_렌더러가_채워진_응답으로_끝까지_돈다(tmp_path: Any) -> None:
    import json
    import re
    import subprocess

    from tests.dashboard import test_tab_render as harness

    payloads = json.loads((harness.PAYLOADS / "learning.json").read_text(encoding="utf-8"))
    payloads["learning/final-round"] = FILLED
    ids = sorted(
        set(re.findall(r'id="([^"]+)"', (harness.TEMPLATES / "learning.html").read_text()))
        | set(re.findall(r'id="([^"]+)"', (harness.TEMPLATES / "_scope.html").read_text()))
    )
    # 하니스는 없는 id 에 null 을 준다(브라우저와 같다) — 그러면 렌더러가 조용히 첫 줄에서 돌아가고
    # 테스트는 통과한다. 그래서 칸이 템플릿에 **있다는 것**을 먼저 단언한다.
    for needed in ("final-round-progress", "final-round-verdicts", "chart-final-round"):
        assert needed in ids, f"learning.html 에 {needed} 칸이 없다"
    source = (harness.STATIC / "learning.js").read_text()
    match = re.search(r"runAll\(\[([^\]]*)\]\)\s*;", source)
    assert match
    source = source[: match.start()] + source[match.end():]
    js = "\n".join([
        harness.HARNESS.replace("IDS", json.dumps(ids)),
        (harness.STATIC / "scope.js").read_text(),
        source,
        harness.DRIVER.replace("PAYLOADS", json.dumps(payloads)).replace("JOBS", "renderFinalRound"),
    ])
    path = tmp_path / "final_round.js"
    path.write_text(js, encoding="utf-8")
    result = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=60)
    assert "OK" in result.stdout, f"{result.stdout}\n{result.stderr}"


def test_주_장부가_모의계좌면_연구_창고의_진행을_읽는다(tmp_path: Any) -> None:
    """2026-09-28: 화면 주 장부가 data/_paper 라 BE 학습 중에도 "데이터 없음" 이었다 — 진행은 data 에 적힌다."""
    from quant_rl_trading.store import Store

    research = Store(root=tmp_path / "data")
    research.seed_config_defaults()
    research.append("trial_progress", [progress_row("BE", 0, 0, at=NOW - timedelta(hours=1))], ingest_run_id="p")
    paper = Store(root=tmp_path / "data" / "_paper")
    paper.seed_config_defaults()
    client = make_app(paper, ReplayClock(NOW)).test_client()
    got = client.get(f"/api/learning/final-round?as_of={NOW.isoformat()}").get_json()["data"]
    assert got["has_data"] and [t["trial"] for t in got["trials"]] == ["BE"]
