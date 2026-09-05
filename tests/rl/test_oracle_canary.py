"""오라클 카나리 — `docs/design/rl-training.md §0` 이 정의한 학습 전 필수 관문.

**이 파일은 영구 보존한다.** 학습 파이프라인을 고칠 때마다 재실행한다(§0).

## 이 시험이 판정하는 것

상태값에 미래를 그대로 알려주는 가짜 피처를 심어 두고, 학습 루프가 그것을
실제로 찾아내는지 본다. 못 찾으면 **시장이 아니라 코드 문제다.**

선행 프로젝트(LS_KR·LS_USA)는 "신호가 약한 것" 과 "코드가 끊어진 것" 을
구분할 수단이 없어서 9차 재정식화까지 갔다(README). 그 수단이 이 파일이다.

## 합격 기준 (§0)

- 200k 스텝 내 explained_variance > 0.5
- 오라클 피처의 정책 그래디언트 기여도가 최상위

## 대조군이 왜 같이 있는가

오라클을 끄면 **같은 코드가 EV 0 근처에 머물러야 한다.** 이 짝이 없으면
"무엇을 넣어도 EV 가 높게 나오는 고장" 이 합격으로 찍힌다. 아무것도 하지 않는
구현도 "두 번 돌려 같다" 는 통과한다는 것과 같은 함정이다(README, M1 판정).

## 빠른 판본과 200k 판본

기본 테스트는 작은 판(12종목·14피처·hidden 32)으로 30k 스텝을 돈다. §0 이 정한
**200k 스텝·실제 관측 규격(30×28, 24)** 판본은 `slow` 마크를 달아 남겨 뒀다:

    uv run pytest tests/rl -q -m slow

작은 판이 통과하고 200k 판이 떨어지면 그건 규격에서만 나는 배선 고장이다 —
선행 프로젝트의 "obs 42 vs 모델 128" 이 그 부류다.

## 느리면 스레드를 꺼라

행렬이 작아서 BLAS 멀티스레드가 이득보다 경합을 더 만든다. 실측으로
`OMP_NUM_THREADS=1` 이 두 배 빨랐다(35초 → 17초).
"""

from __future__ import annotations

import numpy as np
import pytest

from quant_rl_trading.allocator.reward import RewardParams
from quant_rl_trading.modelops.canary_env import ORACLE_IDX, CanaryConfig, CanaryEnv
from quant_rl_trading.modelops.canary_ppo import CanaryResult, PPOConfig, train_canary

#: config/quant_rl_trading.yaml 의 reward 섹션과 같은 값. 창고를 읽지 않는다 —
#: 이 시험은 전략 데이터를 쓰지 않는다(§0). 설정에서 읽는 경로가 살아 있는지는
#: `test_reward.py::test_설정에서_읽는다_하드코딩이_아니다` 가 따로 본다.
REWARD = RewardParams(
    drawdown_free=0.12,
    drawdown_warn=0.22,
    drawdown_hard=0.30,
    w_free=0.0,
    w_mid=1.5,
    w_hot=8.0,
    terminal_penalty=-10.0,
    normalize_returns="return_std",
)

#: 빠른 판. 종목·피처·에피소드를 줄였을 뿐 학습 루프는 200k 판본과 같다.
FAST_ENV = {
    "n_assets": 12,
    "n_asset_features": 14,
    "n_portfolio_features": 12,
    "min_valid": 5,
    "episode_length": 64,
}
#: 30k 로도 세 시드 전부 통과했지만(EV 0.561 / 0.632 / 0.665) 최악 시드가
#: 합격선에 0.06 밖에 안 남는다. 그만큼은 시드 운이라 판정선으로 못 쓴다.
FAST_STEPS = 40_000

PASS_EV = 0.5


def _run(*, oracle_leak: bool, steps: int, seed: int = 0, **env_kwargs) -> CanaryResult:
    return train_canary(
        reward_params=REWARD,
        env_config=CanaryConfig(oracle_leak=oracle_leak, **env_kwargs),
        ppo=PPOConfig(total_timesteps=steps, hidden=32 if env_kwargs else 64),
        seed=seed,
    )


