"""be2 Analyst — 얼린 모델만 쓰고, usable_from 전엔 점수를 안 내고, 판정 도구와 같은 창·같은 망을 먹는다.

지키는 것:
 ① 구조 — 실전 `build_set_ranker` 가 판정 도구 `make_model` 과 같은 파라미터 이름·같은 출력(같은 가중치에서).
 ② 창 — 실전 `window_batch`(국장 ∪ 미장 축, float16 왕복)가 판정 `build_cube` → `window_batch` 와 같은 값.
 ③ 얼린 모델만 — usable_from 전·모델 없음·파일 지문 불일치·창 구멍이면 점수를 안 낸다(이유를 남긴다).
 ④ 끝에서 끝 — 합성 얼리기 → 창고 fa_features → Analyst.run → be2 신호.
 ⑤ 관찰 모드 — 일일 실행기·주간 IC 목록에 없다(운영 장부 가중치를 못 받는다).
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quant_rl_trading.analysts import be2 as be2_module
from quant_rl_trading.analysts import fa_features
from quant_rl_trading.analysts.be2 import Be2Analyst, Be2Model, time_axis, window_batch
from quant_rl_trading.collectors.market_hours import Market, trading_days
from quant_rl_trading.replay.clock import ReplayClock
from quant_rl_trading.schemas.fa import FA_FEATURES, FLAGS

torch = pytest.importorskip("torch")
torch.set_num_threads(2)


# ① 구조 ----------------------------------------------------------------------------------------


def test_constants_match_judged_tool() -> None:
    from tools import trial_final_transformer as be

    for name in ("WINDOW", "D_MODEL", "HEADS", "FFN", "DROPOUT", "TIME_LAYERS", "SET_LAYERS", "TIME_OFFSETS",
                 "N_STEPS", "MIN_SET"):
        assert getattr(be, name) == getattr(be2_module, name), name


def test_set_ranker_matches_judged_architecture() -> None:
    from tools import trial_final_transformer as be

    judged = be.make_model(3, len(FA_FEATURES))
    live = be2_module.build_set_ranker(len(FA_FEATURES))
    assert list(judged.state_dict()) == list(live.state_dict())
    live.load_state_dict(judged.state_dict())
    judged.eval(), live.eval()
    x = torch.from_numpy(np.random.default_rng(0).normal(size=(70, be2_module.N_STEPS, len(FA_FEATURES))).astype(np.float32))
    with torch.inference_mode():
        assert torch.equal(judged(x), live(x))


# ② 창 ------------------------------------------------------------------------------------------


def test_window_matches_judged_cube_on_union_axis() -> None:
    """국장 휴장일(미장만 연 날)은 국장 종목에 0 벡터 한 칸 — 판정 큐브가 그랬다."""
    from tools import trial_final_transformer as be

    session = date(2026, 7, 15)
    axis = time_axis(session)[-be2_module.WINDOW:]
    kr_days = set(trading_days(Market.KR, axis[0], session))
    assert any(d not in kr_days for d in axis), "창에 미장 단독 세션이 있어야 이 시험이 뜻이 있다"
    rng = np.random.default_rng(1)
    entities = [f"KR:{i:06d}" for i in range(55)]
    rows = []
    for d in axis:
        if d not in kr_days:
            continue
        keep = rng.random(len(entities)) < 0.9                           # 그날 빠진 종목도 있다
        f = pd.DataFrame(rng.normal(size=(int(keep.sum()), len(FA_FEATURES))).astype(np.float32), columns=list(FA_FEATURES))
        f["entity_id"] = np.array(entities)[keep]
        f["session"] = d
        rows.append(f)
    history = pd.concat(rows, ignore_index=True)
    ours = window_batch(history, axis, entities)
    cube = be.build_cube(history, list(FA_FEATURES), axis, entities)
    theirs = be.window_batch(cube, len(axis) - 1, np.arange(len(entities)))
    np.testing.assert_array_equal(ours, theirs)


# ③④ 얼린 모델 --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def frozen(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """합성 얼리기 한 번(시드 둘, 스텝 몇 개) — 모듈 안 테스트가 같이 쓴다."""
    from tools import freeze_be2

    root = tmp_path_factory.mktemp("be2root")
    rc = freeze_be2.main(["--synthetic", "--steps", "2", "--max-epochs", "1", "--threads", "2", "--seeds", "0,1",
                          "--out", str(be2_module.model_dir(root))])
    assert rc == 0
    return root


def _fa_frame(rng: np.random.Generator, entities: list[str], session: date) -> pd.DataFrame:
    f = pd.DataFrame(rng.normal(size=(len(entities), len(FA_FEATURES))).astype(np.float32), columns=list(FA_FEATURES))
    for flag in FLAGS:
        f[flag] = (rng.random(len(entities)) < 0.1).astype(np.float32)
    f["is_us"] = np.float32(0.0)
    f.insert(0, "market", "KR")
    f.insert(0, "session", session)
    f.insert(0, "entity_id", entities)
    return f


def _as_of(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 7, 0, tzinfo=UTC)      # 16:00 KST


def _seed_window(store, session: date, *, skip: date | None = None, n: int = 60) -> list[str]:  # type: ignore[no-untyped-def]
    rng = np.random.default_rng(2)
    entities = [f"KR:{i:06d}" for i in range(n)]
    axis = time_axis(session)[-be2_module.WINDOW:]
    for d in trading_days(Market.KR, axis[0], session):
        if d == skip:
            continue
        fa_features.write_session(store, _fa_frame(rng, entities, d), market="KR", session=d, as_of=_as_of(d))
    return entities


def test_scores_with_frozen_model(store, frozen: Path) -> None:  # type: ignore[no-untyped-def]
    store.seed_config_defaults()
    session = date(2026, 7, 15)
    entities = _seed_window(store, session)
    analyst = Be2Analyst(store, ReplayClock(_as_of(session)), market=Market.KR, models_root=frozen)
    signals = analyst.run(_as_of(session))
    assert analyst.skip_reason == ""
    assert len(signals) == len(entities)
    assert all(s.analyst == "be2" and s.analyst_version == be2_module.VERSION for s in signals)
    scores = pd.Series({s.entity_id: s.score for s in signals})
    assert scores.std() > 0.1 and scores.abs().max() <= 1.0
    assert {e.key for s in signals for e in s.evidence} <= {"chart", "event", "flow", "fundamental", "regime", "risk"}
    again = Be2Analyst(store, ReplayClock(_as_of(session)), market=Market.KR, models_root=frozen).run(_as_of(session))
    assert [s.score for s in again] == [s.score for s in signals]          # 결정론


def test_no_score_before_usable_from(store, frozen: Path) -> None:  # type: ignore[no-untyped-def]
    store.seed_config_defaults()
    session = date(2026, 6, 30)                                          # 합성 모델의 usable_from = 2026-07-01
    _seed_window(store, session)
    analyst = Be2Analyst(store, ReplayClock(_as_of(session)), market=Market.KR, models_root=frozen)
    assert analyst.run(_as_of(session)) == []
    assert "얼린 모델" in analyst.skip_reason


def test_no_model_means_no_score_and_ignores_ranker_models(store, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    store.seed_config_defaults()
    session = date(2026, 7, 15)
    _seed_window(store, session)
    (tmp_path / "models" / "ranker").mkdir(parents=True)
    analyst = Be2Analyst(store, ReplayClock(_as_of(session)), market=Market.KR, models_root=tmp_path)
    assert analyst.run(_as_of(session)) == []


def test_tampered_file_is_refused(frozen: Path, tmp_path: Path) -> None:
    import shutil

    copy = tmp_path / "copy"
    shutil.copytree(frozen, copy)
    folder = be2_module.model_dir(copy)
    sidecar = next(folder.glob("*.json"))
    model = Be2Model.load(sidecar)
    assert model.problems() == []
    gbm = model.gbm[0]
    gbm.write_text(gbm.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert any("지문" in p for p in Be2Model.load(sidecar).problems())
    assert be2_module.usable_be2_model(copy, as_of=_as_of(date(2026, 7, 15))) is None


def test_other_protocol_hash_is_refused(frozen: Path, tmp_path: Path) -> None:
    import shutil

    copy = tmp_path / "copy"
    shutil.copytree(frozen, copy)
    sidecar = next(be2_module.model_dir(copy).glob("*.json"))
    meta = json.loads(sidecar.read_text(encoding="utf-8"))
    meta["protocol_hash"] = "0000000000000000"
    sidecar.write_text(json.dumps(meta), encoding="utf-8")
    assert be2_module.usable_be2_model(copy, as_of=_as_of(date(2026, 7, 15))) is None


def test_hole_in_window_means_no_score(store, frozen: Path) -> None:  # type: ignore[no-untyped-def]
    store.seed_config_defaults()
    session = date(2026, 7, 15)
    hole = trading_days(Market.KR, session - timedelta(days=30), session)[3]
    _seed_window(store, session, skip=hole)
    analyst = Be2Analyst(store, ReplayClock(_as_of(session)), market=Market.KR, models_root=frozen)
    assert analyst.run(_as_of(session)) == []
    assert "0 으로 채운 창" in analyst.skip_reason


def test_future_fa_rows_are_not_read(store, frozen: Path) -> None:  # type: ignore[no-untyped-def]
    """오늘 as_of 뒤에 관측된 FA 행(다음 세션)은 창에 안 들어온다 — 점수가 같다."""
    store.seed_config_defaults()
    session = date(2026, 7, 15)
    entities = _seed_window(store, session)
    first = Be2Analyst(store, ReplayClock(_as_of(session)), market=Market.KR, models_root=frozen).run(_as_of(session))
    nxt = trading_days(Market.KR, session + timedelta(days=1), session + timedelta(days=7))[0]
    fa_features.write_session(store, _fa_frame(np.random.default_rng(9), entities, nxt), market="KR", session=nxt,
                              as_of=_as_of(nxt))
    second = Be2Analyst(store, ReplayClock(_as_of(session)), market=Market.KR, models_root=frozen).run(_as_of(session))
    assert [s.score for s in first] == [s.score for s in second]


def test_us_is_not_scored(store, frozen: Path) -> None:  # type: ignore[no-untyped-def]
    analyst = Be2Analyst(store, ReplayClock(_as_of(date(2026, 7, 15))), market=Market.US, models_root=frozen)
    assert analyst.run(_as_of(date(2026, 7, 15))) == []
    assert "국장만" in analyst.skip_reason


# ⑤ 관찰 모드 ---------------------------------------------------------------------------------------


def test_be2_is_not_in_daily_scorers_or_weekly_ic() -> None:
    from quant_rl_trading.session.signals import SCORERS
    from tools.measure_ic import ANALYSTS

    assert all("be2" not in names for names in SCORERS.values())
    assert "be2" not in ANALYSTS
