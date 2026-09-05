"""오라클 카나리 환경 — 합성이다. **창고를 읽지 않는다.**

`rl-training.md §0` 이 요구하는 시험대다. 상태값에 미래를 그대로 알려주는
가짜 피처를 심고, 학습 루프가 그것을 찾아내는지 본다.

## 왜 합성인가

이 시험이 판정하는 것은 **전략이 아니라 배선**이다. 실제 시세를 물리면
"신호가 약한 것" 과 "코드가 끊어진 것" 이 다시 뒤섞여서, 애초에 이 관문을
만든 이유가 사라진다. 선행 프로젝트가 9차 재정식화까지 간 이유가 그
구분 수단이 없었기 때문이다(README).

여기서는 정답을 우리가 직접 심으므로 **못 찾으면 무조건 코드 문제다.**

## 신호를 지속시킨 이유 — EV 는 예측 가능한 몫의 비율이다

explained_variance 는 `1 - Var(리턴 - 가치)/Var(리턴)` 이다. 보상이 매 스텝
독립인 잡음이면, 가치함수가 이번 스텝을 완벽히 맞혀도 남은 지평의 잡음이
분모를 채워 EV 가 0.01 근처에서 논다. 감마=0.997 이면 유효 지평이 수백 스텝이라
더 심하다.

그래서 수익의 원천을 **지속하는 잠재 상태**(AR(1), φ=0.98)로 뒀다. 이건
시험을 쉽게 만들려는 조작이 아니라, EV 라는 지표가 성립하는 조건을 갖춘
것이다 — 실제 시장에도 레짐·모멘텀처럼 지속하는 성분이 있고, 가치함수가
배울 것은 그 성분이다. **잡음만 있는 세계에서는 정상 코드도 EV 0 을 낸다.**

## 오라클을 끄면 못 배워야 한다

`oracle_leak=False` 로 같은 시험을 돌리면 EV 가 0 근처에 머물러야 한다.
이 대조군이 없으면 "무엇을 넣어도 EV 가 높게 나오는 고장" 이 합격으로
찍힌다. 아무것도 하지 않는 구현도 "두 번 돌려 같다" 는 통과한다는 것과
같은 함정이다(README, M1 판정 기준).
"""

from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt

from quant_rl_trading.allocator.reward import RewardEngine, RewardParams

Array = npt.NDArray[np.float64]

logger = logging.getLogger(__name__)

Obs = dict[str, Array]
BoolArray = npt.NDArray[np.bool_]
#: (관측, 보상, terminated, truncated, info)
StepResult = tuple[Obs, Array, BoolArray, BoolArray, dict[str, Any]]

#: 오라클이 들어가는 종목축 피처 인덱스. **0 이 아니다** — 0 번은 어떤 코드가
#: 실수로 첫 칸만 봐도 맞아떨어져서, 그 고장을 합격으로 읽는다.
ORACLE_IDX = 7

_LEAK_BANNER = (
    "\n"
    "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n"
    "!!  oracle_leak=True — 상태값에 미래 수익률이 들어 있다.           !!\n"
    "!!  이 설정으로 나온 성과는 전부 가짜다. 배선 점검 전용이다.       !!\n"
    "!!  실제 학습·백테스트·실전에서 켜져 있으면 즉시 멈춰라.           !!\n"
    "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
)


@dataclass(frozen=True)
class CanaryConfig:
    """합성 환경 설정.

    관측 모양은 `rl-training.md §1` 의 실제 규격을 그대로 쓴다(30×28, 24).
    카나리가 작은 장난감 모양이면, 실제 규격에서만 터지는 배선 고장을
    못 잡는다 — 선행 프로젝트의 "obs 42 vs 모델 128" 이 그 부류다.
    """

    n_envs: int = 16
    n_assets: int = 30
    n_asset_features: int = 28
    n_portfolio_features: int = 24
    episode_length: int = 128
    oracle_horizon: int = 5
    #: 잠재 수익 μ 의 지속성. 0.98 이면 반감기 약 34스텝.
    persistence: float = 0.98
    #: μ 의 정상상태 표준편차(일간). 0.6% 규모.
    mu_std: float = 0.006
    #: 예측 불가능한 잡음. 이것이 EV 의 상한을 정한다.
    noise_std: float = 0.004
    min_valid: int = 10
    oracle_leak: bool = False