@pytest.fixture(scope="module")
def leaked() -> CanaryResult:
    return _run(oracle_leak=True, steps=FAST_STEPS, **FAST_ENV)


@pytest.fixture(scope="module")
def control() -> CanaryResult:
    return _run(oracle_leak=False, steps=FAST_STEPS, **FAST_ENV)


def test_오라클을_심으면_가치함수가_설명한다(leaked: CanaryResult) -> None:
    """§0 합격 기준 ①. 마지막 5업데이트 중앙값으로 잰다 — 한 번 튄 값으로
    합격을 주지 않는다."""
    final = leaked.final_explained_variance

    assert final > PASS_EV, (
        f"EV {final:.3f} — 배선이 끊겼다. 시장이 아니라 코드 문제다. "
        "rl-training.md §5 의 원인을 ①부터 순서대로 배제하고 "
        "docs/rl-diagnosis.md 에 기록해라"
    )


def test_정책이_오라클_피처를_실제로_쓴다(leaked: CanaryResult) -> None:
    """§0 합격 기준 ②. **가치함수가 아니라 정책의** 그래디언트다.

    가치는 배웠는데 액션이 안 바뀌는 고장이 실제로 일어난다 — 그때 액션
    반영률이 0 이 되고, 그게 선행 프로젝트가 죽은 방식이다(README).
    """
    attribution = leaked.attribution
    assert attribution is not None
    order = np.argsort(-attribution)

    assert int(order[0]) == ORACLE_IDX, (
        f"오라클({ORACLE_IDX}) 이 최상위가 아니다. 순위 {list(map(int, order[:3]))}"
    )
    # 잡음 피처와 겨우 앞서는 정도면 "쓴다" 고 말할 수 없다.
    assert attribution[ORACLE_IDX] > 2.0 * attribution[order[1]]


def test_학습이_실제로_올라간다(leaked: CanaryResult) -> None:
    """처음부터 높았던 EV 는 학습의 증거가 아니다. 상수 보상·죽은 어드밴티지도
    그렇게 보인다."""
    early = np.median([log.explained_variance for log in leaked.logs[:3]])
    late = np.median([log.explained_variance for log in leaked.logs[-3:]])

    assert early < 0.3
    assert late > early + 0.3


def test_오라클을_끄면_설명하지_못한다(control: CanaryResult) -> None:
    """대조군. 같은 코드·같은 스텝인데 정답만 뺐다.

    여기서도 EV 가 높게 나오면 카나리가 무엇도 판정하지 못하고 있는 것이다.
    """
    assert control.final_explained_variance < 0.3

    attribution = control.attribution
    assert attribution is not None
    # 정답이 빠진 자리는 이제 잡음이다. 최상위를 독차지할 이유가 없다.
    assert attribution[ORACLE_IDX] < 2.0 * float(np.median(attribution))


def test_기본값은_꺼져_있고_켜면_크게_경고한다() -> None:
    """실수로 실제 학습에 켜지면 성과가 통째로 가짜가 된다."""
    assert CanaryConfig().oracle_leak is False

    with pytest.warns(RuntimeWarning, match="oracle_leak=True"):
        CanaryEnv(CanaryConfig(oracle_leak=True), reward_params=REWARD, seed=0)


def test_액션_반영률이_1이다(leaked: CanaryResult) -> None:
    """합성 환경에는 체결 마찰이 없다. **배선이 온전할 때의 기준선**이고,
    진짜 환경에서 30% 밑으로 떨어지면 그건 시장이 아니라 안전장치가 액션을
    덮어쓴 것이다(§5 ②, README)."""
    assert all(log.action_reflection_rate == 1.0 for log in leaked.logs)


@pytest.mark.slow
def test_200k_스텝_실제_관측규격() -> None:
    """§0 의 원문 조건. 30×28 종목축·24 포트폴리오축으로 200k 스텝.

    수 분 걸린다. 학습 파이프라인을 고칠 때마다 이쪽을 돌린다.
    """
    result = _run(oracle_leak=True, steps=200_000)

    assert result.final_explained_variance > PASS_EV
    attribution = result.attribution
    assert attribution is not None
    assert int(np.argmax(attribution)) == ORACLE_IDX
