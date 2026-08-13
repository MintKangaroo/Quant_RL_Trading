"""IC 검증기가 실제로 누수를 잡는지 증명한다.

검증기를 만들어 놓고 "잘 도네" 하고 넘어가면, 그 검증기는 아무것도 검증하지
않는 채로 통과 도장만 찍는다. 그래서 **일부러 누수를 만들어 넣고 IC 가
치솟는지** 확인한다. 안 치솟으면 검증기가 신호를 못 보고 있는 것이다.

여기서 고정하는 사실 셋:

1. 타깃을 피처에 섞으면 IC 가 1 에 가까워진다 — 누수는 이런 모양이다
2. 무작위 점수의 IC 는 0 근처다 — 검증기가 아무 데서나 알파를 보지 않는다
3. purge/embargo 를 끄면 IC 가 **올라간다** — purge 가 실제로 뭔가를 자르고 있다

3번이 핵심이다. purge 를 켜나 끄나 IC 가 같다면 purge 는 장식이다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from quant_rl_trading.analysts import ic

pytestmark = pytest.mark.invariant

ENTITIES = [f"KR:{index:06d}" for index in range(40)]
START = date(2024, 1, 1)
DAYS = 260


@pytest.fixture
def panel() -> pd.DataFrame:
    """무작위 수익률 패널. 진짜 알파는 들어 있지 않다."""
    rng = np.random.default_rng(20260811)
    sessions = [START + timedelta(days=offset) for offset in range(DAYS)]
    rows = []
    for entity in ENTITIES:
        close = 10_000.0
        for session in sessions:
            close *= 1.0 + rng.normal(0.0, 0.02)
            rows.append({"entity_id": entity, "session": session, "close": close})
    return pd.DataFrame(rows)


@pytest.fixture
def targets(panel: pd.DataFrame) -> pd.DataFrame:
    forward = ic.forward_returns(panel)
    forward["target"] = ic.cross_sectional_z(forward, "forward_return")
    return forward.dropna(subset=["target"]).loc[:, ["entity_id", "session", "target"]]


def _score(frame: pd.DataFrame, values: pd.Series) -> pd.DataFrame:
    return pd.DataFrame(
        {"entity_id": frame["entity_id"], "session": frame["session"], "score": values}
    )


def _evaluate(scores: pd.DataFrame, targets: pd.DataFrame, **kwargs) -> ic.ICResult:  # type: ignore[no-untyped-def]
    return ic.evaluate(
        scores, targets, analyst="probe", analyst_version="probe-v0",
        market="KR", threshold=0.03, min_sample_days=200, **kwargs,
    )


# -- 누수 탐지 ----------------------------------------------------------------


def test_leaked_target_produces_absurd_ic(targets) -> None:
    """타깃을 그대로 점수로 쓰면 IC 가 1 근처다.

    실전에서 IC 0.15 를 보면 축하할 게 아니라 이 모양의 실수를 찾아야 한다.
    """
    leaked = _score(targets, targets["target"])

    result = _evaluate(leaked, targets)

    assert result.ic > 0.9, f"누수를 심었는데 IC 가 {result.ic:.3f} 다 — 검증기가 눈이 멀었다"
    assert result.passed


def test_random_scores_have_no_alpha(targets) -> None:
    """무작위 점수의 IC 는 0 근처. 검증기가 아무 데서나 알파를 보면 안 된다."""
    rng = np.random.default_rng(7)
    noise = _score(targets, pd.Series(rng.normal(size=len(targets)), index=targets.index))

    result = _evaluate(noise, targets)

    assert abs(result.ic) < 0.03
    assert not result.passed, "무작위 점수가 합격하면 합격선이 의미 없다"


def test_partial_leak_is_detectable(targets) -> None:
    """타깃을 조금만 섞어도 IC 가 합격선을 훌쩍 넘는다.

    '조금 섞였을 뿐' 이 안전하다는 착각을 막는다.
    """
    rng = np.random.default_rng(11)
    noise = pd.Series(rng.normal(size=len(targets)), index=targets.index)
    tainted = _score(targets, 0.1 * targets["target"] + 0.9 * noise)

    result = _evaluate(tainted, targets)

    assert result.ic > 0.05


# -- purge / embargo 가 실제로 작동하는가 --------------------------------------


def test_purge_actually_removes_training_sessions() -> None:
    """검증 구간 주변에서 타깃이 겹치는 학습 세션이 실제로 빠진다."""
    sessions = [START + timedelta(days=offset) for offset in range(100)]

    folds = list(ic.purged_folds(sessions, n_splits=5, horizon=5, embargo=5))

    assert len(folds) == 5
    for train, test in folds:
        assert not set(train) & set(test), "학습과 검증이 겹친다"
        gap_low = test[0] - timedelta(days=10)
        gap_high = test[-1] + timedelta(days=5)
        inside = [day for day in train if gap_low < day <= gap_high]
        assert not inside, f"purge 구간에 학습 세션이 남았다: {inside[:3]}"


def test_disabling_embargo_keeps_more_training_data() -> None:
    """embargo 를 끄면 학습 표본이 늘어난다 — 끄는 스위치가 실제로 먹는다."""
    sessions = [START + timedelta(days=offset) for offset in range(100)]

    with_embargo = sum(len(train) for train, _ in ic.purged_folds(sessions, embargo=5))
    without = sum(len(train) for train, _ in ic.purged_folds(sessions, embargo=0))

    assert without > with_embargo


def test_fold_count_is_respected() -> None:
    sessions = [START + timedelta(days=offset) for offset in range(50)]

    assert len(list(ic.purged_folds(sessions, n_splits=5))) == 5
    assert len(list(ic.purged_folds(sessions, n_splits=3))) == 3


def test_too_few_sessions_is_an_error() -> None:
    """표본이 없으면 조용히 폴드를 줄이지 않고 거부한다."""
    with pytest.raises(ValueError, match="분할"):
        list(ic.purged_folds([START, START + timedelta(days=1)], n_splits=5))


# -- 합격 판정 ----------------------------------------------------------------


def test_high_ic_on_small_sample_does_not_pass(targets) -> None:
    """표본 200일 미만에서 나온 IC 는 우연과 구분되지 않는다."""
    short = targets[targets["session"] < START + timedelta(days=60)]
    leaked = _score(short, short["target"])

    result = _evaluate(leaked, short)

    assert result.ic > 0.9
    assert result.sample_days < 200
    assert not result.passed, "표본이 모자란데 통과시키면 검증이 아니다"
    assert result.weight == 0.0


def test_failing_analyst_gets_zero_weight(targets) -> None:
    rng = np.random.default_rng(3)
    noise = _score(targets, pd.Series(rng.normal(size=len(targets)), index=targets.index))

    assert _evaluate(noise, targets).weight == 0.0


def test_result_row_carries_the_verdict(targets) -> None:
    """가중치는 코드가 아니라 측정 결과에서 나온다."""
    leaked = _score(targets, targets["target"])
    row = _evaluate(leaked, targets).row(
        as_of=datetime(2026, 1, 1, tzinfo=UTC),
        observed_at=datetime(2026, 1, 1, tzinfo=UTC),
        source="ic",
    )

    assert row["entity_id"] == "probe"
    assert row["passed"] is True
    assert row["weight"] == 1.0
    assert row["ic_threshold"] == 0.03


# -- 횡단면 정의 --------------------------------------------------------------


def test_target_is_cross_sectional_not_absolute(panel) -> None:
    """시장이 통째로 오른 날의 수익은 예측력이 아니다.

    이걸 안 빼면 모든 Analyst 가 그냥 베타를 학습하고 IC 가 좋아 보인다.
    """
    forward = ic.forward_returns(panel)
    forward["target"] = ic.cross_sectional_z(forward, "forward_return")

    by_day = forward.groupby("session")["target"].mean().abs()

    assert by_day.max() < 1e-9, "횡단면 z 의 일별 평균은 0 이어야 한다"


def test_forward_return_enters_one_day_after_the_signal(panel) -> None:
    """신호는 **마감 후**에 나온다. 그날 종가에 체결할 수는 없다.

    entry_lag 를 빼면 이미 지나간 종가에 들어간 셈이 되어 하루치 공짜 미래를
    본다. IC 를 조용히 부풀리는 대표적 경로다.
    """
    one = panel[panel["entity_id"] == ENTITIES[0]].sort_values("session").reset_index(drop=True)
    forward = ic.forward_returns(panel, horizon=5)
    row = forward[forward["entity_id"] == ENTITIES[0]].sort_values("session").iloc[0]

    # 신호일 t=0 → t=1 종가에 진입 → t=6 종가에 청산
    expected = one.loc[6, "close"] / one.loc[1, "close"] - 1.0

    assert row["forward_return"] == pytest.approx(expected)


def test_entry_lag_changes_the_measured_alpha(panel) -> None:
    """지연을 0 으로 두면 값이 달라진다 — 스위치가 실제로 먹는다는 증거."""
    lagged = ic.forward_returns(panel, horizon=5, entry_lag=1)
    immediate = ic.forward_returns(panel, horizon=5, entry_lag=0)

    first_lagged = lagged.sort_values(["entity_id", "session"]).iloc[0]["forward_return"]
    first_immediate = immediate.sort_values(["entity_id", "session"]).iloc[0]["forward_return"]

    assert first_lagged != pytest.approx(first_immediate)
