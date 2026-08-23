#!/usr/bin/env python
"""배분이 어디서 평평해지는지 잰다.

    uv run python tools/diagnose_allocation.py --updates 40

## 왜 필요한가

카나리가 "정답을 꽂아도 성과가 안 갈린다" 로 두 번 불합격했다. 그런데
기여도 순위는 2위/28, z +2.7~3.0 이라 **그래디언트는 오라클 칸에 흐른다.**
관측·보상·신용할당은 살아 있는데 행동이 안 바뀐다.

`rl_updates.concentration_sum` 을 근거로 "배분이 안 변한다" 고 읽었는데
**그건 오독이었다.** 그 값은 `out.concentration.sum(-1)` — Dirichlet 의 α
합이고, 분포의 *정밀도*지 배분의 집중도가 아니다. α 가 서로 자리를 바꾸면서
합만 유지될 수 있고, 그게 바로 "어떤 종목을 선호하게 됐다" 의 모습이다.

## 무엇을 가르는가

세 자리를 나란히 잰다. 셋이 서로 다른 고장을 가리킨다.

    α 분산      정책이 종목을 가리기는 하는가        (안 변하면 정책망 문제)
    목표 비중    정책의 의도가 집중돼 있는가          (평평하면 정책 문제)
    실현 비중    집행 뒤에도 그 집중이 남는가         (여기서 평평해지면 제약층)

목표는 집중인데 실현이 평평하면 고칠 곳은 정책망이 아니라 제약층이다.
둘은 고치는 곳이 완전히 다르다 — 그래서 재기 전에는 어느 쪽도 말하지 않는다.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any
from dataclasses import replace
from datetime import UTC, date, datetime, time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import torch  # noqa: E402

from quant_rl_trading.allocator import train as train_module  # noqa: E402
from quant_rl_trading.modelops.canary_vec import OnlyOracleEnv, VecLatticeEnv  # noqa: E402
from quant_rl_trading.allocator.env import EnvParams  # noqa: E402
from quant_rl_trading.allocator import policy as policy_mod  # noqa: E402
from quant_rl_trading.allocator.env import FEATURE_ORACLE  # noqa: E402
from quant_rl_trading.allocator.policy import AllocatorPolicy, PolicyConfig  # noqa: E402
from quant_rl_trading.allocator.reward import ReturnNormalizer  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from tools.train_rl import build_optimizer  # noqa: E402


def effective_n(weights: np.ndarray) -> float:
    """유효 종목 수 = 1 / Σw².

    **HHI 의 역수다.** 균등하게 24종목이면 24, 한 종목에 몰면 1.
    비중 자체를 보면 24개 숫자를 눈으로 견줘야 하는데 이건 한 숫자다.
    """
    w = np.asarray(weights, dtype=np.float64)
    w = np.clip(w, 0.0, None)
    total = w.sum()
    if total <= 0:
        return 0.0
    w = w / total
    return float(1.0 / np.square(w).sum())


def describe(label: str, values: list[float]) -> str:
    arr = np.asarray(values, dtype=np.float64)
    return (
        f"  {label:16s} 처음 {arr[:5].mean():7.2f} → 끝 {arr[-5:].mean():7.2f} "
        f"(전체 평균 {arr.mean():7.2f} · 최소 {arr.min():6.2f} · 최대 {arr.max():6.2f})"
    )


def attribution_split(
    policy: AllocatorPolicy, rollout: dict, *, ppo, device: torch.device
) -> dict[str, Any]:
    """기여도를 **비중 항만 / 지연 항만** 으로 갈라서 잰다.

    `train.attribution` 은 `log_prob(비중, 지연)` 의 그래디언트를 잰다. 그런데
    그 함수는 두 항의 **합**이다(`policy.log_prob`: `weights_lp + delay_lp`).
    합쳐서 재면 어느 머리로 그래디언트가 갔는지 말할 수 없다.

    이걸 가르는 이유가 있다. 실측 2026-08-21 에 정답 칸을 밀었더니 비중은
    2e-5 만 움직이는데 지연은 7e-4 로 **35배** 움직였다. 그리고 지연은 NAV 에
    거의 영향이 없다. 기여도 1위가 지연 항에서 온 것이라면, 그 게이트는
    "정책이 정답으로 돈을 번다" 를 전혀 보장하지 않는다.
    """
    from quant_rl_trading.allocator.train import _to_torch, gae
    from quant_rl_trading.modelops.diagnostics import feature_attribution

    advantages, _ = gae(
        rewards=rollout["rewards"], values=rollout["values"], dones=rollout["dones"],
        bootstrap=rollout["bootstrap"], last_value=rollout["last_value"],
        gamma=ppo.gamma, lam=ppo.gae_lambda,
    )
    flat = {
        key: torch.cat([_to_torch(step, device)[key] for step in rollout["obs"]], dim=0)
        for key in rollout["obs"][0]
    }
    actions = torch.as_tensor(
        np.concatenate(rollout["actions"]), dtype=torch.float32, device=device
    )
    adv = torch.as_tensor(advantages.reshape(-1), dtype=torch.float32, device=device)
    adv = (adv - adv.mean()) / (adv.std() + 1e-8)
    mask_np = flat["mask"].detach().cpu().numpy().astype(bool)

    out: dict[str, Any] = {}
    for part in ("weights", "delay"):
        assets = flat["assets"].clone().requires_grad_(True)
        res = policy(flat["portfolio"], assets, flat["mask"])
        delay = torch.zeros(
            (actions.shape[0], res.delay_logits.shape[1]), dtype=torch.long, device=device
        )
        if part == "weights":
            valid = res.weight_valid
            clean = policy_mod._sanitize_simplex(
                torch.where(valid, actions, torch.zeros_like(actions))
            )
            lp = policy_mod._masked_dirichlet_log_prob(res.concentration, clean, valid)
        else:
            lp = (res.delay_dist.log_prob(delay) * res.mask).sum(dim=-1)
        loss = -(lp * adv).mean()
        grad = torch.autograd.grad(loss, assets)[0]
        scores = feature_attribution(
            grad.detach().cpu().numpy().astype(np.float64), mask_np
        )
        rank = int(np.argsort(-scores).tolist().index(FEATURE_ORACLE)) + 1
        others = np.delete(scores, FEATURE_ORACLE)
        out[part] = {
            "score": float(scores[FEATURE_ORACLE]),
            "rank": rank,
            "n": int(scores.shape[0]),
            "z": float((scores[FEATURE_ORACLE] - others.mean()) / (others.std() + 1e-12)),
        }
    return out


def gradient_snr(
    policy: AllocatorPolicy, rollout: dict, *, ppo, device: torch.device, chunks: int = 8
) -> dict[str, dict[str, float]]:
    """두 머리의 **그래디언트 신호 대 잡음비**.

    ## 왜 이걸 재는가

    같은 관측·같은 손실인데 지연 머리는 정답을 배우고(기여도 3위) 비중 머리는
    안 배운다(26위). 초기화 gain 은 둘 다 0.01 로 같으니 남은 것은 구조다.

    지연은 Categorical 이라 점수 함수가 유계다. 비중은 Dirichlet 이고,
    `∂logp/∂α = log x - ψ(α) + ψ(Σα)` 에 **`log x` 가 들어 있다.** α 가 1
    근처면 표본이 심플렉스 위에 거의 균등하게 퍼져서 `x_i` 가 1e-6 까지
    내려가고, 그러면 `log x_i` 가 -14 를 찍는다. 한 표본이 그래디언트를
    통째로 흔든다.

    **없는 신호가 아니라 묻힌 신호일 수 있다.** 그러면 고칠 곳은 관측도
    보상도 아니고 분포의 뾰족함(κ)이다.

    ## 재는 방법

    배치를 조각으로 나눠 조각마다 그래디언트를 낸 뒤,
    `SNR = ||조각 평균|| / 조각들의 표준편차` 를 본다. 표본을 늘리면 잡음은
    √n 으로 줄고 신호는 남으므로, 이 값이 작다는 것은 **그 머리의 업데이트가
    표본 잡음에 지배된다**는 뜻이다.
    """
    from quant_rl_trading.allocator.train import _to_torch, gae

    advantages, _ = gae(
        rewards=rollout["rewards"], values=rollout["values"], dones=rollout["dones"],
        bootstrap=rollout["bootstrap"], last_value=rollout["last_value"],
        gamma=ppo.gamma, lam=ppo.gae_lambda,
    )
    flat = {
        key: torch.cat([_to_torch(step, device)[key] for step in rollout["obs"]], dim=0)
        for key in rollout["obs"][0]
    }
    actions = torch.as_tensor(
        np.concatenate(rollout["actions"]), dtype=torch.float32, device=device
    )
    adv = torch.as_tensor(advantages.reshape(-1), dtype=torch.float32, device=device)
    adv = (adv - adv.mean()) / (adv.std() + 1e-8)

    total = int(adv.shape[0])
    size = max(1, total // chunks)
    out: dict[str, dict[str, float]] = {}
    for part, head in (("weights", policy.weight_head), ("delay", policy.delay_head)):
        grads = []
        for start in range(0, total, size):
            sl = slice(start, min(start + size, total))
            if sl.stop - sl.start < 2:
                continue
            res = policy(flat["portfolio"][sl], flat["assets"][sl], flat["mask"][sl])
            delay = torch.zeros(
                (sl.stop - sl.start, res.delay_logits.shape[1]),
                dtype=torch.long, device=device,
            )
            if part == "weights":
                valid = res.weight_valid
                clean = policy_mod._sanitize_simplex(
                    torch.where(valid, actions[sl], torch.zeros_like(actions[sl]))
                )
                lp = policy_mod._masked_dirichlet_log_prob(res.concentration, clean, valid)
            else:
                lp = (res.delay_dist.log_prob(delay) * res.mask).sum(dim=-1)
            loss = -(lp * adv[sl]).mean()
            g = torch.autograd.grad(loss, head.weight, retain_graph=False)[0]
            grads.append(g.detach().flatten().cpu().numpy())
        if len(grads) < 2:
            continue
        arr = np.stack(grads)
        mean = arr.mean(axis=0)
        signal = float(np.linalg.norm(mean))
        noise = float(np.mean(np.linalg.norm(arr - mean, axis=1)))
        out[part] = {
            "signal": signal,
            "noise": noise,
            "snr": signal / (noise + 1e-30),
        }
    return out


def probe_response(
    policy: AllocatorPolicy, rollout: dict, device: torch.device, n_steps: int = 9
) -> dict[str, float]:
    """**정답을 손으로 밀어 보고 비중이 따라 오는지 본다.**

    기여도(`attribution`)는 |∂손실/∂피처| 를 잰다. 그건 **민감도**지
    **정렬**이 아니다 — 정책이 그 칸에 크게 반응하면서도 반응 방향이
    제멋대로일 수 있다. 실측 2026-08-21 에 정확히 그랬다: 기여도 순위 1위
    (z +4.71) 인데 정답↔배분 상관은 −0.015 였다.

    상관은 관측된 값들 사이의 관계라 "정답이 높은 종목이 원래 다른 이유로도
    좋았다" 같은 교란이 섞인다. 여기서는 **다른 칸을 전부 고정한 채 정답
    칸만** 낮은 값에서 높은 값으로 밀고, 그 종목의 목표 비중이 어떻게
    변하는지 본다. 인과가 한 방향뿐이라 해석이 갈릴 자리가 없다.

    돌려주는 값:
      slope       정답을 1 표준편차 올렸을 때 그 종목 비중의 평균 변화(비율)
      monotonic   비중이 정답을 따라 단조증가한 표본의 비율 (0.5 면 동전던지기)
    """
    obs = rollout["obs"][0]
    port = torch.as_tensor(np.asarray(obs["portfolio"]), dtype=torch.float32, device=device)
    assets = torch.as_tensor(np.asarray(obs["assets"]), dtype=torch.float32, device=device)
    mask = torch.as_tensor(np.asarray(obs["mask"]), dtype=torch.float32, device=device)
    if port.ndim == 1:
        port, assets, mask = port[None], assets[None], mask[None]

    live = mask[0] > 0.5
    if int(live.sum()) < 3:
        return {}
    # 밀어 볼 폭은 그 배치의 정답 분포에서 뽑는다. 임의의 절대값을 쓰면
    # 관측에 실제로 오지 않는 구간을 재게 된다.
    truth = assets[..., FEATURE_ORACLE][mask > 0.5]
    lo, hi = float(truth.min()), float(truth.max())
    if not np.isfinite(lo) or hi <= lo:
        return {}
    grid = torch.linspace(lo, hi, n_steps, device=device)

    slopes: list[float] = []
    monotone: list[float] = []
    delay_slopes: list[float] = []
    idxs = torch.nonzero(live).flatten().tolist()[:8]
    with torch.no_grad():
        for slot in idxs:
            weights = []
            for value in grid:
                probe = assets.clone()
                probe[:, slot, FEATURE_ORACLE] = value
                out = policy(port, probe, mask)
                alpha = out.concentration
                share = alpha / alpha.sum(dim=-1, keepdim=True)
                weights.append(float(share[:, slot].mean()))
            arr = np.asarray(weights)
            span = float(grid[-1] - grid[0])
            if span > 0:
                # 표준편차 1 단위로 환산 — 칸마다 폭이 달라도 비교된다.
                slopes.append(float((arr[-1] - arr[0]) / span * float(truth.std())))
            monotone.append(float(np.mean(np.diff(arr) > 0)))

            # **지연 머리도 본다.** 행동 공간은 비중 하나가 아니다 —
            # `attribution` 은 `log_prob(actions, delay)` 의 그래디언트라
            # 지연 쪽으로 흐르는 것도 같이 센다. 비중만 재고 "반응 없음" 이라
            # 하면, 정답을 지연에만 쓰는 정책을 놓친다.
            dl = []
            for value in grid:
                probe = assets.clone()
                probe[:, slot, FEATURE_ORACLE] = value
                out = policy(port, probe, mask)
                logits = out.delay_logits[:, slot]
                dl.append(float(torch.softmax(logits, dim=-1)[:, 0].mean()))
            darr = np.asarray(dl)
            if span > 0:
                delay_slopes.append(
                    float((darr[-1] - darr[0]) / span * float(truth.std()))
                )
    if not slopes:
        return {}
    return {
        "slope": float(np.mean(slopes)),
        "slope_std": float(np.std(slopes)),
        "monotonic": float(np.mean(monotone)),
        "delay_slope": float(np.mean(delay_slopes)) if delay_slopes else 0.0,
        "delay_abs": float(np.mean(np.abs(delay_slopes))) if delay_slopes else 0.0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--updates", type=int, default=40)
    parser.add_argument("--envs", type=int, default=8)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--market", default="KR")
    parser.add_argument("--root", default="data")
    parser.add_argument("--concentration-mode", default="softplus",
                        choices=["softplus", "simplex"])
    parser.add_argument("--only-oracle", action="store_true",
                        help="정답 칸만 남기고 종목 피처를 0 으로. 희석 가설 검증용.")
    parser.add_argument(
        "--n-max", type=int, default=None,
        help="후보 슬롯 수를 덮어쓴다. **창고를 안 건드린다** — 아래 주석 참조.",
    )
    parser.add_argument(
        "--oracle", action="store_true",
        help="관측에 정답을 꽂는다. **성과는 전부 가짜다** — 배선 점검 전용.",
    )
    parser.add_argument(
        "--kappa", type=float, default=None,
        help=(
            "simplex 의 총 집중도 κ (α = softmax(logits)·κ). 안 주면 살아 있는 "
            "칸 수라 **평균 α = 1** 이 되는데, 그게 log x 가 -14 까지 내려가는 "
            "병리 구간이다 (gradient_snr 독스트링). 키우면 표본이 평균 둘레로 "
            "모여 log x 의 분산이 준다."
        ),
    )
    args = parser.parse_args(argv)

    device = torch.device("cpu")
    torch.manual_seed(args.seed)
    ppo = replace(
        train_module.train_config(), num_envs=args.envs, n_steps=128,
        minibatch_size=512, n_epochs=4, lr_policy=3e-5, lr_value=9e-5,
    )
    store = Store(root=Path(args.root))
    train_start, train_end = date(2025, 1, 2), date(2026, 6, 30)
    # **학습 설계값은 "지금" 으로 읽는다.** 학습 구간 첫날로 읽으면 오늘 바꾼
    # 설정을 못 본다 (`EnvParams.from_store` 독스트링). 시장 쪽 값(체결·호가·
    # 환율)은 여전히 그때 값이다.
    run_moment = datetime.now(UTC)  # invariant-allow: wallclock

    # **설정은 오늘 시점으로 읽는다** (`hyper_as_of`). 안 그러면 학습 구간
    # 첫날 기준이라 오늘 바꾼 값을 못 본다 — `EnvParams.from_store` 독스트링.
    #
    # `--n-max` 는 그 위에 얹는 **한 판짜리 덮어쓰기**다. 창고에 남길 값인지
    # 아직 모르는 것을 재 볼 때 쓴다. 정하고 나면 config 를 고친다.
    params = None
    if args.n_max is not None:
        base = EnvParams.from_store(
            store,
            as_of=datetime.combine(train_start, time(0, 0), tzinfo=UTC),
            hyper_as_of=run_moment,
        )
        params = replace(base, n_max=args.n_max)

    env = VecLatticeEnv(
        store=store, train_start=train_start, train_end=train_end,
        market=args.market, n_envs=args.envs, oracle_leak=args.oracle,
        seed=args.seed, params=params, hyper_as_of=run_moment,
    )
    if args.only_oracle:
        env = OnlyOracleEnv(env, FEATURE_ORACLE)
    obs = env.reset()
    policy = AllocatorPolicy(PolicyConfig(
        n_max=int(obs["mask"].shape[1]),
        n_asset_features=int(obs["assets"].shape[-1]),
        n_portfolio_features=int(obs["portfolio"].shape[-1]),
        n_delay_choices=3,
        concentration_mode=args.concentration_mode,
        concentration_total=args.kappa,
    )).to(device)
    optimizer = build_optimizer(policy, ppo)
    normalizer = ReturnNormalizer(gamma=ppo.gamma, num_envs=args.envs)
    generator = torch.Generator(device=device)
    generator.manual_seed(args.seed)

    #: **정답과 배분의 상관.** NAV 보다 날카롭다 — NAV 는 종목 선택 말고도
    #: 비용·타이밍·잡음이 다 섞여 있어서, 정책이 정답을 조금 쓰는지 전혀
    #: 안 쓰는지를 못 가른다. 이 값은 그 한 가지만 묻는다.
    #:
    #: 스텝마다 "그 판의 오라클 값" 과 "그 판이 준 목표 비중" 의 순위상관을
    #: 낸다. 정책이 정답대로 배분하면 +1 에 가깝고, 무시하면 0 근처다.
    oracle_corr: list[float] = []
    alpha_spread: list[float] = []
    target_n: list[float] = []
    realized_n: list[float] = []
    reflection: list[float] = []

    for index in range(1, args.updates + 1):
        rollout = train_module.collect(
            env, policy, obs, ppo=ppo, normalizer=normalizer, device=device
        )
        obs = rollout["obs_after"]
        for step_obs, info in zip(rollout["obs"], rollout["infos"], strict=False):
            # **환경마다 따로 센다.** VecEnv 의 info 는 (n_envs, n_max+1) 로
            # 쌓여 오는데, 그걸 한 줄로 펴서 HHI 를 내면 8개 포트폴리오가
            # 한 포트폴리오처럼 세어진다 — 실측으로 슬롯이 30칸인데 유효
            # 종목수 94가 나왔다. 숫자가 슬롯 수를 넘으면 그건 집중도가
            # 아니라 계산 실수다.
            #
            # `target_weights` 의 마지막 칸은 현금이라 빼고 센다 — 안 빼면
            # 현금이 한 종목처럼 세어진다.
            targets = np.atleast_2d(np.asarray(info["target_weights"]))
            # 오라클 칸과 목표 비중의 순위상관. 살아 있는 칸만 본다 —
            # 죽은 칸은 비중이 늘 0 이라 상관을 0 쪽으로 끌어내린다.
            obs_assets = np.atleast_3d(np.asarray(step_obs["assets"]))
            obs_mask = np.atleast_2d(np.asarray(step_obs["mask"])) > 0.5
            for env_i in range(targets.shape[0]):
                live = obs_mask[env_i]
                if live.sum() < 3:
                    continue
                truth = obs_assets[env_i][live, FEATURE_ORACLE]
                got = targets[env_i][: live.shape[0]][live]
                if np.std(truth) <= 0 or np.std(got) <= 0:
                    continue
                r = np.corrcoef(
                    np.argsort(np.argsort(truth)), np.argsort(np.argsort(got))
                )[0, 1]
                if np.isfinite(r):
                    oracle_corr.append(float(r))
            realized = np.atleast_2d(np.asarray(info["realized_weights"]))
            target_n.append(float(np.mean([effective_n(row[:-1]) for row in targets])))
            realized_n.append(float(np.mean([effective_n(row) for row in realized])))
            reflection.append(float(np.mean(info["action_reflection_rate"])))

        with torch.no_grad():
            # `rollout["obs"]` 는 스텝별 dict 의 리스트다. 학습 쪽과 같은
            # 방식으로 펴야 α 가 같은 관측에 대응한다.
            flat = {
                key: torch.cat(
                    [train_module._to_torch(step, device)[key]
                     for step in rollout["obs"]],
                    dim=0,
                )
                for key in ("portfolio", "assets", "mask")
            }
            out = policy(flat["portfolio"], flat["assets"], flat["mask"])
            alpha = out.concentration.cpu().numpy()
            valid = flat["mask"].cpu().numpy() > 0.5
            # **α 의 흩어짐.** 합이 아니라 흩어짐을 본다 — 합은 그대로인 채
            # α 가 자리를 바꾸는 것이 "선호가 생겼다" 의 모습이다.
            for row, keep in zip(alpha, valid, strict=False):
                live = row[: keep.shape[0]][keep]
                if live.size > 1 and live.mean() > 0:
                    alpha_spread.append(float(live.std() / live.mean()))

        train_module.update(
            policy, optimizer, rollout, ppo=ppo,
            progress=1.0 - (index - 1) / args.updates, device=device,
            generator=generator,
        )
        if index % 10 == 0 or index == 1:
            print(f"  [{index}/{args.updates}]", flush=True)

    probe = probe_response(policy, rollout, device) if args.oracle else {}
    split = (
        attribution_split(policy, rollout, ppo=ppo, device=device)
        if args.oracle else {}
    )
    snr = gradient_snr(policy, rollout, ppo=ppo, device=device)

    print()
    print(f"오라클 {'켬 — 성과는 가짜다' if args.oracle else '끔'} · 슬롯 {env.config.n_assets}칸")
    if oracle_corr:
        print(describe("정답↔배분 상관", oracle_corr))
    print(describe("α 변동계수", alpha_spread))
    print(describe("목표 유효종목수", target_n))
    print(describe("실현 유효종목수", realized_n))
    print(describe("액션 반영률", reflection))
    print()

    t_end = float(np.mean(target_n[-5:]))
    r_end = float(np.mean(realized_n[-5:]))
    a0, a1 = float(np.mean(alpha_spread[:5])), float(np.mean(alpha_spread[-5:]))
    if probe:
        print()
        print("[인과] 다른 칸을 고정하고 **정답 칸만** 밀어 봤다")
        print(f"  기울기      {probe['slope']:+.5f} (정답 +1σ 당 비중 변화 · 흩어짐 {probe['slope_std']:.5f})")
        print(f"  단조증가 비율 {probe['monotonic']:.2f} (0.50 이면 동전던지기)")
        print(f"  지연 머리     {probe['delay_slope']:+.5f} · |기울기| {probe['delay_abs']:.5f}")

    if split:
        print()
        print("[기여도] 두 머리를 갈라서 — 합쳐 재면 어디로 갔는지 알 수 없다")
        for part, label in (("weights", "비중 항만"), ("delay", "지연 항만")):
            d = split[part]
            print(f"  {label}  순위 {d['rank']}위/{d['n']} · z {d['z']:+.2f} · 값 {d['score']:.3e}")

    if snr:
        print()
        print("[잡음] 머리별 그래디언트 신호 대 잡음비 — 없는 신호인가 묻힌 신호인가")
        for part, label in (("weights", "비중 머리"), ("delay", "지연 머리")):
            d = snr.get(part)
            if d:
                print(f"  {label}  SNR {d['snr']:.4f} · 신호 {d['signal']:.3e} · 잡음 {d['noise']:.3e}")
        w, dl = snr.get("weights"), snr.get("delay")
        if w and dl and dl["snr"] > w["snr"] * 3:
            print(f"  → 지연 쪽 SNR 이 {dl['snr'] / max(w['snr'], 1e-30):.1f}배다.")
            print("    비중 머리의 업데이트가 **표본 잡음에 지배된다** — 신호가")
            print("    없는 것이 아니라 묻혀 있다. 고칠 곳은 분포의 뾰족함(κ)이다.")
        elif w and dl:
            print("  → 두 머리의 SNR 이 비슷하다. 잡음 가설은 아니다.")

    print("판정:")
    if split:
        w, dl = split["weights"], split["delay"]
        if dl["rank"] <= 3 < w["rank"]:
            print("  · 기여도가 **지연 머리에서 온다**. 비중 쪽은 순위 밖이다.")
            print("    지연은 NAV 를 거의 안 바꾸므로 그 게이트는 아무것도 보장하지 않는다.")
        elif w["rank"] <= 3:
            print("  · 기여도가 비중 항에서도 높다 — 지연 탓으로 돌릴 수 없다.")
    if probe:
        if probe["monotonic"] >= 0.7 and probe["slope"] > 0:
            print("  · 정답을 밀면 비중이 따라 온다 — 정책은 **방향을 안다**.")
        elif abs(probe["slope"]) < 1e-4:
            print("  · 정답을 밀어도 **비중이** 안 움직인다.")
            if probe["delay_abs"] > 1e-3:
                print(f"    그런데 지연 머리는 움직인다(|{probe['delay_abs']:.4f}|) — 정책이")
                print("    정답을 **비중이 아니라 지연 결정에** 쓰고 있다.")
            else:
                print("    지연 머리도 안 움직인다 — 출력 전체가 이 칸에 무감각하다.")
                print("    그러면 기여도 1위는 **기여도 함수 쪽을 의심**해야 한다.")
        else:
            print("  · 비중이 움직이긴 하는데 방향이 제멋대로다 "
                  f"(단조 {probe['monotonic']:.2f}) — **정렬이 안 됐다**.")
    if oracle_corr:
        c0, c1 = float(np.mean(oracle_corr[:20])), float(np.mean(oracle_corr[-20:]))
        if c1 >= 0.30:
            print(f"  · 정답↔배분 상관 {c0:+.3f} → {c1:+.3f}. **정책이 정답대로 배분한다.**")
            print("    그런데 NAV 가 안 갈리면 남은 것은 비용·타이밍이다.")
        elif c1 > c0 + 0.05:
            print(f"  · 상관이 {c0:+.3f} → {c1:+.3f} 로 오르는 중이다 — 방향은 맞고 **크기가 모자라다.**")
        else:
            print(f"  · 상관이 {c0:+.3f} → {c1:+.3f}. **정책이 정답을 배분에 안 쓴다.**")
            print("    기여도가 높은데 여기가 0 이면 그래디언트가 행동으로 안 간다.")
    if a1 <= a0 * 1.1:
        print("  · α 흩어짐이 안 커졌다 — 정책이 종목을 **가리지 않는다**.")
        print("    고칠 곳은 정책망·학습률 쪽이다.")
    else:
        print(f"  · α 흩어짐이 {a0:.3f} → {a1:.3f} 로 커졌다 — 정책은 선호를 배웠다.")
    if r_end > t_end * 1.3:
        print(f"  · 목표 {t_end:.1f}종목 → 실현 {r_end:.1f}종목. **집행이 평평하게 만든다** —")
        print("    고칠 곳은 정책망이 아니라 제약층이다.")
    else:
        print(f"  · 목표 {t_end:.1f} · 실현 {r_end:.1f} — 집행은 집중을 안 지운다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
