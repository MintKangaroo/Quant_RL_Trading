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
#: 2026-09-28 카드 재설계로 더한 칸도 **진행·시각·상태·설명**뿐이다 — 상태(과적합 의심)도 학습창 안쪽 검증만 본다.
ALLOWED_TRIAL_KEYS = {
    "trial", "kind", "axis", "markets", "metric", "seeds", "n_seeds", "units_per_seed",
    "units_done", "units_total", "progress", "mean_unit_s", "eta_seconds", "last_at",
    "early_share", "early_n", "curves", "last_note",
    # 카드(2026-09-28)
    "about", "score_label", "eta_at", "eta_label", "started_at", "elapsed_wall_s", "last_label",
    "since_last_s", "status", "status_reason",
}
#: 곡선 칸. `score` 는 `val`(학습창 안쪽 검증)의 부호를 사람이 읽는 방향으로 되돌린 것뿐이다.
ALLOWED_CURVE_KEYS = {"seed", "x", "train", "val", "score"}
#: 칸 이름에 이 말이 들어가면 판정 창 성적이 새어 나간 것이다.
FORBIDDEN_WORDS = ("return", "ret", "ic", "mdd", "drawdown", "turnover", "sharpe", "ir", "judge")


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
        assert set(trial["curves"][0]) == ALLOWED_CURVE_KEYS
    for key in ALLOWED_TRIAL_KEYS | ALLOWED_CURVE_KEYS:
        words = key.split("_")
        assert not any(w in FORBIDDEN_WORDS for w in words), f"{key} 는 판정 창 성적처럼 읽힌다"


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


# --------------------------------------------------------------------------- 카드 (2026-09-28)
# 사용자: "학습이 잘 되고 있는지 · 얼마나 남았는지 · 뭐가 학습되고 있는지". 상태는 셋 — 정상 · 느림/멈춤 의심 ·
# 과적합 의심 — 이고 기준(배수 3 · 창 5)은 config 에서 온다(conftest 창고는 yaml 기본값을 심는다).


def walk(store: Any, trial: str, *, last: datetime, n: int = 5, gap_s: float = 600.0,
         train: list[float] | None = None, val: list[float] | None = None,
         n_blocks: int = 41, n_seeds: int = 5, metric: str = "mse / spearman(−)") -> None:
    """시드 0 으로 블록 0..n−1 을 gap_s 간격으로 적는다. 마지막 블록의 기록 시각이 `last`."""
    rows = []
    for block in range(n):
        at = last - timedelta(seconds=gap_s * (n - 1 - block))
        row = progress_row(trial, 0, block, at=at, n_blocks=n_blocks, elapsed=gap_s)
        row["n_seeds"] = n_seeds
        row["metric"] = metric
        if train is not None:
            row["train_loss"] = train[block]
        if val is not None:
            row["val_loss"] = val[block]
        rows.append(row)
    store.append("trial_progress", rows, ingest_run_id=f"walk-{trial}-{last.isoformat()}")


def one(store: Any, trial: str = "BE") -> dict[str, Any]:
    return next(t for t in service.final_round_progress(store, as_of=NOW)["trials"] if t["trial"] == trial)


def test_상태_정상_최근_기록이고_내부_검증이_나빠지지_않는다(store: Any) -> None:
    store.seed_config_defaults()
    walk(store, "BE", last=NOW - timedelta(minutes=5),
         train=[1.0, 0.98, 0.96, 0.95, 0.94], val=[-0.02, -0.03, -0.035, -0.04, -0.041])
    trial = one(store)
    assert trial["status"] == "ok", trial["status_reason"]
    assert "나빠지지 않는다" in trial["status_reason"]
    assert trial["about"].startswith("가격 흐름을 읽는 트랜스포머")
    # 부호를 되돌려 "높을수록 좋음" 으로 그린다 — val −0.041 은 순위상관 +0.041 이다.
    assert trial["score_label"] == "내부 검증 순위상관 (높을수록 좋음)"
    assert trial["curves"][0]["score"][-1] == pytest.approx(0.041)


