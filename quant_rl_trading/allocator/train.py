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

    빌려 쓰면 이런 것이 조용히 섞인다. 그래서 값을 여기에 다시 적는다.

    ## 학습률 3e-5 — 2026-08-27 재보정 (분리 클리핑 + 관측 스케일 수정 후)

    처음 표(2026-08-25)는 1e-5 를 골랐다: 1e-4 가 한 스텝에 KL 3.29 로 발산했다.
    그 발산은 학습률 탓이 아니었다 — 환율 원값(1,478)이 관측에 그대로 들어가
    가치 쪽 그래디언트를 1,000 대로 키웠고, 전역 클리핑이 그 합을 0.5 로 자르며
    정책 몫은 3e-9 로 굶었다(`rl-training.md §4`). 둘을 고치고 다시 쟀다
    (초기 정책 · 8env×256스텝 · 정책/가치 자르기 전 노름 2.5/15):

        lr      1스텝 KL   1스텝 배분L1   3스텝 KL   3스텝 L1
        1e-4    0.00272     0.0017       0.01059    0.0040   <- 3스텝에 목표선 절반, 조기종료 잦다
        3e-5    0.00025     0.0005       0.00187    0.0015   <- 채택
        1e-5    0.00003     0.0002       0.00024    0.0005
        3e-6    0.00000     0.0001       0.00002    0.0002

    한 업데이트는 최대 80 미니배치 스텝이다. 3e-5 는 한두 에폭을 돌고 target_kl
    0.02 에 닿는 규모라 신뢰영역을 실제로 쓴다. 1e-5 는 이제 신뢰영역의 1/100
    에서 노는 값이다 — 2회차가 그 자리에서 340업데이트를 헛돌았다.

    **`target_kl` 은 손대지 않는다.** 0.02 는 이 행동공간에서 "한 업데이트에
    1% 안팎 재배분" 이고, 그건 합리적인 신뢰영역이다.

    가치 쪽 배수(3배)는 §4 그대로 지킨다.
    """
    return PPOConfig(
        total_timesteps=20_000_000,
        num_envs=32,
        n_steps=512,
        minibatch_size=2048,
        n_epochs=10,
        lr_policy=3e-5,
        lr_value=1e-4,
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
    #: 가치 손실 쪽 자르기 전 노름. 창고 컬럼은 아니고 콘솔·회고용이다 —
    #: 정책 쪽(`grad_norm`)과 나란히 놓아야 누가 예산을 먹는지 보인다.
    value_grad_norm: float = 0.0


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


#: **지연 행동을 뽑지 않는다** — 커리큘럼 C1~C2 는 지연을 0 으로 고정한다(§6).
#:
#: 고정한 항은 **로그확률에서도 빼야 한다.** 안 빼면 정책이 `delay=0` 의
#: 확률을 올리는 것만으로 손실을 움직일 수 있는데, 지연은 어차피 항상 0 이라
#: **행동은 하나도 안 바뀐다.** PPO 입장에서는 공짜 지렛대다 — 비중을 바꾸려면
#: 실제로 다른 종목을 사야 하지만 지연 확률을 미는 데는 대가가 없다.
#:
#: 실측 2026-08-21 에 최적화기가 그 쉬운 길을 찾아갔다:
#:
#:     지연 머리 그래디언트   비중의 55배 (6.1e+02 vs 1.1e+01)
#:     정답 기여도(지연)      3위 / 28
#:     정답 기여도(비중)     26위 / 28
#:     정답을 밀었을 때 비중  반응 없음 (-2e-05)
#:     NAV                    네 판 모두 부호가 뒤집히는 잡음
#:
#: 배선이 아니라 손실 함수가 정책에게 **행동을 안 바꿔도 되는 출구**를 열어
#: 준 것이었다.
DELAY_FIXED = True


def _fixed_delay(out: Any, rows: int, device: torch.device) -> torch.Tensor:
    """고정 지연 행동. 실제로 뽑지 않으므로 0 이다."""
    return torch.zeros(
        (rows, out.delay_logits.shape[1]), dtype=torch.long, device=device
    )


def _log_prob(out: Any, weights: torch.Tensor, delay: torch.Tensor) -> torch.Tensor:
    """정책 로그확률. **세 곳(collect·update·attribution)이 같은 규칙을 쓴다.**

    하나만 어긋나면 PPO 비율 `exp(logp - old_logp)` 가 서로 다른 정의를
    비교하게 되어 조용히 망가진다 — 그래서 헬퍼 하나로 묶는다.
    """
    if DELAY_FIXED:
        return out.weights_log_prob(weights)
    return out.log_prob(weights, delay)


def _entropy(out: Any) -> torch.Tensor:
    """정책 엔트로피. 고정된 머리의 엔트로피는 세지 않는다 — 그 보너스도
    행동을 안 바꾸면서 손실만 움직인다."""
    if DELAY_FIXED:
        return out.weights_entropy()
    return out.entropy()


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
            # 지연은 0 으로 고정한다 — 커리큘럼 C1~C2 다(§6). **고정한 항은
            # 로그확률에서도 뺀다** (`DELAY_FIXED` 주석 참조).
            delay = _fixed_delay(out, n_envs, device)
            logp = _log_prob(out, weights, delay)

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


def step_separately(
    policy: AllocatorPolicy,
    optimizer: torch.optim.Optimizer,
    *,
    policy_side: Tensor,
    value_side: Tensor,
    max_norm: float,
) -> tuple[float, float]:
    """정책 손실과 가치 손실을 **따로** 역전파해 각각 자른 뒤 더해 한 스텝 간다.

    한 손실로 합쳐 전역 노름을 자르면 노름이 큰 쪽이 예산을 독식한다 — M4
    2회차에서 가치 쪽 1,659 대 정책 쪽 19.5 로 정책의 실효 학습률이 3e-9 까지
    떨어졌다(`rl-training.md §4`). 공유 인코더는 두 방향을 다 받되, 어느 쪽도
    상대를 지우지 못한다.

    돌려주는 것은 (정책 쪽, 가치 쪽) **자르기 전** 노름이다.
    """
    params = [p for p in policy.parameters() if p.requires_grad]
    optimizer.zero_grad(set_to_none=True)
    policy_side.backward(retain_graph=True)
    policy_norm = float(torch.nn.utils.clip_grad_norm_(params, max_norm))
    kept = [p.grad.detach().clone() if p.grad is not None else None for p in params]

    optimizer.zero_grad(set_to_none=True)
    value_side.backward()
    value_norm = float(torch.nn.utils.clip_grad_norm_(params, max_norm))
    for param, held in zip(params, kept, strict=True):
        if held is None:
            continue
        if param.grad is None:
            param.grad = held
        else:
            param.grad.add_(held)
    optimizer.step()
    return policy_norm, value_norm


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
    value_grad_norm = 0.0
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
            logp = _log_prob(out, actions[index], delay)

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

            entropy = _entropy(out).mean()
            grad_norm, value_grad_norm = step_separately(
                policy,
                optimizer,
                policy_side=policy_loss - ent_coef * entropy,
                value_side=ppo.vf_coef * value_loss,
                max_norm=ppo.max_grad_norm,
            )

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
        value_grad_norm=value_grad_norm,
        action_reflection=float(
            np.mean([info["action_reflection_rate"] for info in infos])
        ),
        policy_churn=float(np.mean([info["turnover"].mean() for info in infos])),
        concentration_sum=concentration_sum,
        episode_reward=float(np.mean(rollout["rewards"])),
        # 현금 비중 — 진단서 ⑦ 이 지목한 값이라 매 업데이트 남긴다.
        cash_weight=float(np.mean([a[:, -1] for a in rollout["actions"]])),
    )


def attribution(
    policy: AllocatorPolicy,
    rollout: dict[str, Any],
    *,
    ppo: PPOConfig,
    device: torch.device,
) -> Array:
    """종목축 피처별 **정책** 그래디언트 기여도. 개정된 §0 이 이걸로 판정한다.

    ## 가치 헤드를 끊는다

    §0 이 요구하는 것은 *정책* 그래디언트 기여도다. 가치까지 섞으면 **"가치
    함수만 오라클을 보고 정책은 못 보는" 고장이 합격으로 찍힌다.** 그건 실제로
    일어나는 고장이고(선행 프로젝트), 그때 액션 반영률이 0 이 된다.

    torch 에서는 손실에 가치 항을 안 넣는 것으로 끊는다. `canary_ppo._attribution`
    이 `d_value=0` 으로 하는 것과 같다.

    ## 절댓값 평균이고 유효 슬롯만 센다

    `modelops.diagnostics.feature_attribution` 을 그대로 쓴다. 부호 있는 평균은
    **정확히 반대로 읽힐 수 있다** — 종목마다 부호가 갈리는 강한 피처가 0 으로
    상쇄되어 아무도 안 보는 피처처럼 보인다.

    ## 정규화를 끄고 잰다

    관측 정규화가 아직 없어서(진단서 ⑤) 칸마다 크기 스케일이 다르다. 그래서
    **크기 그대로** 비교한다 — 정규화한 순위는 "큰 숫자가 든 칸" 을 위로
    올린다. 실제로 카나리에서 정규화 켠 순위는 2위, 끈 순위는 1위였다.
    """
    from quant_rl_trading.modelops.diagnostics import feature_attribution

    advantages, _ = gae(
        rewards=rollout["rewards"],
        values=rollout["values"],
        dones=rollout["dones"],
        bootstrap=rollout["bootstrap"],
        last_value=rollout["last_value"],
        gamma=ppo.gamma,
        lam=ppo.gae_lambda,
    )
    flat = {
        key: torch.cat(
            [_to_torch(step, device)[key] for step in rollout["obs"]], dim=0
        )
        for key in rollout["obs"][0]
    }
    assets = flat["assets"].clone().requires_grad_(True)
    actions = torch.as_tensor(
        np.concatenate(rollout["actions"]), dtype=torch.float32, device=device
    )
    adv = torch.as_tensor(
        advantages.reshape(-1), dtype=torch.float32, device=device
    )
    adv = (adv - adv.mean()) / (adv.std() + 1e-8)

    out = policy(flat["portfolio"], assets, flat["mask"])
    delay = torch.zeros(
        (actions.shape[0], out.delay_logits.shape[1]), dtype=torch.long, device=device
    )
    # **정책 항만.** 가치·엔트로피를 넣으면 무엇을 재는지가 흐려진다.
    loss = -(_log_prob(out, actions, delay) * adv).mean()
    grad = torch.autograd.grad(loss, assets)[0]

    return feature_attribution(
        grad.detach().cpu().numpy().astype(np.float64),
        flat["mask"].detach().cpu().numpy().astype(bool),
    )
