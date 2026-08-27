"""카나리용 PPO — numpy. `rl-training.md §4` 의 하이퍼파라미터를 그대로 쓴다.

## 이것은 진짜 학습 루프가 아니다

진짜 루프는 `allocator/train.py` 에 torch 로 만들어진다(M4-4-5). 여기 있는
것은 **오라클 카나리를 돌리기 위한 최소 PPO** 이고, 판정에 필요한 배선만
같게 맞췄다.

- Dict 관측(assets/portfolio/mask)을 다루는 롤아웃 버퍼
- 절단(truncation)에서 마지막 관측으로 부트스트랩
- **리턴 정규화** — §3 이 explained_variance 0 의 1순위 처방으로 지목한 것
- target_kl 초과 시 업데이트 조기 종료
- 절단된 목적함수·가치 클리핑·엔트로피 보너스·전역 노름 클리핑

이 다섯 중 하나라도 빠지면 카나리가 통과해도 의미가 없다. 진짜 루프가 이
목록을 지키는지는 그쪽 테스트가 따로 본다.

## 절단 부트스트랩을 빼면 무슨 일이 일어나는가

에피소드가 250스텝에서 잘릴 때 남은 가치를 0 으로 두면, 가치함수는 매
에피소드 끝에서 통째로 틀린 목표를 배운다. 그 오차가 EV 를 갉아먹고,
증상은 "학습이 안 된다" 로만 보인다. §5 의 원인 목록에 없는 종류의 고장이라
더 오래 걸린다.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt

from quant_rl_trading.allocator.reward import ReturnNormalizer, RewardParams
from quant_rl_trading.modelops import canary_policy as dirichlet
from quant_rl_trading.modelops.canary_env import CanaryConfig, CanaryEnv
from quant_rl_trading.modelops.canary_policy import CanaryPolicy
from quant_rl_trading.modelops.diagnostics import explained_variance, feature_attribution
from quant_rl_trading.modelops.numeric import Adam, clip_grads

Array = npt.NDArray[np.float64]


@dataclass(frozen=True)
class PPOConfig:
    """§4 의 출발점. **감마를 낮추지 않는다** — 0.99 면 유효 지평이 100스텝이라
    250일 낙폭을 보지 못한다.

    ``lr_policy`` 만 §4(1e-4)보다 크게 잡는다. 1e-4 는 2,000만 스텝짜리 본
    학습의 값이고, 카나리는 20만 스텝 안에 답을 내야 한다 — **여기서 학습률이
    모자라 못 배우면 배선이 아니라 예산을 재는 시험이 된다.** 비율(가치 쪽이
    2~3배)은 §4 그대로 지킨다.
    """

    total_timesteps: int = 200_000
    num_envs: int = 16
    n_steps: int = 128
    minibatch_size: int = 512
    n_epochs: int = 10
    gamma: float = 0.997
    gae_lambda: float = 0.95
    clip_range: float = 0.2
    clip_range_vf: float = 0.2
    ent_coef: float = 3e-3
    vf_coef: float = 0.5
    max_grad_norm: float = 0.5
    lr_policy: float = 3e-3
    lr_value: float = 9e-3
    target_kl: float = 0.02
    normalize_returns: bool = True
    hidden: int = 64


@dataclass
class IterationLog:
    """§10 표의 지표. 한 업데이트에 한 줄."""

    step: int
    explained_variance: float
    approx_kl: float
    entropy: float
    grad_norm: float
    reward_mean: float
    drawdown_mean: float
    concentration_sum: float
    action_reflection_rate: float
    turnover: float


@dataclass
class CanaryResult:
    logs: list[IterationLog] = field(default_factory=list)
    attribution: Array | None = None
    elapsed_seconds: float = 0.0

    @property
    def best_explained_variance(self) -> float:
        return max((log.explained_variance for log in self.logs), default=float("nan"))

    @property
    def final_explained_variance(self) -> float:
        """마지막 **5 업데이트의 중앙값**. 한 번 튄 값으로 합격을 주지 않는다."""
        tail = [log.explained_variance for log in self.logs[-5:]]
        return float(np.median(tail)) if tail else float("nan")


def _stack(obs_list: list[dict[str, Array]]) -> dict[str, Array]:
    return {key: np.concatenate([obs[key] for obs in obs_list]) for key in obs_list[0]}


def train_canary(
    *,
    reward_params: RewardParams,
    env_config: CanaryConfig | None = None,
    ppo: PPOConfig | None = None,
    seed: int = 0,
    env: Any = None,
    on_iteration: Callable[[IterationLog], None] | None = None,
) -> CanaryResult:
    """오라클 카나리 학습을 끝까지 돌리고 지표를 돌려준다.

    ``env`` 를 주면 그것을 그대로 쓴다 — 실제 환경(`canary_vec.VecLatticeEnv`)
    을 물리는 자리다. **루프를 두 벌로 만들지 않기 위한 구멍이다**: 합성 판과
    실제 판이 다른 루프를 돌면, 성적이 갈렸을 때 그것이 환경 차이인지 루프
    차이인지 못 가른다 — 그 구분이 이 시험의 존재 이유다(§0).

    ``on_iteration`` 은 진행 보고용이다. 실제 환경 판은 한 시간 단위로 도는데
    끝날 때까지 아무것도 안 찍으면 살았는지 죽었는지 알 수 없다.
    """
    ppo = ppo or PPOConfig()
    started = time.perf_counter()
    if env is None:
        env = CanaryEnv(
            env_config or CanaryConfig(), reward_params=reward_params, seed=seed
        )
    config = env.config
    if config.n_envs != ppo.num_envs:
        # 스텝 수를 `ppo.num_envs` 로 세고 롤아웃은 `env.config.n_envs` 로 돈다.
        # 둘이 갈리면 "200k 스텝 돌렸다" 가 거짓말이 되고, 로그에는 안 보인다.
        raise ValueError(
            f"환경 {config.n_envs}개 · PPO num_envs {ppo.num_envs} — 스텝 계산이 어긋난다"
        )
    policy = CanaryPolicy(
        n_asset_features=config.n_asset_features,
        n_portfolio_features=config.n_portfolio_features,
        hidden=ppo.hidden,
        seed=seed + 1,
    )
    rng = np.random.default_rng(seed + 2)
    lr = {"default": ppo.lr_policy}
    for name in ("wv1", "bv1", "wv2", "bv2"):
        lr[name] = ppo.lr_value
    optimizer = Adam(policy.params, lr=lr)
    normalizer = ReturnNormalizer(gamma=ppo.gamma, num_envs=config.n_envs)

    obs = env.reset()
    batch = ppo.num_envs * ppo.n_steps
    iterations = max(1, ppo.total_timesteps // batch)
    result = CanaryResult()

    for iteration in range(iterations):
        progress = 1.0 - iteration / iterations  # 선형 감쇠 (§4)
        rollout = _collect(env, policy, rng, obs, normalizer=normalizer, ppo=ppo)
        obs = rollout["obs_after"]
        log = _update(
            policy, optimizer, rollout, ppo=ppo, progress=progress, rng=rng
        )
        log.step = (iteration + 1) * batch
        result.logs.append(log)
        if on_iteration is not None:
            on_iteration(log)

    result.attribution = _attribution(policy, rollout, ppo=ppo)
    result.elapsed_seconds = time.perf_counter() - started
    return result


def _collect(
    env: Any,
    policy: CanaryPolicy,
    rng: np.random.Generator,
    obs: dict[str, Array],
    *,
    normalizer: ReturnNormalizer,
    ppo: PPOConfig,
) -> dict[str, Any]:
    n_envs = env.config.n_envs
    obs_list: list[dict[str, Array]] = []
    actions: list[Array] = []
    logps: list[Array] = []
    values: list[Array] = []
    rewards: list[Array] = []
    dones: list[Array] = []
    next_values: list[Array] = []
    infos: list[dict[str, Any]] = []

    for _ in range(ppo.n_steps):
        out = policy.forward(obs)
        valid = np.concatenate(
            [obs["mask"], np.ones((n_envs, 1), dtype=bool)], axis=1
        )
        weights = dirichlet.sample(rng, out.concentration, valid)
        logp = dirichlet.log_prob(out.concentration, weights, valid)

        obs_list.append(obs)
        actions.append(weights)
        logps.append(logp)
        values.append(out.value)

        obs, reward, terminated, truncated, info = env.step(weights)
        done = terminated | truncated
        if ppo.normalize_returns:
            reward = np.asarray(
                normalizer(list(map(float, reward)), list(map(bool, done)))
            )

        # 절단은 종료가 아니다. 남은 가치를 부트스트랩한다.
        bootstrap = np.zeros(n_envs)
        needs = truncated & ~terminated
        if np.any(needs):
            final = policy.forward(info["final_obs"])
            bootstrap = np.where(needs, final.value, 0.0)

        rewards.append(reward)
        dones.append(done.astype(np.float64))
        next_values.append(bootstrap)
        infos.append(info)

    last_value = policy.forward(obs).value
    return {
        "obs": obs_list,
        "actions": actions,
        "logps": logps,
        "values": values,
        "rewards": rewards,
        "dones": dones,
        "bootstrap": next_values,
        "last_value": last_value,
        "obs_after": obs,
        "infos": infos,
    }


def _advantages(rollout: dict[str, Any], *, ppo: PPOConfig) -> tuple[Array, Array]:
    """GAE(λ). 종료·절단에서 사슬을 끊고, 절단에서만 부트스트랩한다."""
    values = np.array(rollout["values"])
    rewards = np.array(rollout["rewards"])
    dones = np.array(rollout["dones"])
    bootstrap = np.array(rollout["bootstrap"])
    n_steps = values.shape[0]

    advantages = np.zeros_like(values)
    last_gae = np.zeros(values.shape[1])
    for step in reversed(range(n_steps)):
        if step == n_steps - 1:
            next_value = np.where(dones[step] > 0, bootstrap[step], rollout["last_value"])
        else:
            next_value = np.where(dones[step] > 0, bootstrap[step], values[step + 1])
        delta = rewards[step] + ppo.gamma * next_value - values[step]
        last_gae = delta + ppo.gamma * ppo.gae_lambda * (1.0 - dones[step]) * last_gae
        advantages[step] = last_gae
    return advantages, advantages + values


def _update(
    policy: CanaryPolicy,
    optimizer: Adam,
    rollout: dict[str, Any],
    *,
    ppo: PPOConfig,
    progress: float,
    rng: np.random.Generator,
) -> IterationLog:
    advantages, returns = _advantages(rollout, ppo=ppo)
    obs = _stack(rollout["obs"])
    actions = np.concatenate(rollout["actions"])
    old_logp = np.concatenate(rollout["logps"])
    old_values = np.concatenate(rollout["values"])
    advantages_flat = advantages.reshape(-1)
    returns_flat = returns.reshape(-1)
    valid = np.concatenate(
        [obs["mask"], np.ones((obs["mask"].shape[0], 1), dtype=bool)], axis=1
    )

    total = advantages_flat.size
    ent_coef = ppo.ent_coef * progress
    approx_kl = 0.0
    entropy_mean = 0.0
    grad_norm = 0.0
    concentration_sum = 0.0
    stop = False

    for _ in range(ppo.n_epochs):
        order = rng.permutation(total)
        for start in range(0, total, ppo.minibatch_size):
            index = order[start : start + ppo.minibatch_size]
            size = index.size
            batch_obs = {key: value[index] for key, value in obs.items()}
            out = policy.forward(batch_obs)
            logp = dirichlet.log_prob(out.concentration, actions[index], valid[index])
            entropies = dirichlet.entropy(out.concentration, valid[index])

            adv = advantages_flat[index]
            adv = (adv - adv.mean()) / (adv.std() + 1e-8)
            log_ratio = logp - old_logp[index]
            ratio = np.exp(np.clip(log_ratio, -20.0, 20.0))

            unclipped = ratio * adv
            clipped = np.clip(ratio, 1.0 - ppo.clip_range, 1.0 + ppo.clip_range) * adv
            # min 이 고른 가지로만 그래디언트가 흐른다. 잘린 가지는 상수다.
            take = unclipped <= clipped
            d_logp = -np.where(take, adv * ratio, 0.0) / size

            # 가치 손실 — 클리핑 포함 (§4 clip_range_vf)
            value_clipped = old_values[index] + np.clip(
                out.value - old_values[index], -ppo.clip_range_vf, ppo.clip_range_vf
            )
            error = out.value - returns_flat[index]
            error_clipped = value_clipped - returns_flat[index]
            use_plain = error**2 >= error_clipped**2
            d_value = (
                ppo.vf_coef
                * np.where(
                    use_plain,
                    error,
                    error_clipped
                    * (
                        np.abs(out.value - old_values[index]) <= ppo.clip_range_vf
                    ).astype(np.float64),
                )
                / size
            )

            d_concentration = (
                d_logp[:, None]
                * dirichlet.log_prob_grad(out.concentration, actions[index], valid[index])
                - ent_coef * dirichlet.entropy_grad(out.concentration, valid[index]) / size
            )
            # 정책·가치를 **따로** 자른다 — `allocator/train.step_separately` 와
            # 같은 규칙. 합쳐 자르면 노름이 큰 쪽이 예산을 독식한다(§4).
            grads, _ = policy.backward(
                out, d_concentration=d_concentration, d_value=np.zeros_like(d_value)
            )
            grad_norm = clip_grads(grads, max_norm=ppo.max_grad_norm)
            value_grads, _ = policy.backward(
                out, d_concentration=np.zeros_like(d_concentration), d_value=d_value
            )
            clip_grads(value_grads, max_norm=ppo.max_grad_norm)
            for name in grads:
                grads[name] = grads[name] + value_grads[name]
            optimizer.step(grads, lr_scale=progress)

            approx_kl = float(np.mean((ratio - 1.0) - log_ratio))
            entropy_mean = float(np.mean(entropies))
            concentration_sum = float(
                np.mean(np.where(valid[index], out.concentration, 0.0).sum(axis=1))
            )
            if approx_kl > ppo.target_kl:
                # §4: 초과 시 이 업데이트를 조기 종료한다.
                stop = True
                break
        if stop:
            break

    predicted = policy.forward(obs).value
    infos = rollout["infos"]
    return IterationLog(
        step=0,
        explained_variance=explained_variance(predicted, returns_flat),
        approx_kl=approx_kl,
        entropy=entropy_mean,
        grad_norm=grad_norm,
        reward_mean=float(np.mean(rollout["rewards"])),
        drawdown_mean=float(np.mean([info["drawdown"].mean() for info in infos])),
        concentration_sum=concentration_sum,
        action_reflection_rate=float(
            np.mean([info["action_reflection_rate"] for info in infos])
        ),
        turnover=float(np.mean([info["turnover"].mean() for info in infos])),
    )


def _attribution(policy: CanaryPolicy, rollout: dict[str, Any], *, ppo: PPOConfig) -> Array:
    """마지막 롤아웃에서 **정책 손실의 입력 미분**을 잰다.

    가치 헤드는 끊는다(`d_value=0`) — §0 이 요구하는 것은 *정책* 그래디언트
    기여도다. 가치까지 섞으면 "가치함수만 오라클을 보고 정책은 못 보는" 고장이
    합격으로 찍힌다. 그건 실제로 일어나는 고장이다 — 가치는 배웠는데 액션이
    안 바뀌면 액션 반영률이 0 이 된다(README, 선행 프로젝트).
    """
    advantages, _ = _advantages(rollout, ppo=ppo)
    obs = _stack(rollout["obs"])
    actions = np.concatenate(rollout["actions"])
    valid = np.concatenate(
        [obs["mask"], np.ones((obs["mask"].shape[0], 1), dtype=bool)], axis=1
    )
    adv = advantages.reshape(-1)
    adv = (adv - adv.mean()) / (adv.std() + 1e-8)

    out = policy.forward(obs)
    size = adv.size
    d_logp = -adv / size
    d_concentration = d_logp[:, None] * dirichlet.log_prob_grad(
        out.concentration, actions, valid
    )
    _, input_grad = policy.backward(
        out,
        d_concentration=d_concentration,
        d_value=np.zeros(size),
        want_input_grad=True,
    )
    assert input_grad is not None
    return feature_attribution(input_grad, obs["mask"].astype(bool))