def test_상태_멈춤_의심_마지막_기록이_평균_단위의_배수를_넘었다(store: Any) -> None:
    store.seed_config_defaults()
    factor = store.config("dashboard.training_stall_factor", as_of=NOW)
    # 블록 하나에 10분인데 마지막 기록이 (배수 × 10분 + 1분) 전.
    walk(store, "BE", last=NOW - timedelta(minutes=10 * factor + 1))
    trial = one(store)
    assert trial["status"] == "stalled"
    assert "배를 넘었다" in trial["status_reason"]
    # 멈춘 학습의 "예상 끝" 은 이미 지났거나 믿을 수 없다 — 말하지 않는다(남은 초는 그대로 준다).
    assert trial["eta_at"] is None and trial["eta_label"] is None and trial["eta_seconds"] is not None


def test_상태_과적합_의심_학습_손실은_줄고_내부_검증은_나빠진다(store: Any) -> None:
    store.seed_config_defaults()
    walk(store, "BE", last=NOW - timedelta(minutes=5),
         train=[1.0, 0.95, 0.90, 0.85, 0.80], val=[-0.05, -0.04, -0.03, -0.02, -0.01])
    trial = one(store)
    assert trial["status"] == "overfit", trial["status_reason"]
    assert "내부 검증은 나빠진다" in trial["status_reason"]


def test_추세_창보다_짧으면_과적합을_판단하지_않는다(store: Any) -> None:
    store.seed_config_defaults()
    window = store.config("dashboard.training_trend_window", as_of=NOW)
    walk(store, "BE", last=NOW - timedelta(minutes=5), n=window - 1,
         train=[1.0 - 0.1 * i for i in range(window - 1)], val=[0.1 * i for i in range(window - 1)])
    assert one(store)["status"] == "ok"


def test_설정이_없으면_상태를_지어내지_않는다(store: Any) -> None:
    walk(store, "BE", last=NOW - timedelta(minutes=5))      # seed_config_defaults 를 안 불렀다
    trial = one(store)
    assert trial["status"] == "unknown" and "training_stall_factor" in trial["status_reason"]


def test_예상_끝은_마지막_기록_더하기_남은_양이고_한국시간_as_of_기준_말이다(store: Any) -> None:
    store.seed_config_defaults()
    # 3블록 × 2시드 = 6, 5개 끝 → 남은 1 × 10분. 마지막 기록 08:55Z → 끝 09:05Z = 18:05 KST(as_of 와 같은 날).
    walk(store, "BE", last=NOW - timedelta(minutes=5), n_blocks=3, n_seeds=2,
         train=[1.0, 0.9, 0.8, 0.7, 0.6], val=[0.5, 0.4, 0.3, 0.2, 0.1])
    trial = one(store)
    assert trial["eta_at"] == "2026-10-06T09:05:00+00:00"
    assert trial["eta_label"] == "오늘 18:05"
    assert trial["last_label"] == "오늘 17:55"
    # 경과 = as_of − (첫 기록 − 그 단위 시간). 첫 기록 08:15Z, 단위 10분 → 시작 08:05Z → 55분.
    assert trial["elapsed_wall_s"] == pytest.approx(55 * 60)
    # 자정을 넘기면 '내일' — 2026-10-06 20:40Z 는 10/07 05:40 KST.
    import pandas as pd
    assert service._kst_label(pd.Timestamp("2026-10-06T20:40:00Z"), NOW) == "내일 05:40"


def test_as_of_를_되감으면_그때의_상태가_나온다(store: Any) -> None:
    """상태·경과는 벽시계가 아니라 as_of 로 잰다 — 지난 시점으로 가면 그때는 정상이었다."""
    store.seed_config_defaults()
    walk(store, "BE", last=NOW - timedelta(hours=3))
    assert one(store)["status"] == "stalled"
    back = service.final_round_progress(store, as_of=NOW - timedelta(hours=3) + timedelta(minutes=2))
    assert back["trials"][0]["status"] == "ok"


