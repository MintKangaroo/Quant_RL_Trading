"""AI v2 R0 밴딧 — 결정론·허용 행동·미래 안 봄·창고 계약. 합성 자료다(그래서 "이긴다"는 여기서 안 잰다)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from tools.v2_bandit import (
    ARMS,
    CTX_NAMES,
    CTX_SCALE,
    FEEDBACK_LAG,
    LEARNERS,
    ContextFree,
    LinUCB,
    ThompsonLinear,
    _synthetic,
    action_row,
    dd_weight,
    index_features,
    main,
    raw_context,
    reward,
    run_bandit,
)
from tools.v2_sim import simulate

COST = 0.0041
NOW = datetime(2026, 10, 6, 6, 0, tzinfo=UTC)


def _run(kind: str, seed: int = 0, n: int = 200):  # type: ignore[no-untyped-def]
    targets, ret, bench, closes, hmm = _synthetic(seed=0, n=n)
    from tools.v2_bandit import _extra_of

    extra = _extra_of(hmm, closes)
    dim = len(CTX_NAMES)
    return run_bandit(targets, ret, bench, COST, LEARNERS[kind](dim, len(ARMS), seed=seed), extra=extra)


# --------------------------------------------------------------------------- 결정론


@pytest.mark.parametrize("kind", ["linucb", "thompson", "contextfree"])
def test_시드가_같으면_같은_행동을_낸다(kind: str) -> None:
    """실전 배선 전에 이게 안 되면 '어제 왜 0.7 이었나' 를 영원히 답할 수 없다."""
    a, _ = _run(kind, seed=1)
    b, _ = _run(kind, seed=1)
    assert a.exposure.to_numpy() == pytest.approx(b.exposure.to_numpy())


def test_톰슨은_시드가_다르면_경로가_갈린다() -> None:
    """갈리지 않으면 난수가 안 들어간 것이다 — 시드 흩어짐을 잴 수 없다(seed-noise 교훈)."""
    a, _ = _run("thompson", seed=1)
    b, _ = _run("thompson", seed=2)
    assert not np.allclose(a.exposure.to_numpy(), b.exposure.to_numpy())


def test_LinUCB_는_난수를_안_쓴다() -> None:
    learner = LinUCB(len(CTX_NAMES), len(ARMS))
    assert not hasattr(learner, "rng")


# --------------------------------------------------------------------------- 행동 집합


@pytest.mark.parametrize("kind", ["linucb", "thompson", "contextfree"])
def test_행동은_허용_집합_안에만_있다(kind: str) -> None:
    res, _ = _run(kind, seed=0)
    assert set(np.round(res.exposure.unique(), 6)) <= set(np.round(ARMS, 6))
    assert res.exposure.min() >= 0.30 and res.exposure.max() <= 1.0


def test_재조정은_규칙이_정한다() -> None:
    """행동은 노출 하나다 — 재조정 주기(10세션)는 밴딧이 못 건드린다."""
    res, _ = _run("linucb")
    assert list(np.flatnonzero(res.rebalanced.to_numpy())) == list(range(0, len(res.rebalanced), 10))


# --------------------------------------------------------------------------- 미래를 안 본다


def test_t_의_선택은_지연을_통과한_보상만_배운다() -> None:
    """시뮬 하루 수익은 t+1→t+2 구간이다. t 의 선택이 t-1 의 보상을 배웠다면 미래를 본 것이다."""
    _, runner = _run("linucb")
    assert runner.log, "선택 기록이 비었다"
    for rec in runner.log:
        assert rec.trained_through <= max(-1, rec.step - FEEDBACK_LAG)
    assert runner.log[-1].trained_through == len(runner.log) - 1 - FEEDBACK_LAG, "지연만큼만 늦게 배워야 한다"


def test_문맥은_그날_전_종가까지만_본다() -> None:
    closes = pd.Series(np.linspace(100, 200, 60), index=pd.bdate_range("2025-01-01", periods=60).date)
    day = closes.index[40]
    before = index_features(closes, day)
    tampered = closes.copy()
    tampered.iloc[40:] = 1.0        # 그날 이후를 망쳐도 문맥이 바뀌면 미래를 본 것이다
    assert index_features(tampered, day) == pytest.approx(before)


def test_문맥에_원값_칸이_없고_전부_O1_이다() -> None:
    """2회차 r5 를 죽인 것은 환율 원값 1,478 이었다 — 칸 이름과 스케일을 테스트가 지킨다."""
    assert CTX_NAMES == ("bias", "p0", "p1", "p2", "ret20", "vol20", "index_dd", "prev_k")
    assert len(CTX_SCALE) == len(CTX_NAMES)
    worst = raw_context(np.array([1.0, 0.0, 0.0]), ret20=-0.30, vol20=0.60, index_dd=-0.40, prev_k=1.0)
    assert np.abs(worst).max() <= 3.0, "극단 구간에서도 문맥은 O(1) 이어야 한다"


def test_문맥_없는_밴딧은_문맥을_무시한다() -> None:
    """대조가 진짜 대조인가 — 같은 학습 이력이면 문맥이 달라도 같은 팔을 골라야 한다."""
    learner = ContextFree(len(CTX_NAMES), len(ARMS))
    learner.update(2, raw_context(np.array([0.1, 0.2, 0.7]), 0.05, 0.10, 0.0, 1.0), 0.01)
    calm = raw_context(np.array([0.1, 0.2, 0.7]), 0.05, 0.10, 0.0, 1.0)
    panic = raw_context(np.array([0.9, 0.1, 0.0]), -0.20, 0.50, -0.30, 0.5)
    assert learner.choose(calm) == learner.choose(panic)


def test_전환_비용이_노출_왕복을_줄인다() -> None:
    """비용을 모르는 밴딧은 잡음만큼 매일 노출을 왕복시킨다(스모크 회전 9배). 바꾸는 값은 아는 값이라 점수에서 먼저 뺀다."""
    targets, ret, bench, closes, hmm = _synthetic(n=300)
    from tools.v2_bandit import _extra_of

    extra = _extra_of(hmm, closes)
    free = run_bandit(targets, ret, bench, 0.0, LinUCB(len(CTX_NAMES), len(ARMS)), extra=extra)[0]
    paid = run_bandit(targets, ret, bench, COST, LinUCB(len(CTX_NAMES), len(ARMS)), extra=extra)[0]
    switches = lambda res: int((res.exposure.diff().abs() > 1e-12).sum())  # noqa: E731
    assert switches(paid) < switches(free)


# --------------------------------------------------------------------------- 보상


def test_보상은_초과수익에서_새_낙폭만_벌한다() -> None:
    assert reward(0.01, 0.004, 0.05, 0.05) == pytest.approx(0.006)          # 신저점 아님 → 벌점 0
    assert dd_weight(0.05) == 0.0 and dd_weight(0.15) == 1.5 and dd_weight(0.25) == 8.0 and dd_weight(0.40) == 8.0
    # 22% 를 넘어 1%p 더 깊어지면 초과수익 8%p 만큼 아프다(reward-and-risk.md §2)
    assert reward(0.0, 0.0, 0.22, 0.23) == pytest.approx(-0.08)
    # 자유구간(12% 미만)에서는 깊어져도 벌점이 없다 — 작은 낙폭은 정상 영업이다
    assert reward(0.0, 0.0, 0.02, 0.06) == pytest.approx(0.0)


def test_비용은_한_번만_빠진다() -> None:
    """보상은 비용을 다시 빼지 않는다 — 시뮬 수익에 이미 들어 있다(이중차감하면 정책이 '덜 행동하기' 를 배운다)."""
    targets, ret, bench, _, _ = _synthetic(n=60)
    gross = simulate(targets, ret, bench, 0.0, lambda s: (1.0, s.step % 10 == 0))
    net = simulate(targets, ret, bench, COST, lambda s: (1.0, s.step % 10 == 0))
    paid = float((gross.daily - net.daily).sum())
    assert paid == pytest.approx(COST * net.turnover / 252 * len(net.daily), rel=1e-6)


# --------------------------------------------------------------------------- 창고 계약(실전 경로)


class _ActionStore:
    """`exposure_actions` 한 행만 돌려주는 창고 — learned_decision 이 읽는 모양인지 본다."""

    def __init__(self, row: dict) -> None:
        self._row = row

    def get(self, table: str, **kwargs: object) -> pd.DataFrame:
        return pd.DataFrame([self._row])


def test_행동_행은_learned_decision_이_읽는_모양이다() -> None:
    from quant_rl_trading.selector.exposure import learned_decision

    row = action_row("KR", NOW, 0.85, {"arm": 2})
    decision = learned_decision(_ActionStore(row), as_of=NOW, market="KR", source="bandit-v1")  # type: ignore[arg-type]
    assert decision is not None
    assert decision.scale == pytest.approx(0.85)
    assert decision.driver == "learned:bandit-v1"


def test_낡은_행동은_안_쓰인다() -> None:
    """부품이 죽은 날 어제 배수로 오늘을 돌면 비교가 오염된다(2026-09-26 감사)."""
    from quant_rl_trading.selector.exposure import learned_decision

    row = action_row("KR", NOW - timedelta(days=3), 0.5, {})
    assert learned_decision(_ActionStore(row), as_of=NOW, market="KR", source="bandit-v1") is None  # type: ignore[arg-type]


def test_행동_행은_하한과_상한을_지킨다() -> None:
    assert action_row("KR", NOW, 2.0, {})["scale"] == pytest.approx(1.0)
    assert action_row("KR", NOW, 0.0, {})["scale"] == pytest.approx(0.30)


# --------------------------------------------------------------------------- 관문(사전등록 전에는 결과를 안 본다)


@pytest.mark.parametrize("cmd", ["judge", "canary", "train"])
def test_플래그_없이는_판정_경로가_안_돈다(cmd: str) -> None:
    with pytest.raises(SystemExit, match="사전등록"):
        main([cmd])


def test_등록_문서가_초안이면_플래그가_있어도_안_돈다() -> None:
    with pytest.raises(SystemExit, match="초안"):
        main(["judge", "--i-registered"])


def test_행동_적기도_관문_뒤다() -> None:
    with pytest.raises(SystemExit, match=r"사전등록|초안"):
        main(["act", "--market", "KR"])


def test_스모크는_판정_경로를_안_탄다() -> None:
    """합성 자료 스모크는 아무 때나 돌아야 한다 — 창고도 등록 문서도 안 본다."""
    assert main(["smoke", "--seed", "0"]) == 0


def test_톰슨과_LinUCB_는_같은_문맥_차원을_쓴다() -> None:
    a, b = LinUCB(len(CTX_NAMES), len(ARMS)), ThompsonLinear(len(CTX_NAMES), len(ARMS))
    assert a.A.shape == b.A.shape == (len(ARMS), len(CTX_NAMES), len(CTX_NAMES))
