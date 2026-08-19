#!/usr/bin/env python
"""오라클 카나리 — **torch 경로** 판정 (§0 개정본).

    uv run python tools/verify_oracle_canary.py --updates 30

## 무엇을 판정하는가

개정된 §0 기준(2026-08-19): **오라클 켠 판의 오라클 그래디언트 기여도가
대조군의 10배 이상.** `explained_variance > 0.5` 는 뺐다 — 실측에서 오라클을
끈 대조군의 EV(0.903)가 켠 판(0.606)보다 높았고, 판정 도구가 정답의 유무와
반대로 움직이면 느슨한 기준이 아니라 **틀린 기준**이다. 근거는
`docs/rl-diagnosis.md`.

## 두 판을 같은 설정으로 돌린다

절대값에는 뜻이 없다 — 보상 크기와 학습률에 따라 통째로 움직인다. **배수만
본다.** 그래서 시드·업데이트 수·환경 수를 똑같이 두고 오라클만 켜고 끈다.

## 왜 이 도구가 따로 있는가

카나리는 `train_rl.py` 로도 돌릴 수 있지만(`--oracle-leak`), 그러면 **판정을
사람이 눈으로** 해야 한다. 기준이 문서에만 있고 코드에 없으면 다음에 누가
다른 숫자로 통과시킨다. 여기서 판정까지 한다.

## 이 판의 성과는 전부 가짜다

오라클을 켜면 관측에 5일 뒤 실제 초과수익이 들어간다. 배선 점검 전용이고,
`rl_updates` 에도 적지 않는다 — 학습 곡선에 섞이면 화면이 거짓말한다.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

import numpy as np
import torch

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.allocator import train as train_module  # noqa: E402
from quant_rl_trading.allocator.env import FEATURE_ORACLE  # noqa: E402
from quant_rl_trading.allocator.policy import AllocatorPolicy, PolicyConfig  # noqa: E402
from quant_rl_trading.allocator.reward import ReturnNormalizer  # noqa: E402
from quant_rl_trading.modelops.canary_vec import VecLatticeEnv  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from tools.train_rl import build_optimizer  # noqa: E402

#: 합격선. **문서(§0)와 같은 값을 여기 다시 적는다** — 화면·문서·코드가 다른
#: 선을 그으면 어느 쪽이 맞는지 아무도 모르게 된다.
CONTRIBUTION_RATIO = 10.0


def run_one(
    *, store: Store, oracle: bool, updates: int, envs: int, seed: int, market: str
) -> tuple[float, np.ndarray]:
    """한 판 돌리고 (오라클 칸 기여도, 전체 기여도) 를 돌려준다."""
    device = torch.device("cpu")
    torch.manual_seed(seed)
    ppo = replace(train_module.train_config(), num_envs=envs, n_steps=128,
                  minibatch_size=512, n_epochs=4)
    env = VecLatticeEnv(
        store=store, train_start=date(2025, 1, 2), train_end=date(2026, 6, 30),
        market=market, n_envs=envs, oracle_leak=oracle, seed=seed,
    )
    obs = env.reset()
    policy = AllocatorPolicy(PolicyConfig(
        n_max=int(obs["mask"].shape[1]),
        n_asset_features=int(obs["assets"].shape[-1]),
        n_portfolio_features=int(obs["portfolio"].shape[-1]),
        n_delay_choices=3,
    )).to(device)
    optimizer = build_optimizer(policy, ppo)
    generator = torch.Generator(device=device)
    generator.manual_seed(seed)
    normalizer = ReturnNormalizer(gamma=ppo.gamma, num_envs=envs)

    rollout: dict = {}
    for index in range(1, updates + 1):
        rollout = train_module.collect(
            env, policy, obs, ppo=ppo, normalizer=normalizer, device=device
        )
        obs = rollout["obs_after"]
        log = train_module.update(
            policy, optimizer, rollout, ppo=ppo,
            progress=1.0 - (index - 1) / updates, device=device, generator=generator,
        )
        if index % 10 == 0:
            print(
                f"  [{index}/{updates}] EV {log.explained_variance:+.4f} · "
                f"KL {log.approx_kl:.5f}",
                flush=True,
            )
    scores = train_module.attribution(policy, rollout, ppo=ppo, device=device)
    return float(scores[FEATURE_ORACLE]), scores


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--updates", type=int, default=30)
    parser.add_argument("--envs", type=int, default=8)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--market", default="KR")
    parser.add_argument("--root", default="data")
    args = parser.parse_args(argv)

    store = Store(root=Path(args.root))
    print("오라클 켠 판 — **이 판의 성과는 전부 가짜다**", flush=True)
    on, on_all = run_one(
        store=store, oracle=True, updates=args.updates, envs=args.envs,
        seed=args.seed, market=args.market,
    )
    print("대조군 (오라클 끔)", flush=True)
    off, off_all = run_one(
        store=store, oracle=False, updates=args.updates, envs=args.envs,
        seed=args.seed, market=args.market,
    )

    # 대조군에서 그 칸은 **섹터 원핫 자리**다(FEATURE_ORACLE = FEATURE_SECTOR_BASE).
    # 즉 같은 칸을 오라클이 덮어쓴 것이라 비교가 성립한다.
    ratio = on / off if off > 0 else float("inf")
    rank_on = int(np.argsort(-on_all).tolist().index(FEATURE_ORACLE)) + 1

    print()
    print(f"오라클 칸 기여도  켬 {on:.3e} · 끔 {off:.3e} · 배수 {ratio:.1f}x")
    print(f"켠 판에서의 순위  {rank_on}위 / {len(on_all)}")
    print(f"합격선            {CONTRIBUTION_RATIO:.0f}x")

    passed = ratio >= CONTRIBUTION_RATIO
    print(f"\n판정: {'합격' if passed else '불합격'}")
    if not passed:
        print(
            "정답을 꽂아도 정책이 안 움직인다 — 학습 코드가 아니라 **배선**을 본다.\n"
            "docs/design/rl-training.md §5 의 원인 목록 순서대로."
        )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
