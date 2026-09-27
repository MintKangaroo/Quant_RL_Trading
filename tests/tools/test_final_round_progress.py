"""학습 진행 기록 — `trial_progress` 표와 `final_round_kit.record_progress`.

지키려는 것 셋.

1. **판정 창 지표는 이 표에 못 들어간다.** 사전등록(final-model-round-2026-10.md §과적합 억제 5)이
   "학습 중에는 판정 창을 보지 않는다" 를 요구한다. 수익·IC 를 진행 기록으로 흘리면 학습이 끝나기
   전에 사람이 그것을 읽게 되고, 그 뒤의 판정은 사전등록이 아니다.
2. **쓰기 실패가 학습을 죽이지 않는다.** 밤새 도는 학습이 진행 기록 한 줄 때문에 죽으면 하룻밤을 잃는다.
   그렇다고 **조용히 삼키지도 않는다** — 경고를 낸다.
3. **시간은 Clock 으로만.** 진행 기록도 창고 행이고, 벽시계를 직접 읽으면 리플레이가 갈린다.
"""

from __future__ import annotations

import warnings
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from quant_rl_trading.replay.clock import ReplayClock
from quant_rl_trading.store.tables import get_spec
from tools import final_round_kit as kit

NOW = datetime(2026, 10, 6, 2, 30, tzinfo=UTC)


def test_표가_등록돼_있고_필수_칸이_다_있다() -> None:
    spec = get_spec(kit.PROGRESS_TABLE)
    columns = spec.all_columns
    for name in ("entity_id", "valid_from", "observed_at", "source", "ingest_run_id"):
        assert name in columns
    for name in kit.PROGRESS_FIELDS:
        assert name in columns, f"{name} 을 record_progress 가 적는데 표에 칸이 없다"
    # entity_id 는 종목이 아니라 시행 이름이다 — 시장 접두어 규칙이 안 맞는다.
    assert spec.market_prefixed_entity is False
    # 하한 프루닝 선언이 빠지면 3일 창이 전 기간 파일을 다 연다.
    assert spec.observation_lag_days is not None


def test_판정_창_지표는_표에_칸이_아예_없다() -> None:
    """수익·IC·MDD·회전을 적을 **자리 자체가 없어야** 한다. 칸이 있으면 언젠가 채워진다."""
    columns = set(get_spec(kit.PROGRESS_TABLE).all_columns)
    for forbidden in ("ret", "annual", "ic", "mdd", "turnover", "edge", "judge_ndcg", "verdict"):
        assert forbidden not in columns


def test_블록_하나가_한_행으로_들어간다(store: Any) -> None:
    clock = ReplayClock(NOW)
    assert kit.record_progress(
        store, clock, "BF", market="KR+US", seed=1, n_seeds=5, block=3, n_blocks=41,
        step=210, rounds=3000, train_loss=-0.61, val_loss=-0.55, metric="ndcg@100(−)",
        stopped_early=True, elapsed_s=42.5, note="BF1 · 학습 1,234행",
    )
    frame = store.get(kit.PROGRESS_TABLE, as_of=NOW + timedelta(minutes=1))
    assert len(frame) == 1
    row = frame.iloc[0]
    assert row["entity_id"] == "BF" and int(row["seed"]) == 1 and int(row["block"]) == 3
    assert int(row["n_blocks"]) == 41 and int(row["n_seeds"]) == 5
    assert bool(row["stopped_early"]) is True
    assert row["valid_from"].to_pydatetime() == NOW == row["observed_at"].to_pydatetime()
    assert row["metric"] == "ndcg@100(−)"


def test_as_of_가_기록_시각보다_앞이면_안_보인다(store: Any) -> None:
    kit.record_progress(store, ReplayClock(NOW), "BE", seed=0, block=0, n_blocks=41)
    assert store.get(kit.PROGRESS_TABLE, as_of=NOW - timedelta(hours=1)).empty


def test_표에_없는_칸은_경고하고_아무것도_안_적는다(store: Any) -> None:
    """판정 창 지표를 실수로 넘기는 경로를 여기서 끊는다."""
    with pytest.warns(RuntimeWarning, match="없는 칸"):
        assert kit.record_progress(store, ReplayClock(NOW), "BE", seed=0, block=1,
                                   n_blocks=41, annual_return=0.21) is False
    assert store.get(kit.PROGRESS_TABLE, as_of=NOW + timedelta(days=1)).empty


def test_쓰기가_실패해도_학습은_안_죽는다_경고만_난다(store: Any) -> None:
    class Broken:
        def append(self, *args: Any, **kwargs: Any) -> int:
            raise RuntimeError("디스크가 꽉 찼다")

    with pytest.warns(RuntimeWarning, match="기록 실패"):
        assert kit.record_progress(Broken(), ReplayClock(NOW), "BG", seed=0, fold=1, n_folds=2) is False


def test_store_가_None_이면_아무_일도_없다() -> None:
    """합성 스모크·테스트에서 기록을 끄는 길. 경고도 내지 않는다 — 끈 것은 실패가 아니다."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert kit.record_progress(None, None, "BE", seed=0, block=0) is False


def test_같은_블록을_두_번_적어도_거부되지_않는다(store: Any) -> None:
    """append-only 창고에서 재실행은 새 행이다. 같은 초에 두 번 적어도 적재 ID 가 겹치지 않아야 한다."""
    clock = ReplayClock(NOW)
    assert kit.record_progress(store, clock, "BE", seed=0, block=0, n_blocks=41, train_loss=1.0)
    assert kit.record_progress(store, clock, "BE", seed=1, block=0, n_blocks=41, train_loss=0.9)
    assert len(store.get(kit.PROGRESS_TABLE, as_of=NOW + timedelta(days=1))) == 2
