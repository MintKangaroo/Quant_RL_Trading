#!/usr/bin/env python
"""PPO 본 학습 (M4-4-5). **결과를 창고에 적는다.**

    uv run python tools/train_rl.py --market KR --timesteps 200000 --seed 0
    uv run python tools/train_rl.py --market KR --timesteps 200000 --oracle-leak

## 창고에 적는 것이 이 도구의 절반이다

학습만 돌리고 결과를 로그로만 남기면 화면은 영영 빈다. 실제로 `allocator/`
가 들어온 뒤에도 학습 탭이 비어 있었는데, 원인이 학습 코드가 아니라
**담을 표가 없어서**였다(2026-08-19). 그래서 업데이트마다 `rl_updates` 에
한 행씩 쌓는다 — 중간에 죽어도 거기까지는 화면에 남는다.

## 재현성 (§11)

행마다 `seed`·`git_commit`·`config_fingerprint` 를 같이 적는다. 좋은 성적이
나왔는데 다시 못 만드는 것이 이 프로젝트에서 제일 비싼 실패다.

## 오라클 카나리도 이 도구로 돈다

`--oracle-leak` 을 주면 관측에 5일 뒤 실제 초과수익이 들어간다. **그 판의
성과는 전부 가짜다** — 배선 점검 전용이다. 개정된 §0 기준은 "오라클 켠 판의
기여도 ≥ 대조군의 10배" 라 **두 판을 같은 설정으로 돌려야** 한다. 절대값에는
뜻이 없다(보상 크기·학습률에 따라 통째로 움직인다).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import torch

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.allocator import train as train_module  # noqa: E402
from quant_rl_trading.allocator.policy import AllocatorPolicy, PolicyConfig  # noqa: E402
from quant_rl_trading.allocator.reward import ReturnNormalizer  # noqa: E402
from quant_rl_trading.modelops.canary_vec import VecLatticeEnv  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402

RL_UPDATES = "rl_updates"


def git_commit() -> str:
    """지금 코드의 지문. 없으면 빈 문자열 — 학습을 막지는 않는다."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
            capture_output=True, text=True, timeout=10, check=False,
        )
        return out.stdout.strip()[:12]
    except Exception:  # pragma: no cover - git 이 없는 환경
        return ""