def test_대기열은_등록_순서이고_기록이나_판정이_있으면_빠진다(store: Any) -> None:
    store.seed_config_defaults()
    empty = service.final_round_progress(store, as_of=NOW)
    assert [q["trial"] for q in empty["queued"]] == ["BE", "BF", "BG", "D1"]
    assert empty["queued"][1]["about"].startswith("순위 전용 GBM")

    walk(store, "BE", last=NOW - timedelta(minutes=5))
    walk(store, "D1a", last=NOW - timedelta(minutes=6), metric="mixed / -hard rule ann(val)")
    at = NOW - timedelta(hours=1)
    store.append("research_trials", [{
        "entity_id": "final-model-round-2026-10:BF", "valid_from": at, "observed_at": at,
        "source": "trial_final_lambdarank", "market": "KR", "family": "ranker", "n_trials": 1,
        "protocol_hash": "abc", "detail": "기각 | ...",
    }], ingest_run_id="verdict-bf")
    data = service.final_round_progress(store, as_of=NOW)
    # BE 는 돌고 있고, BF 는 판정이 적혔고(기록이 창 밖이어도), D1 은 변형 D1a 로 돌고 있다 → BG 만 대기.
    assert [q["trial"] for q in data["queued"]] == ["BG"]
    d1a = next(t for t in data["trials"] if t["trial"] == "D1a")
    assert d1a["about"].startswith("결정 중심 학습")
    assert d1a["score_label"] == "내부 검증 규칙 포트 연수익 (높을수록 좋음)"


def test_끝난_대조군은_끝남이고_도는_시행_뒤에_선다(store: Any) -> None:
    store.seed_config_defaults()
    # C0 은 3블록 × 1시드를 다 돌았다(마지막 기록이 BE 보다 늦어도 끝난 것은 아래로).
    walk(store, "C0", last=NOW - timedelta(minutes=1), n=3, n_blocks=3, n_seeds=1,
         metric="손실 없음(GBM · 조기 종료를 안 쓴다)")
    walk(store, "BE", last=NOW - timedelta(minutes=5))
    trials = service.final_round_progress(store, as_of=NOW)["trials"]
    assert [t["trial"] for t in trials] == ["BE", "C0"]
    c0 = trials[1]
    assert c0["status"] == "done" and c0["kind"] == "control" and c0["eta_at"] is None
    assert c0["about"].startswith("비교 기준(대조군)")
    assert "끝남" in c0["status_reason"]


