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
from datetime import UTC, date, datetime
from datetime import time as dt_time
from pathlib import Path

import numpy as np
import torch

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.allocator import train as train_module  # noqa: E402
from quant_rl_trading.allocator.env import FEATURE_ORACLE, EnvParams  # noqa: E402
from quant_rl_trading.allocator.policy import AllocatorPolicy, PolicyConfig  # noqa: E402
from quant_rl_trading.allocator.reward import ReturnNormalizer  # noqa: E402
from quant_rl_trading.modelops.canary_vec import OnlyOracleEnv, VecLatticeEnv  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from tools.train_rl import build_optimizer  # noqa: E402

#: 합격선. **문서(§0)와 같은 값을 여기 다시 적는다** — 화면·문서·코드가 다른
#: 선을 그으면 어느 쪽이 맞는지 아무도 모르게 된다.
CONTRIBUTION_RATIO = 10.0

#: 카나리 전용 학습률. 본 학습(1e-5)보다 높다 — 위 `run_one` 주석 참고.
CANARY_LR = 3e-5


def run_one(
    *, store: Store, oracle: bool, updates: int, envs: int, seed: int, market: str,
    ent_coef: float | None = None, n_max: int | None = None,
    concentration_mode: str = "softplus", only_oracle: bool = False
) -> tuple[float, np.ndarray]:
    """한 판 돌리고 (오라클 칸 기여도, 전체 기여도) 를 돌려준다."""
    device = torch.device("cpu")
    torch.manual_seed(seed)
    # **카나리는 본 학습보다 높은 학습률을 쓴다.** `PPOConfig` 독스트링이
    # 적어 둔 그대로다: "카나리는 20만 스텝 안에 답을 내야 한다 — 여기서
    # 학습률이 모자라 못 배우면 배선이 아니라 예산을 재는 시험이 된다."
    #
    # 실측 2026-08-19에 그 함정을 그대로 밟았다. 본 학습 설정(1e-5)으로
    # 돌렸더니 예산을 4배 늘려도 배수가 1.8x → 4.4x 로 오르다 말았다.
    # numpy 카나리는 같은 스텝 수에서 68x 였고, 남은 차이는 학습률뿐이다
    # (3e-3 vs 1e-5).
    #
    # 1e-4 는 이 정책망에서 진동·발산한다(한 스텝에 배분 13%). 그 사이인
    # 3e-5 를 쓴다 — 1스텝 배분 L1 0.039, 3스텝 0.032 로 안정이다.
    ppo = replace(train_module.train_config(), num_envs=envs, n_steps=128,
                  minibatch_size=512, n_epochs=4,
                  lr_policy=CANARY_LR, lr_value=CANARY_LR * 3,
                  **({} if ent_coef is None else {"ent_coef": ent_coef}))
    # 설정은 오늘 시점으로 읽는다(`hyper_as_of`). `--n-max` 는 그 위에 얹는
    # 한 판짜리 덮어쓰기다 — 창고에 남길 값인지 아직 모르는 것을 재 볼 때 쓴다.
    train_start, train_end = date(2025, 1, 2), date(2026, 6, 30)
    # 학습 설계값은 "지금" 으로 읽는다 — 학습 구간 첫날로 읽으면 오늘 바꾼
    # 설정을 못 본다 (`EnvParams.from_store` 독스트링).
    run_moment = datetime.now(UTC)  # invariant-allow: wallclock
    params = None
    if n_max is not None:
        base = EnvParams.from_store(
            store,
            as_of=datetime.combine(train_start, dt_time(0, 0), tzinfo=UTC),
            hyper_as_of=run_moment,
        )
        params = replace(base, n_max=n_max)
    env = VecLatticeEnv(
        store=store, train_start=train_start, train_end=train_end,
        market=market, n_envs=envs, oracle_leak=oracle, seed=seed, params=params,
        hyper_as_of=run_moment,
    )
    if only_oracle and oracle:
        env = OnlyOracleEnv(env, FEATURE_ORACLE)
    obs = env.reset()
    policy = AllocatorPolicy(PolicyConfig(
        n_max=int(obs["mask"].shape[1]),
        n_asset_features=int(obs["assets"].shape[-1]),
        n_portfolio_features=int(obs["portfolio"].shape[-1]),
        n_delay_choices=3,
        concentration_mode=concentration_mode,
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
    # **행동으로도 잰다.** 기여도는 대리 지표다 — 진짜 질문은 "정답을 준
    # 판이 실제로 더 버는가" 이고, 그건 NAV 로 답한다. 정답을 쓰면 성과가
    # 갈라져야 하고, 안 쓰면 안 갈라진다.
    navs = [float(np.mean(info["nav"])) for info in rollout["infos"]]
    return float(scores[FEATURE_ORACLE]), scores, float(np.mean(navs))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--updates", type=int, default=30)
    parser.add_argument("--envs", type=int, default=8)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--market", default="KR")
    parser.add_argument("--root", default="data")
    # **진단용이다.** 엔트로피 보너스가 배분을 균등에 붙들어 두는지 보려면
    # 그것만 빼고 같은 판을 돌려 봐야 한다. 본 학습 설정은 안 건드린다.
    parser.add_argument("--ent-coef", type=float, default=None)
    parser.add_argument("--n-max", type=int, default=None,
                        help="후보 슬롯 수를 덮어쓴다. 창고를 안 건드린다.")
    parser.add_argument("--only-oracle", action="store_true",
                        help="정답 칸만 남긴다(오라클 판에만). 희석 가설 검증용.")
    parser.add_argument("--concentration-mode", default="softplus",
                        choices=["softplus", "simplex"],
                        help="α 만드는 방식. simplex 는 선호와 탐색을 가른다.")
    args = parser.parse_args(argv)

    store = Store(root=Path(args.root))
    print("오라클 켠 판 — **이 판의 성과는 전부 가짜다**", flush=True)
    on, on_all, on_nav = run_one(
        store=store, oracle=True, updates=args.updates, envs=args.envs,
        seed=args.seed, market=args.market, ent_coef=args.ent_coef,
        n_max=args.n_max, concentration_mode=args.concentration_mode,
        only_oracle=args.only_oracle,
    )
    print("대조군 (오라클 끔)", flush=True)
    off, off_all, off_nav = run_one(
        store=store, oracle=False, updates=args.updates, envs=args.envs,
        seed=args.seed, market=args.market, ent_coef=args.ent_coef,
        n_max=args.n_max, concentration_mode=args.concentration_mode,
        only_oracle=args.only_oracle,
    )

    # 대조군에서 그 칸은 **섹터 원핫 자리**다(FEATURE_ORACLE = FEATURE_SECTOR_BASE).
    # 즉 같은 칸을 오라클이 덮어쓴 것이라 비교가 성립한다.
    ratio = on / off if off > 0 else float("inf")
    rank_on = int(np.argsort(-on_all).tolist().index(FEATURE_ORACLE)) + 1

    print()
    print(f"오라클 칸 기여도  켬 {on:.3e} · 끔 {off:.3e} · 배수 {ratio:.1f}x")
    print(f"켠 판에서의 순위  {rank_on}위 / {len(on_all)}")
    print(f"합격선            {CONTRIBUTION_RATIO:.0f}x")

    # -- 판별용 진단 -------------------------------------------------------
    #
    # 배수만으로는 "기준이 이 구조에 안 맞는 것" 과 "정말 정답을 안 쓰는 것"
    # 을 못 가른다. 둘을 가르는 값을 같이 낸다.

    # 1) 분포상 위치. 4x 가 낮은지 높은지는 **다른 칸들과 비교해야** 뜻이 있다.
    others = np.delete(on_all, FEATURE_ORACLE)
    z = (on - others.mean()) / (others.std() + 1e-12)
    print()
    print(f"[분포] 켠 판 다른 27칸  중앙값 {np.median(others):.3e} · "
          f"최대 {others.max():.3e}")
    print(f"[분포] 오라클의 z      {z:+.2f} (다른 칸 평균 대비 표준편차 배수)")

    # 2) **행동.** 정답을 쓰면 성과가 갈라져야 한다. 이게 안 갈라지면 기여도가
    #    무슨 값이든 정책은 정답을 안 쓰는 것이다.
    lift = (on_nav / off_nav - 1.0) if off_nav > 0 else float("nan")
    print(f"[행동] 평균 NAV       켬 {on_nav:,.0f} · 끔 {off_nav:,.0f} · "
          f"차이 {lift:+.2%}")
    print()
    # **두 축을 따로 읽는다.** 처음에는 NAV 하나로 "배선 문제" 를 판정했는데
    # 그 문장이 틀렸다. 실측 2026-08-20 에 오라클이 28칸 중 **1위** · z +4.29
    # 인데도 NAV 는 +1.47% 였다 — 그래디언트는 완벽하게 흐르는데 성과가 안
    # 따라오는 상태다. 그걸 "배선 문제" 라 부르면 없는 배선을 뒤지게 된다.
    #
    #   기여도가 낮다 + 성과 안 갈림  → 정답이 정책에 안 닿는다 (배선)
    #   기여도가 높다 + 성과 안 갈림  → 닿는데 크게 말하지 못한다 (표현력)
    reaches = rank_on <= 3 or z >= 2.0
    moves = lift > 0.02
    print()
    if moves:
        print("→ 성과가 갈렸다. 정책은 정답을 **쓰고 있다**.")
    elif reaches:
        print(f"→ 정답은 정책에 **닿는다** (순위 {rank_on}위 · z {z:+.2f}).")
        print("   그런데 성과가 안 갈렸다 — 배선이 아니라 **표현력**이다.")
        print("   정책이 무엇이 좋은지는 알지만 충분히 크게 말하지 못한다.")
        print("   볼 곳: α 출력 스케일 · 학습률 · 그래디언트 스텝 수.")
    else:
        print("→ 정답이 정책에 닿지도 않는다 — **배선**이다.")
        print("   docs/design/rl-training.md §5 원인 목록 순서대로.")

    passed = ratio >= CONTRIBUTION_RATIO
    print(f"\n판정: {'합격' if passed else '불합격'}")
    if not passed:
        # 어디를 볼지는 위 진단이 이미 갈랐다. 여기서 또 "배선" 이라고 적으면
        # 두 문장이 다른 말을 하게 된다 — 실제로 그랬다.
        print("기여도가 합격선에 못 미친다. 위 진단이 가리키는 쪽부터 본다.")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
