"""ranker — 순위 정규분위·모델 선택·신호 0건 규칙."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quant_rl_trading.analysts import ranker as ranker_module
from quant_rl_trading.analysts.ranker import (
    FEATURES, RankerAnalyst, VERSION, model_dir, rank_gauss, usable_model,
)
from quant_rl_trading.collectors.market_hours import Market
from quant_rl_trading.replay.clock import ReplayClock
from quant_rl_trading.session.signals import SCORERS
from quant_rl_trading.store import Store


def test_rank_gauss_is_symmetric_and_grouped() -> None:
    frame = pd.DataFrame({
        "g": ["a"] * 4 + ["b"] * 3,
        "x": [1.0, 2.0, 3.0, 4.0, 10.0, np.nan, 30.0],
    })
    out = rank_gauss(frame, ["x"], by=["g"])
    a = out.loc[out["g"] == "a", "x"].to_numpy()
    assert np.allclose(a, -a[::-1])                      # 대칭
    assert a[0] < a[1] < a[2] < a[3]                     # 단조
    b = out.loc[out["g"] == "b", "x"].to_numpy()
    assert b[1] == 0.0                                   # 결측 = 중앙
    assert b[0] < 0 < b[2]
    single = rank_gauss(frame.loc[frame["g"] == "a"], ["x"])["x"].to_numpy()
    assert np.allclose(single, a)                        # 그룹 없이도 같은 함수


def _write_model(root: Path, *, through: str, usable: str, version: str = VERSION) -> None:
    import lightgbm as lgb

    rng = np.random.default_rng(0)
    X = rng.normal(size=(500, len(FEATURES))).astype(np.float32)
    y = X[:, 3] + 0.1 * rng.normal(size=500)             # fundamental 을 따라간다
    booster = lgb.train({"objective": "regression", "verbose": -1, "min_data_in_leaf": 5, "num_leaves": 4},
                        lgb.Dataset(X, y, feature_name=list(FEATURES)), num_boost_round=20)
    folder = model_dir(root); folder.mkdir(parents=True, exist_ok=True)
    stem = folder / f"{version}-{through.replace('-', '')}"
    booster.save_model(f"{stem}.txt")
    Path(f"{stem}.json").write_text(json.dumps({
        "version": version, "trained_through": through, "usable_from": usable, "features": list(FEATURES), "rows": 500,
    }), encoding="utf-8")


def test_usable_model_respects_usable_from_and_version(tmp_path: Path) -> None:
    _write_model(tmp_path, through="2026-05-31", usable="2026-06-01")
    _write_model(tmp_path, through="2026-06-30", usable="2026-07-01")
    _write_model(tmp_path, through="2026-08-31", usable="2026-09-01", version="ranker-v9.9.9")
    assert usable_model(tmp_path, as_of=datetime(2026, 5, 15, tzinfo=UTC)) is None
    assert usable_model(tmp_path, as_of=datetime(2026, 6, 15, tzinfo=UTC)).trained_through.isoformat() == "2026-05-31"
    chosen = usable_model(tmp_path, as_of=datetime(2026, 9, 2, tzinfo=UTC))
    assert chosen.trained_through.isoformat() == "2026-06-30"   # 다른 버전은 안 본다


def _seed_signals(store: Store, *, as_of: datetime, n: int = 40) -> None:
    rows = []
    for i in range(n):
        for analyst in ("chart", "event", "flow_kr", "fundamental", "regime", "risk"):
            rows.append({
                "entity_id": f"KR:{i:06d}", "valid_from": as_of, "observed_at": as_of, "source": "test",
                "analyst": analyst, "analyst_version": f"{analyst}-t", "score": float((i * 7 + hash(analyst) % 11) % n) / n - 0.5,
                "confidence": 1.0, "horizon_days": 5, "features_hash": "x", "evidence_json": "[]", "latency_ms": 0.0,
            })
    store.append("signals", rows, ingest_run_id=f"test-signals-{as_of:%Y%m%d}")


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(root=tmp_path / "wh")


def test_no_model_means_no_signal(store: Store) -> None:
    as_of = datetime(2026, 8, 3, 7, tzinfo=UTC)
    _seed_signals(store, as_of=as_of)
    analyst = RankerAnalyst(store, ReplayClock(as_of), market=Market.KR, models_root=store.root)
    assert analyst.run(as_of) == []


def test_scores_only_the_as_of_session(store: Store) -> None:
    _write_model(store.root, through="2026-06-30", usable="2026-07-01")
    yesterday = datetime(2026, 8, 3, 7, tzinfo=UTC)
    _seed_signals(store, as_of=yesterday)
    analyst = RankerAnalyst(store, ReplayClock(yesterday), market=Market.KR, models_root=store.root)
    today = yesterday + timedelta(days=1)
    assert analyst.run(today) == []                      # 어제 점수로 오늘을 매기지 않는다
    signals = analyst.run(yesterday)
    assert len(signals) == 40
    assert all(s.analyst == "ranker" and s.analyst_version == VERSION for s in signals)
    scores = pd.Series({s.entity_id: s.score for s in signals})
    assert scores.abs().max() <= 1.0 and scores.std() > 0.1
    assert all(e.key != "is_us" for s in signals for e in s.evidence)
    assert analyst.run(datetime(2026, 6, 1, 7, tzinfo=UTC)) == []   # 학습창 안은 안 낸다
    # 같은 순간을 다른 시간대로 불러도 "오늘 세션" 이다 (미장 as_of 는 UTC, 창고는 KST 로 돌려준다)
    from zoneinfo import ZoneInfo
    same = yesterday.astimezone(ZoneInfo("Asia/Seoul"))
    assert len(analyst.run(same)) == 40


def test_ranker_runs_last_in_both_markets() -> None:
    for market in (Market.KR, Market.US):
        names = list(SCORERS[market])
        assert names[-1] == "ranker"
        assert SCORERS[market]["ranker"] is RankerAnalyst


def test_score_features_match_registered_inputs() -> None:
    assert set(ranker_module.BASE_ANALYSTS.values()) == set(ranker_module.SCORE_FEATURES)
    assert "volume" not in ranker_module.BASE_ANALYSTS


def test_smoothing_blends_with_previous_session_score(store: Store) -> None:
    """시행 N — 직전 세션 자기 점수와 EMA(span 5). 직전 점수가 없으면 raw 그대로."""
    store.seed_config_defaults()
    assert int(store.config("ranker.smoothing_span", as_of=datetime(2026, 8, 4, tzinfo=UTC))) == 5
    _write_model(store.root, through="2026-06-30", usable="2026-07-01")
    day1 = datetime(2026, 8, 3, 7, tzinfo=UTC)
    _seed_signals(store, as_of=day1)
    analyst = RankerAnalyst(store, ReplayClock(day1), market=Market.KR, models_root=store.root)
    first = analyst.run(day1)                       # 직전 점수 없음 → raw
    assert len(first) == 40
    store.append("signals", [s.row(observed_at=day1, source="test") for s in first], ingest_run_id="test-ranker-day1")
    day2 = day1 + timedelta(days=1)
    _seed_signals(store, as_of=day2)                # 같은 기초 점수 → raw 도 같다
    second = analyst.run(day2)
    raw = {s.entity_id: s.score for s in first}
    blended = {s.entity_id: s.score for s in second}
    # 같은 raw 를 같은 이전값과 섞으면 값이 같다(불변); 다른 이전값이면 그쪽으로 끌린다.
    assert all(abs(blended[e] - raw[e]) < 1e-9 for e in raw)
    rows = [{**s.row(observed_at=day2, source="test"), "score": 0.9} for s in second]
    store.append("signals", rows, ingest_run_id="test-ranker-day2-high")
    day3 = day2 + timedelta(days=1)
    _seed_signals(store, as_of=day3)
    third = {s.entity_id: s.score for s in analyst.run(day3)}
    pulled_up = sum(1 for e in raw if third[e] > raw[e] + 1e-6)
    assert pulled_up >= 35                          # 직전 세션 0.9 쪽으로 끌려간다