def build_optimizer(policy: AllocatorPolicy, ppo) -> torch.optim.Optimizer:  # noqa: ANN001
    """가치 쪽 학습률을 2~3배 높인다 (§4).

    인코더는 정책·가치가 공유하므로 **정책 학습률**을 따른다. 공유부에 높은
    학습률을 주면 가치를 맞추려는 힘이 정책 표현까지 흔든다.
    """
    value_params = list(policy.value_pool.parameters()) + list(
        policy.value_head.parameters()
    )
    value_ids = {id(p) for p in value_params}
    rest = [p for p in policy.parameters() if id(p) not in value_ids]
    return torch.optim.Adam(
        [
            {"params": rest, "lr": ppo.lr_policy},
            {"params": value_params, "lr": ppo.lr_value},
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", default="KR")
    parser.add_argument("--root", default="data")
    parser.add_argument("--timesteps", type=int, default=None, help="기본은 §4 의 2천만")
    parser.add_argument("--envs", type=int, default=None)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--oracle-leak", action="store_true")
    parser.add_argument("--train-start", default="2025-01-02")
    parser.add_argument("--train-end", default="2026-06-30")
    parser.add_argument("--curriculum", default="C1")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--no-store", action="store_true", help="창고에 안 적는다")
    args = parser.parse_args(argv)

    ppo = train_module.train_config()
    if args.timesteps:
        ppo = replace(ppo, total_timesteps=args.timesteps)
    if args.envs:
        ppo = replace(ppo, num_envs=args.envs)

    device = torch.device("cpu")
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)

    store = Store(root=Path(args.root))
    env = VecLatticeEnv(
        store=store,
        train_start=date.fromisoformat(args.train_start),
        train_end=date.fromisoformat(args.train_end),
        market=args.market,
        n_envs=ppo.num_envs,
        oracle_leak=args.oracle_leak,
        seed=args.seed,
    )
    obs = env.reset()

    policy = AllocatorPolicy(
        PolicyConfig(
            n_max=int(obs["mask"].shape[1]),
            n_asset_features=int(obs["assets"].shape[-1]),
            n_portfolio_features=int(obs["portfolio"].shape[-1]),
            n_delay_choices=3,
        )
    ).to(device)
    optimizer = build_optimizer(policy, ppo)
    generator = torch.Generator(device=device)
    generator.manual_seed(args.seed)
    normalizer = ReturnNormalizer(gamma=ppo.gamma, num_envs=ppo.num_envs)

    per_update = ppo.num_envs * ppo.n_steps
    total_updates = max(1, ppo.total_timesteps // per_update)
    run_id = args.run_id or (
        f"rl-{datetime.now(UTC):%Y%m%d-%H%M%S}"  # invariant-allow: wallclock
        f"-s{args.seed}{'-oracle' if args.oracle_leak else ''}"
    )
    commit = git_commit()
    fingerprint = f"{ppo.lr_policy}/{ppo.gamma}/{ppo.num_envs}x{ppo.n_steps}"

    print(
        f"{run_id} · {args.market} · {total_updates}업데이트 × {per_update}스텝 "
        f"= {total_updates * per_update:,} · 파라미터 "
        f"{sum(p.numel() for p in policy.parameters()):,}"
        + (" · **오라클 켬 — 성과는 가짜다**" if args.oracle_leak else ""),
        flush=True,
    )

    started = time.time()
    for index in range(1, total_updates + 1):
        # 진행도는 선형 감쇠에 쓴다(§4 — 학습률·엔트로피 계수).
        progress = 1.0 - (index - 1) / total_updates
        rollout = train_module.collect(
            env, policy, obs, ppo=ppo, normalizer=normalizer, device=device
        )
        obs = rollout["obs_after"]
        log = train_module.update(
            policy, optimizer, rollout,
            ppo=ppo, progress=progress, device=device, generator=generator,
        )
        log.update = index
        log.step = index * per_update

        elapsed = time.time() - started
        remain = elapsed / index * (total_updates - index)
        print(
            f"[{index}/{total_updates}] EV {log.explained_variance:+.4f} · "
            f"KL {log.approx_kl:.5f} · ent {log.entropy:.1f} · "
            f"반영률 {log.action_reflection:.3f} · 현금 {log.cash_weight:.3f} · "
            f"보상 {log.episode_reward:+.4f} · 남은시간 ~{remain / 60:.0f}분",
            flush=True,
        )

        if not args.no_store:
            # **업데이트마다 쓴다.** 끝에 몰아 쓰면 중간에 죽었을 때 몇 시간이
            # 통째로 사라지고, 화면은 그동안 아무것도 못 보여준다.
            moment = datetime.now(UTC)  # invariant-allow: wallclock
            store.append(
                RL_UPDATES,
                [{
                    "entity_id": run_id,
                    "valid_from": moment,
                    "observed_at": moment,
                    "source": "ppo",
                    "update": log.update,
                    "step": log.step,
                    "seed": args.seed,
                    "market": args.market,
                    "curriculum": args.curriculum,
                    "explained_variance": log.explained_variance,
                    "approx_kl": log.approx_kl,
                    "entropy": log.entropy,
                    "grad_norm": log.grad_norm,
                    "action_reflection": log.action_reflection,
                    "policy_churn": log.policy_churn,
                    "concentration_sum": log.concentration_sum,
                    "episode_reward": log.episode_reward,
                    "cash_weight": log.cash_weight,
                    "git_commit": commit,
                    "config_fingerprint": fingerprint,
                }],
                ingest_run_id=f"{run_id}-u{log.update}",
                source="ppo",
            )

    print(f"완료 — {total_updates}업데이트 · {(time.time() - started) / 60:.1f}분", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
