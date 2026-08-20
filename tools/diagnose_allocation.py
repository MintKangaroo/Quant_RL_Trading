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
from dataclasses import replace
from datetime import UTC, date, datetime, time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import torch  # noqa: E402

from quant_rl_trading.allocator import train as train_module  # noqa: E402
from quant_rl_trading.modelops.canary_vec import VecLatticeEnv  # noqa: E402
from quant_rl_trading.allocator.env import EnvParams  # noqa: E402
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--updates", type=int, default=40)
    parser.add_argument("--envs", type=int, default=8)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--market", default="KR")
    parser.add_argument("--root", default="data")
    parser.add_argument(
        "--n-max", type=int, default=None,
        help="후보 슬롯 수를 덮어쓴다. **창고를 안 건드린다** — 아래 주석 참조.",
    )
    parser.add_argument(
        "--oracle", action="store_true",
        help="관측에 정답을 꽂는다. **성과는 전부 가짜다** — 배선 점검 전용.",
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
    obs = env.reset()
    policy = AllocatorPolicy(PolicyConfig(
        n_max=int(obs["mask"].shape[1]),
        n_asset_features=int(obs["assets"].shape[-1]),
        n_portfolio_features=int(obs["portfolio"].shape[-1]),
        n_delay_choices=3,
    )).to(device)
    optimizer = build_optimizer(policy, ppo)
    normalizer = ReturnNormalizer(gamma=ppo.gamma, num_envs=args.envs)
    generator = torch.Generator(device=device)
    generator.manual_seed(args.seed)

    alpha_spread: list[float] = []
    target_n: list[float] = []
    realized_n: list[float] = []
    reflection: list[float] = []

    for index in range(1, args.updates + 1):
        rollout = train_module.collect(
            env, policy, obs, ppo=ppo, normalizer=normalizer, device=device
        )
        obs = rollout["obs_after"]
        for info in rollout["infos"]:
            # **환경마다 따로 센다.** VecEnv 의 info 는 (n_envs, n_max+1) 로
            # 쌓여 오는데, 그걸 한 줄로 펴서 HHI 를 내면 8개 포트폴리오가
            # 한 포트폴리오처럼 세어진다 — 실측으로 슬롯이 30칸인데 유효
            # 종목수 94가 나왔다. 숫자가 슬롯 수를 넘으면 그건 집중도가
            # 아니라 계산 실수다.
            #
            # `target_weights` 의 마지막 칸은 현금이라 빼고 센다 — 안 빼면
            # 현금이 한 종목처럼 세어진다.
            targets = np.atleast_2d(np.asarray(info["target_weights"]))
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

    print()
    print(f"오라클 {'켬 — 성과는 가짜다' if args.oracle else '끔'} · 슬롯 {env.config.n_assets}칸")
    print(describe("α 변동계수", alpha_spread))
    print(describe("목표 유효종목수", target_n))
    print(describe("실현 유효종목수", realized_n))
    print(describe("액션 반영률", reflection))
    print()

    t_end = float(np.mean(target_n[-5:]))
    r_end = float(np.mean(realized_n[-5:]))
    a0, a1 = float(np.mean(alpha_spread[:5])), float(np.mean(alpha_spread[-5:]))
    print("판정:")
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
