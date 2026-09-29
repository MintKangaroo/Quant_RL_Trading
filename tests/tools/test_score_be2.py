"""BE2 매일 경로(tools/score_be2) — 점수가 빈 세션은 적지 않고, 얼린 모델로 be2 신호를 한 번만 적는다."""
from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pytest

from quant_rl_trading.analysts import be2 as be2_module
from quant_rl_trading.analysts import fa_features
from quant_rl_trading.collectors.market_hours import Market, trading_days
from quant_rl_trading.session.signals import SIGNALS, run_id_for
from tools import score_be2

pytest.importorskip("torch")
SESSION = date(2026, 7, 15)


def _as_of(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 7, 0, tzinfo=UTC)


def test_missing_base_scores_writes_nothing(store) -> None:  # type: ignore[no-untyped-def]
    store.seed_config_defaults()
    rc = score_be2.score_session(store, Market.KR, SESSION, _as_of(SESSION), features_only=False, write_signals=True)
    assert rc == score_be2.RC_NO_FEATURES
    assert not store.ingest_run_recorded(fa_features.TABLE, fa_features.run_id("KR", SESSION))


def test_scores_once_with_frozen_model(store, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    import torch

    from tests.analysts.test_be2 import _fa_frame
    from tools import freeze_be2

    torch.set_num_threads(2)
    monkeypatch.setenv("QUANT_RL_DATA_ROOT", str(store.root))              # 기본 창고로 물러서도 실데이터를 안 본다
    store.seed_config_defaults()
    assert freeze_be2.main(["--synthetic", "--steps", "2", "--max-epochs", "1", "--threads", "2", "--seeds", "0",
                            "--out", str(be2_module.model_dir(store.root))]) == 0
    rng = np.random.default_rng(4)
    entities = [f"KR:{i:06d}" for i in range(60)]
    axis = be2_module.time_axis(SESSION)[-be2_module.WINDOW:]
    for d in trading_days(Market.KR, axis[0], SESSION):
        fa_features.write_session(store, _fa_frame(rng, entities, d), market="KR", session=d, as_of=_as_of(d))
    rc = score_be2.score_session(store, Market.KR, SESSION, _as_of(SESSION), features_only=False, write_signals=True)
    assert rc == score_be2.RC_OK
    run_id = run_id_for(SIGNALS, Market.KR, _as_of(SESSION), "be2")
    assert store.ingest_run_recorded(SIGNALS, run_id)
    rows = store.get(SIGNALS, as_of=_as_of(SESSION), lookback=2, market="KR")
    rows = rows[rows["analyst"] == "be2"]
    assert len(rows) == len(entities) and (rows["observed_at"] == _as_of(SESSION)).all()
    again = score_be2.score_session(store, Market.KR, SESSION, _as_of(SESSION), features_only=False, write_signals=True)
    assert again == score_be2.RC_OK                                            # 이미 있다 — 다시 안 쓴다
    # 피처만(금고 창 굽기)이면 신호를 안 본다.
    assert score_be2.score_session(store, Market.KR, SESSION, _as_of(SESSION), features_only=True,
                                   write_signals=True) == score_be2.RC_OK