def test_모르는_시행은_설명_없이_이름만(store: Any) -> None:
    store.seed_config_defaults()
    walk(store, "ZZ9", last=NOW - timedelta(minutes=5), metric="loss")
    trial = one(store, "ZZ9")
    assert trial["about"] == "" and trial["score_label"] == "내부 검증 손실 (낮을수록 좋음)"


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
             "about": "가격 흐름을 읽는 트랜스포머 — 최근 60일 가격·거래 패턴으로 5일 뒤 순위를 맞힌다",
             "score_label": "내부 검증 순위상관 (높을수록 좋음)",
             "eta_at": "2026-10-07T17:50:00+00:00", "eta_label": "내일 02:50",
             "started_at": "2026-10-06T07:40:00+00:00", "elapsed_wall_s": 4800.0,
             "last_label": "오늘 17:40", "since_last_s": 1200.0, "status": "overfit",
             "status_reason": "최근 블록 5개: 학습 손실은 줄어드는데 내부 검증은 나빠진다",
             "curves": [{"seed": 0, "x": [0, 1, 2], "train": [1.0, 0.98, 0.97],
                         "val": [-0.03, -0.02, -0.01], "score": [0.03, 0.02, 0.01]},
                        {"seed": 1, "x": [0, 1, 2], "train": [1.0, 0.99, 0.98],
                         "val": [-0.03, -0.03, None], "score": [0.03, 0.03, None]}]},
            {"trial": "D1a", "kind": "model", "axis": "block", "markets": ["KR"], "metric": "",
             "seeds": [0], "n_seeds": None, "units_per_seed": None, "units_done": 2, "units_total": None,
             "progress": None, "mean_unit_s": None, "eta_seconds": None, "last_at": "2026-10-06T08:00:00+00:00",
             "early_share": None, "early_n": 0, "last_note": "", "about": "결정 중심 학습",
             "score_label": "내부 검증 손실 (낮을수록 좋음)", "eta_at": None, "eta_label": None,
             "started_at": "2026-10-06T07:00:00+00:00", "elapsed_wall_s": 7200.0, "last_label": "오늘 17:00",
             "since_last_s": 3600.0, "status": "unknown", "status_reason": "속도를 모른다",
             "curves": [{"seed": 0, "x": [0, 1], "train": [None, None], "val": [None, None],
                         "score": [None, None]}]},
            {"trial": "C0", "kind": "control", "axis": "block", "markets": ["KR+US"], "metric": "",
             "seeds": [0, 1, 2, 3, 4], "n_seeds": 5, "units_per_seed": 41, "units_done": 205,
             "units_total": 205, "progress": 1.0, "mean_unit_s": 61.0, "eta_seconds": None,
             "last_at": "2026-10-05T07:34:00+00:00", "early_share": None, "early_n": 0,
             "last_note": "", "about": "비교 기준(대조군) — 지금 쓰는 GBM 랭커",
             "score_label": "내부 검증 손실 (낮을수록 좋음)", "eta_at": None, "eta_label": None,
             "started_at": "2026-10-05T04:00:00+00:00", "elapsed_wall_s": 12840.0, "last_label": "어제 16:34",
             "since_last_s": 91000.0, "status": "done", "status_reason": "다 돌았다 — 어제 16:34 끝남",
             "curves": [{"seed": 0, "x": [0], "train": [None], "val": [None], "score": [None]}]},
            {"trial": "C1", "kind": "control", "axis": "block", "markets": ["KR+US"], "metric": "",
             "seeds": [0], "n_seeds": 1, "units_per_seed": 1, "units_done": 1,
             "units_total": 1, "progress": 1.0, "mean_unit_s": 61.0, "eta_seconds": None,
             "last_at": "2026-10-05T11:52:00+00:00", "early_share": None, "early_n": 0,
             "last_note": "", "about": "비교 기준(대조군) — 같은 GBM 에 새 재료를 넣은 것",
             "score_label": "내부 검증 손실 (낮을수록 좋음)", "eta_at": None, "eta_label": None,
             "started_at": "2026-10-05T11:51:00+00:00", "elapsed_wall_s": 61.0, "last_label": "어제 20:52",
             "since_last_s": 76000.0, "status": "done", "status_reason": "다 돌았다 — 어제 20:52 끝남",
             "curves": [{"seed": 0, "x": [0], "train": [None], "val": [None], "score": [None]}]},
        ],
        "queued": [{"trial": "BG", "about": "잔차 RL — GBM 점수 위에서 비중만 조금 조정"}],
        "settings": {"stall_factor": 3.0, "trend_window": 5},
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
    for needed in ("final-round-progress", "final-round-verdicts"):
        assert needed in ids, f"learning.html 에 {needed} 칸이 없다"
    source = (harness.STATIC / "learning.js").read_text()
    match = re.search(r"runAll\(\[([^\]]*)\]\)\s*;", source)
    assert match
    source = source[: match.start()] + source[match.end():]
    js = "\n".join([
        harness.HARNESS.replace("IDS", json.dumps(ids)),
        (harness.STATIC / "scope.js").read_text(),
        source,
        harness.DRIVER.replace("PAYLOADS", json.dumps(payloads)).replace("JOBS", "renderFinalRound")
        # 그려진 칸을 꺼내 본다 — "안 죽었다" 만으로는 카드가 비어도 통과한다.
        .replace('() => console.log("OK")',
                 '() => { console.log("OK"); console.log("HTML<<" + '
                 'document.getElementById("final-round-progress").innerHTML + ">>"); }'),
    ])
    path = tmp_path / "final_round.js"
    path.write_text(js, encoding="utf-8")
    result = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=60)
    assert "OK" in result.stdout, f"{result.stdout}\n{result.stderr}"
    html = result.stdout.split("HTML<<", 1)[1].rsplit(">>", 1)[0]
    # 무엇을 배우나 · 상태 배지 · 진행 · 예상 끝 · 그래프 칸
    assert "가격 흐름을 읽는 트랜스포머" in html and "과적합 의심" in html
    assert "시드 2/5 · 블록 6/205" in html and "내일 02:50" in html
    assert 'id="chart-fr-BE-score"' in html and 'id="chart-fr-BE-train"' in html
    # 남은 양을 모르면 칸을 숨긴다 — 문구로 자리를 차지하지 않는다. 전문 용어는 풀어 쓴다.
    assert "남은 양을 모른다" not in html and "조기 종료" not in html
    assert "과적합을 막으려 학습을 일찍 멈춘 비율" in html
    # 순서: 지금 도는 것 → 대기 → 끝난 비교 기준(한 줄로 접힘, 카드 없음)
    assert html.index("BE") < html.index("fr-queue") < html.index("비교 기준 C0·C1 준비 완료")
    assert "어제 16:34 · 어제 20:52" in html
    assert 'id="chart-fr-C0-score"' not in html and html.count('<article class="fr-card') == 2


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