class CanaryEnv:
    """벡터화 합성 환경. `step` 은 (E, N+1) 비중 액션을 받는다 (마지막이 현금).

    Gymnasium 인터페이스를 흉내내되 상속하지는 않는다 — 진짜 `LatticeEnv` 는
    `allocator/env.py` 에 창고를 물고 따로 만들어진다(M4-4-1). 여기서 그
    클래스를 미리 점유하면, 나중에 진짜 환경이 카나리의 형태에 맞춰 휘어진다.
    """

    def __init__(
        self,
        config: CanaryConfig,
        *,
        reward_params: RewardParams,
        seed: int = 0,
    ) -> None:
        self.config = config
        self.reward_params = reward_params
        self.rng = np.random.default_rng(seed)

        if config.oracle_leak:
            # 로그와 경고 둘 다에 남긴다. 로그만 남기면 테스트 러너에서
            # 조용히 삼켜지고, 경고만 남기면 학습 로그에 흔적이 없다.
            logger.warning(_LEAK_BANNER)
            warnings.warn(_LEAK_BANNER, RuntimeWarning, stacklevel=2)

        self._engines: list[RewardEngine] = []
        self._step_index = np.zeros(config.n_envs, dtype=np.int64)
        self._mu = np.zeros((config.n_envs, config.n_assets, 0))
        self._returns = np.zeros((config.n_envs, config.n_assets, 0))
        self._mask = np.zeros((config.n_envs, config.n_assets), dtype=bool)
        self._last_weights = np.zeros((config.n_envs, config.n_assets + 1))
        self._last_excess = np.zeros(config.n_envs)

    # -- 에피소드 생성 --------------------------------------------------------

    def _spawn(self, envs: npt.NDArray[np.int64]) -> None:
        """지정한 환경들의 에피소드를 새로 뽑는다.

        수익 경로를 **에피소드 시작에 통째로 만든다.** 오라클은 5스텝 뒤의
        실현 수익이라, 그 시점에 아직 만들어지지 않은 미래를 알려줄 수는 없다.
        """
        config = self.config
        count = envs.size
        # +1 은 마지막 관측 몫이다. 절단 시점의 obs 도 오라클을 채워야 하고,
        # 그 obs 는 episode_length 스텝에서 5칸 앞을 본다.
        total = config.episode_length + config.oracle_horizon + 1

        phi = config.persistence
        innovation = config.mu_std * np.sqrt(1.0 - phi * phi)
        mu = np.zeros((count, config.n_assets, total))
        mu[:, :, 0] = self.rng.normal(0.0, config.mu_std, (count, config.n_assets))
        for step in range(1, total):
            mu[:, :, step] = phi * mu[:, :, step - 1] + self.rng.normal(
                0.0, innovation, (count, config.n_assets)
            )
        returns = mu + self.rng.normal(0.0, config.noise_std, mu.shape)

        # 유효 후보 수는 에피소드마다 다르고, 유효한 칸은 흩어져 있다.
        # 앞쪽 n개만 유효하게 두면 "패딩은 늘 뒤" 라는 없는 규칙에 기대는
        # 마스킹 버그가 통과한다.
        mask = np.zeros((count, config.n_assets), dtype=bool)
        for row in range(count):
            n_valid = int(self.rng.integers(config.min_valid, config.n_assets + 1))
            chosen = self.rng.choice(config.n_assets, size=n_valid, replace=False)
            mask[row, chosen] = True

        if self._mu.shape[2] != total:
            shape = (config.n_envs, config.n_assets, total)
            self._mu = np.zeros(shape)
            self._returns = np.zeros(shape)

        self._mu[envs] = mu
        self._returns[envs] = returns
        self._mask[envs] = mask
        self._step_index[envs] = 0
        self._last_weights[envs] = 0.0
        self._last_excess[envs] = 0.0
        for env in envs:
            self._engines[int(env)] = RewardEngine(params=self.reward_params)

    def reset(self) -> Obs:
        self._engines = [
            RewardEngine(params=self.reward_params) for _ in range(self.config.n_envs)
        ]
        self._spawn(np.arange(self.config.n_envs, dtype=np.int64))
        return self._observe()

    # -- 관측 -----------------------------------------------------------------

    def _observe(self) -> Obs:
        config = self.config
        n_envs, n_assets = config.n_envs, config.n_assets

        assets = self.rng.standard_normal((n_envs, n_assets, config.n_asset_features))

        rows = np.arange(n_envs)
        if config.oracle_leak:
            # 5스텝 앞 실현 수익의 평균. 표준화해서 넣는다 — 실제 환경은 관측
            # 정규화를 거치므로, 여기만 0.005 규모로 두면 카나리가 정규화
            # 유무까지 같이 재게 된다.
            window = np.stack(
                [
                    self._returns[rows, :, self._step_index + offset]
                    for offset in range(1, config.oracle_horizon + 1)
                ],
                axis=-1,
            )
            oracle = window.mean(axis=-1)
            scale = np.sqrt(
                config.mu_std**2 + config.noise_std**2 / config.oracle_horizon
            )
            assets[:, :, ORACLE_IDX] = oracle / scale

        assets[~self._mask] = 0.0

        portfolio = self.rng.standard_normal((n_envs, config.n_portfolio_features))
        portfolio[:, 0] = 1.0 - self._step_index / config.episode_length
        portfolio[:, 1] = [engine.drawdown.depth for engine in self._engines]
        portfolio[:, 2] = self._last_weights[:, -1]
        portfolio[:, 3] = self._last_excess * 100.0

        return {
            "assets": assets,
            "portfolio": portfolio,
            "mask": self._mask.copy(),
        }

    # -- 진행 -----------------------------------------------------------------

    def step(self, weights: Array) -> StepResult:
        """``weights`` 는 (E, N+1). 마지막 칸이 현금이고 합은 1 이다.

        끝난 에피소드는 **자동으로 다시 뽑는다.** 그 스텝의 관측은 이미 새
        에피소드의 것이므로, 부트스트랩할 마지막 관측을 `info["final_obs"]` 로
        따로 넘긴다. 이걸 빼먹으면 250스텝 절단이 전부 "가치 0 인 종료" 로
        읽혀 가치함수가 에피소드 끝마다 통째로 틀린다.
        """
        config = self.config
        rows = np.arange(config.n_envs)
        asset_weights = weights[:, : config.n_assets] * self._mask

        realized = self._returns[rows, :, self._step_index]
        portfolio_return = np.sum(asset_weights * realized, axis=1)

        counts = self._mask.sum(axis=1)
        benchmark_return = np.sum(realized * self._mask, axis=1) / np.maximum(counts, 1)

        rewards = np.zeros(config.n_envs)
        terminated = np.zeros(config.n_envs, dtype=bool)
        depths = np.zeros(config.n_envs)
        for env in range(config.n_envs):
            breakdown = self._engines[env].step(
                portfolio_return=float(portfolio_return[env]),
                benchmark_return=float(benchmark_return[env]),
                cost=0.0,  # C0 은 비용 0 이다. 비용은 C2 에서 켠다(§6).
            )
            rewards[env] = breakdown.reward
            terminated[env] = breakdown.terminated
            depths[env] = breakdown.depth

        turnover = np.abs(weights - self._last_weights).sum(axis=1) / 2.0
        self._last_weights = weights.copy()
        self._last_excess = portfolio_return - benchmark_return

        self._step_index += 1
        truncated = self._step_index >= config.episode_length
        done = terminated | truncated

        final_obs = self._observe()
        if np.any(done):
            self._spawn(rows[done].astype(np.int64))

        info: dict[str, Any] = {
            # 합성 환경에는 체결 마찰이 없다. 목표가 곧 실현이고 반영률은 1.0 —
            # **배선이 온전할 때의 기준선**이다. 진짜 환경에서 이 값이 떨어지면
            # 그건 시장이 아니라 안전장치가 액션을 덮어쓴 것이다(README).
            "realized_weights": asset_weights,
            "target_weights": weights[:, : config.n_assets],
            "action_reflection_rate": 1.0,
            "cost": np.zeros(config.n_envs),
            "drawdown": depths,
            "turnover": turnover,
            "final_obs": final_obs,
        }
        obs = self._observe() if np.any(done) else final_obs
        return obs, rewards, terminated, truncated, info
