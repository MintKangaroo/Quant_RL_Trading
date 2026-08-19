"""PPO 학습 루프 — **torch 판** (M4-4-5).

## 왜 새로 쓰는가 — 카나리 판이 있는데

`modelops/canary_ppo.py` 는 같은 알고리즘을 numpy 로 손수 미분해 돌린다.
그 코드가 검증한 것은 **환경·보상·PPO 수식**이다(`canary_vec` 가 진짜
`LatticeEnv` 를 물린다). 검증하지 **못한** 것은 정책망이다:

    카나리   numpy 공유 MLP(DeepSets), hidden 64
    진짜     torch Transformer ×2, 322,440 파라미터 (`allocator/policy.py`)

오라클 기여도 68배는 앞의 것으로 낸 숫자다. 그러니 이 파일이 도는 순간까지
**torch 경로는 한 번도 학습된 적이 없다.** 짧게 돌려 기여도를 다시 재는
것이 4-5 의 첫 관문이다(§0 개정본: 오라클 기여도 ≥ 대조군 10배).

## 수식은 베낀다. 미분만 자동으로 바뀐다

GAE·클리핑·가치 클리핑·target_kl 조기종료는 `canary_ppo` 와 **같은 식**이다.
여기서 식을 다시 고안하면 합성 판과 실제 판의 차이가 알고리즘 차이인지
망 차이인지 못 가른다 — 카나리를 둔 이유가 그 구분이다.

## 절단 부트스트랩 — 0 관측을 쓰지 않는다

`LatticeEnv` 는 절단 스텝에서 `_blank()`(전부 0)을 돌려준다. 카나리는 그걸
그대로 넘겼고 `canary_vec` 독스트링이 "실제 학습에서 고칠지는 사람이 정할
일" 이라고 남겨 뒀다. **여기서는 고친다.**

이유: 250일 절단은 에피소드마다 한 번씩 **반드시** 일어난다. 그때 부트스트랩
값이 "0 관측의 가치" 가 되면, 정책은 에피소드 끝에서 가치가 뚝 떨어지는
세계를 배운다 — 실제로는 그냥 창이 끝난 것뿐인데. 감마 0.997 은 유효 지평이
300스텝이라 그 왜곡이 에피소드 뒤쪽 전체에 번진다.

대신 **절단 직전 관측**으로 가치를 매긴다. 종료(파산·낙폭 한계)는 다르다 —
그건 진짜로 끝난 것이라 부트스트랩이 0 이 맞다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt
import torch
from torch import Tensor

from quant_rl_trading.allocator.policy import AllocatorPolicy, PolicyConfig
from quant_rl_trading.allocator.reward import ReturnNormalizer
from quant_rl_trading.modelops.canary_ppo import PPOConfig

Array = npt.NDArray[np.float64]


def train_config() -> PPOConfig:
    """본 학습용 하이퍼파라미터 (`rl-training.md` §4 그대로).

    **`PPOConfig()` 기본값을 그대로 쓰지 않는다.** 그 값은 카나리용이고,
    독스트링이 밝히듯 `lr_policy` 를 §4(1e-4)의 **30배**로 올려 뒀다 — 20만
    스텝 안에 답을 내야 해서다. 그 학습률을 322,440 파라미터 Transformer 에
    쓰면 한 스텝에 정책이 멀리 간다.

    실측 2026-08-19(2 업데이트): lr 3e-3 에서 grad norm 2,273,140 ·
    approx KL 123.76(목표 0.02) · entropy -4,424. 1e-4 로 낮추면 잡힌다.

    빌려 쓰면 이런 것이 조용히 섞인다. 그래서 값을 여기에 다시 적는다.
    """
    return PPOConfig(
        total_timesteps=20_000_000,
        num_envs=32,
        n_steps=512,
        minibatch_size=2048,
        n_epochs=10,
        lr_policy=1e-4,
        lr_value=3e-4,
    )


@dataclass
class UpdateLog:
    """한 업데이트에 한 줄. 이름은 `rl-training.md` §10 표와 맞춘다.

    창고의 `rl_updates` 컬럼과도 같은 이름이다 — 화면·문서·창고가 다른 말을
    쓰면 "경고선 위/아래" 를 옮겨 적는 곳이 생긴다.
    """

    update: int
    step: int
    explained_variance: float
    approx_kl: float
    entropy: float
    grad_norm: float
    action_reflection: float
    policy_churn: float
    concentration_sum: float
    episode_reward: float
    cash_weight: float


@dataclass
class TrainResult:
    logs: list[UpdateLog] = field(default_factory=list)
    attribution: Array | None = None
    elapsed_seconds: float = 0.0

    def contribution(self, slot: int) -> float:
        """그 관측 칸을 통과한 그래디언트 기여도. 카나리 판정에 쓴다."""
        if self.attribution is None:
            return float("nan")
        return float(self.attribution[slot])


def _to_torch(obs: dict[str, Any], device: torch.device) -> dict[str, Tensor]:
    out: dict[str, Tensor] = {}
    for key, value in obs.items():
        array = np.asarray(value)
        dtype = torch.bool if array.dtype == np.bool_ else torch.float32
        out[key] = torch.as_tensor(array, dtype=dtype, device=device)
    return out


def explained_variance(predicted: Array, actual: Array) -> float:
    """1 - Var(잔차)/Var(실제). **분산이 0 이면 답이 없다** — nan 을 돌려준다.

    0 을 돌려주면 "설명 못 한다" 로 읽히는데, 실제로는 "잴 수 없다" 다.
    """
    var = float(np.var(actual))
    if var <= 0.0:
        return float("nan")
    return float(1.0 - np.var(actual - predicted) / var)


def gae(
    *,
    rewards: Array,
    values: Array,
    dones: Array,
    bootstrap: Array,
    last_value: Array,
    gamma: float,
    lam: float,
) -> tuple[Array, Array]:
    """GAE(λ). **종료·절단에서 사슬을 끊고, 절단에서만 부트스트랩한다.**

    `canary_ppo._advantages` 와 같은 식이다. 두 곳이 달라지면 카나리 결과와
    본 학습 결과를 비교할 수 없다.
    """
    n_steps = values.shape[0]
    advantages = np.zeros_like(values)
    last_gae = np.zeros(values.shape[1])
    for step in reversed(range(n_steps)):
        if step == n_steps - 1:
            next_value = np.where(dones[step] > 0, bootstrap[step], last_value)
        else:
            next_value = np.where(dones[step] > 0, bootstrap[step], values[step + 1])
        delta = rewards[step] + gamma * next_value - values[step]
        last_gae = delta + gamma * lam * (1.0 - dones[step]) * last_gae
        advantages[step] = last_gae
    return advantages, advantages + values


def collect(
    env: Any,
    policy: AllocatorPolicy,
    obs: dict[str, Any],
    *,
    ppo: PPOConfig,
    normalizer: ReturnNormalizer,
    device: torch.device,
) -> dict[str, Any]:
    """롤아웃 한 판. **그래디언트를 만들지 않는다** — 여기서 만든 그래프를
    들고 있으면 n_steps × num_envs 만큼 메모리가 쌓인다.
    """
    n_envs = env.config.n_envs
    obs_list: list[dict[str, Any]] = []
    actions: list[Array] = []
    logps: list[Array] = []
    values: list[Array] = []
    rewards: list[Array] = []
    dones: list[Array] = []
    bootstrap: list[Array] = []
    infos: list[dict[str, Any]] = []

    for _ in range(ppo.n_steps):
        with torch.no_grad():
            batch = _to_torch(obs, device)
            out = policy(batch["portfolio"], batch["assets"], batch["mask"])
            weights = out.weights_dist.sample()
            # 지연은 0 으로 고정한다 — 커리큘럼 C1~C2 다(§6). 카나리와 같은
            # 설정이라야 기여도를 비교할 수 있다.
            delay = torch.zeros(
                (n_envs, out.delay_logits.shape[1]), dtype=torch.long, device=device
            )
            logp = out.log_prob(weights, delay)

        obs_list.append(obs)
        actions.append(weights.cpu().numpy().astype(np.float64))
        logps.append(logp.cpu().numpy().astype(np.float64))
        values.append(out.value.cpu().numpy().astype(np.float64))

        obs, reward, terminated, truncated, info = env.step(actions[-1])
        done = terminated | truncated
        if ppo.normalize_returns:
            reward = np.asarray(
                normalizer(list(map(float, reward)), list(map(bool, done)))
            )

        # **절단은 종료가 아니다.** 남은 가치를 부트스트랩한다. 환경이 절단
        # 스텝에 0 관측을 주므로(`_blank`), 그 0 이 아니라 **절단 직전 관측**
        # 으로 값을 매긴다 — 모듈 독스트링 참조.
        boot = np.zeros(n_envs)
        needs = truncated & ~terminated
        if np.any(needs):
            with torch.no_grad():
                prev = _to_torch(obs_list[-1], device)
                final = policy(prev["portfolio"], prev["assets"], prev["mask"])
            boot = np.where(needs, final.value.cpu().numpy(), 0.0)

        rewards.append(np.asarray(reward, dtype=np.float64))
        dones.append(done.astype(np.float64))
        bootstrap.append(boot)
        infos.append(info)

    with torch.no_grad():
        batch = _to_torch(obs, device)
        last = policy(batch["portfolio"], batch["assets"], batch["mask"])

    return {
        "obs": obs_list,
        "actions": actions,
        "logps": logps,
        "values": np.array(values),
        "rewards": np.array(rewards),
        "dones": np.array(dones),
        "bootstrap": np.array(bootstrap),
        "last_value": last.value.cpu().numpy().astype(np.float64),
        "obs_after": obs,
        "infos": infos,
    }


def update(
    policy: AllocatorPolicy,
    optimizer: torch.optim.Optimizer,
    rollout: dict[str, Any],
    *,
    ppo: PPOConfig,
    progress: float,
    device: torch.device,
    generator: torch.Generator,
) -> UpdateLog:
    """PPO 업데이트 한 번. 식은 `canary_ppo._update` 와 같다.

    다른 것은 **미분을 torch 가 한다**는 것뿐이다. 손수 미분한 판이 참조
    구현으로 남아 있어서, 값이 갈리면 어느 쪽이 틀렸는지 물을 수 있다.
    """
    advantages, returns = gae(
        rewards=rollout["rewards"],
        values=rollout["values"],
        dones=rollout["dones"],
        bootstrap=rollout["bootstrap"],
        last_value=rollout["last_value"],
        gamma=ppo.gamma,
        lam=ppo.gae_lambda,
    )

    flat_obs = {
        key: torch.cat(
            [_to_torch(step, device)[key] for step in rollout["obs"]], dim=0
        )
        for key in rollout["obs"][0]
    }
    actions = torch.as_tensor(
        np.concatenate(rollout["actions"]), dtype=torch.float32, device=device
    )
    old_logp = torch.as_tensor(
        np.concatenate(rollout["logps"]), dtype=torch.float32, device=device
    )
    old_values = torch.as_tensor(
        rollout["values"].reshape(-1), dtype=torch.float32, device=device
    )
    adv_all = torch.as_tensor(
        advantages.reshape(-1), dtype=torch.float32, device=device
    )
    ret_all = torch.as_tensor(returns.reshape(-1), dtype=torch.float32, device=device)

    total = int(adv_all.shape[0])
    # 엔트로피 계수는 선형 감쇠다(§4). 초반에 넓게 훑고 나중에 좁힌다.
    ent_coef = ppo.ent_coef * progress
    approx_kl = entropy_mean = grad_norm = concentration_sum = 0.0
    stop = False

    for _ in range(ppo.n_epochs):
        order = torch.randperm(total, generator=generator, device=device)
        for start in range(0, total, ppo.minibatch_size):
            index = order[start : start + ppo.minibatch_size]
            out = policy(
                flat_obs["portfolio"][index],
                flat_obs["assets"][index],
                flat_obs["mask"][index],
            )
            delay = torch.zeros(
                (index.shape[0], out.delay_logits.shape[1]),
                dtype=torch.long,
                device=device,
            )
            logp = out.log_prob(actions[index], delay)

            adv = adv_all[index]
            adv = (adv - adv.mean()) / (adv.std() + 1e-8)
            log_ratio = logp - old_logp[index]
            ratio = torch.exp(log_ratio.clamp(-20.0, 20.0))

            unclipped = ratio * adv
            clipped = ratio.clamp(1.0 - ppo.clip_range, 1.0 + ppo.clip_range) * adv
            policy_loss = -torch.min(unclipped, clipped).mean()

            # 가치 손실 — 클리핑 포함 (§4 clip_range_vf)
            value_clipped = old_values[index] + (
                out.value - old_values[index]
            ).clamp(-ppo.clip_range_vf, ppo.clip_range_vf)
            plain = (out.value - ret_all[index]) ** 2
            clipped_loss = (value_clipped - ret_all[index]) ** 2
            value_loss = torch.max(plain, clipped_loss).mean()

            entropy = out.entropy().mean()
            loss = policy_loss + ppo.vf_coef * value_loss - ent_coef * entropy

            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            grad_norm = float(
                torch.nn.utils.clip_grad_norm_(policy.parameters(), ppo.max_grad_norm)
            )
            optimizer.step()

            with torch.no_grad():
                approx_kl = float(((ratio - 1.0) - log_ratio).mean())
                entropy_mean = float(entropy)
                concentration_sum = float(out.concentration.sum(dim=-1).mean())
            if approx_kl > ppo.target_kl:
                # §4: 초과하면 이 업데이트를 조기 종료한다. 학습률이 과했다는
                # 뜻이고, 더 밀면 정책이 한 번에 멀리 간다.
                stop = True
                break
        if stop:
            break

    with torch.no_grad():
        predicted = policy(
            flat_obs["portfolio"], flat_obs["assets"], flat_obs["mask"]
        ).value.cpu().numpy()
    infos = rollout["infos"]
    return UpdateLog(
        update=0,
        step=0,
        explained_variance=explained_variance(predicted, returns.reshape(-1)),
        approx_kl=approx_kl,
        entropy=entropy_mean,
        grad_norm=grad_norm,
        action_reflection=float(
            np.mean([info["action_reflection_rate"] for info in infos])
        ),
        policy_churn=float(np.mean([info["turnover"].mean() for info in infos])),
        concentration_sum=concentration_sum,
        episode_reward=float(np.mean(rollout["rewards"])),
        # 현금 비중 — 진단서 ⑦ 이 지목한 값이라 매 업데이트 남긴다.
        cash_weight=float(np.mean([a[:, -1] for a in rollout["actions"]])),
    )