def test_재학습_블록이_길어도_멈춤으로_읽지_않는다(store: Any) -> None:
    """2026-09-29: BE 는 평균 3분 블록 사이에 12분짜리 재학습 블록이 끼어 평균 × 3 에서 헛경보가 났다."""
    store.seed_config_defaults()
    factor = store.config("dashboard.training_stall_factor", as_of=NOW)
    rows = []
    for block, secs in enumerate([180.0, 180.0, 720.0, 180.0, 180.0]):
        at = NOW - timedelta(minutes=9) - timedelta(minutes=3 * (4 - block))
        rows.append(progress_row("BE", 0, block, at=at, elapsed=secs))
    store.append("trial_progress", rows, ingest_run_id="retrain-mix")
    # 마지막 기록 9분 전 > 평균(5.2분) 은 넘지만 < 가장 긴 단위 12분 × 배수 — 정상이어야 한다.
    assert 9 * 60 < factor * 720
    assert one(store)["status"] == "ok"


def test_재학습_사이_반복된_손실은_한_번으로_세어_추세를_꾸미지_않는다(store: Any) -> None:
    """재학습 사이 블록은 같은 손실을 반복한다 — 한 걸음의 흔들림을 '과적합 추세' 로 읽지 않는다(2026-09-29)."""
    store.seed_config_defaults()
    train = [1.00, 1.00, 1.00, 0.98, 0.98]
    val = [-0.117, -0.117, -0.117, -0.114, -0.114]   # 재학습 두 번뿐 — 추세를 볼 표본이 아니다
    walk(store, "BE", last=NOW - timedelta(minutes=1), train=train, val=val)
    trial = one(store)
    assert trial["status"] == "ok" and "쌓이면 본다" in trial["status_reason"]


def test_도는_중인데_기록이_없는_시행은_시작됨으로_보인다(store: Any) -> None:
    """2026-09-29: BG 는 폴드가 끝날 때만 적어 첫 기록까지 '대기' 로 보였다. 라이브에서만 /proc 를 본다."""
    store.seed_config_defaults()
    got = service.final_round_progress(store, as_of=NOW, running_scripts={"trial_final_residual_rl"})
    bg = next(q for q in got["queued"] if q["trial"] == "BG")
    assert bg["started"] is True
    assert not next(q for q in got["queued"] if q["trial"] == "D1")["started"]
    # 되감은 화면(running_scripts=None)은 도는 여부를 지어내지 않는다.
    assert not any(q["started"] for q in service.final_round_progress(store, as_of=NOW)["queued"])
