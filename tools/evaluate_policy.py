"""학습한 정책을 **본 적 없는 구간**에서 재고, 학습 구간과 견준다 (과적합 점검).

    .venv/bin/python tools/evaluate_policy.py \
        --checkpoint data/rl_checkpoints/m4-main-s0-r2.pt

## 왜 필요한가

학습 로그의 보상·EV 는 **학습 구간 성적**이다. 이 환경은 같은 에피소드
시작점을 평균 714번 반복해서 보므로(rl-training.md §과적합 점검), 그 성적이
"외운 것" 일 수 있다. 외웠는지 아닌지는 **안 본 구간**에서만 갈린다.

## 같은 자로 잰다

OOS 구간은 36거래일뿐인데 학습 에피소드는 250일이라 한 판이 안 들어간다
(`env.py` 가 그걸 명시적으로 거부한다). 그래서 **에피소드를 짧게 잡고, 학습
구간도 같은 길이로 다시 잰다.** 두 숫자를 같은 자로 재지 않으면 격차가
과적합 때문인지 에피소드 길이 때문인지 말할 수 없다.

## 대조군을 같이 돌린다

정책 성적만으로는 "좋다/나쁘다" 를 못 말한다. 같은 구간·같은 환경에서
**균등가중**을 돌려 견준다. 정책이 균등가중을 못 이기면 그 정책은 쓸 이유가
없다 — M4 완료 기준의 "룰 베이스라인 대비 우위" 가 이것이다.

**성과는 보상 합으로 본다.** 보상은 이미 (초과수익 − 낙폭페널티 − 비용) 이라
우리가 최적화하려던 값 그 자체다.
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

from quant_rl_trading.allocator.env import EnvParams  # noqa: E402
from quant_rl_trading.allocator.policy import AllocatorPolicy, PolicyConfig  # noqa: E402
from quant_rl_trading.modelops.canary_vec import VecLatticeEnv  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402

#: 학습 구간(=본 것)과 평가 구간(=안 본 것). 학습은 2026-06-30 에서 끝났다.
TRAIN = (date(2025, 1, 2), date(2026, 6, 30))
OOS = (date(2026, 7, 1), date(2026, 8, 22))


def _policy_from(checkpoint: Path, obs: dict, device) -> AllocatorPolicy:
    state = torch.load(checkpoint, map_location=device, weights_only=False)
    policy = AllocatorPolicy(PolicyConfig(
        n_max=int(obs["mask"].shape[1]),
        n_asset_features=int(obs["assets"].shape[-1]),
        n_portfolio_features=int(obs["portfolio"].shape[-1]),
        n_delay_choices=3,
    )).to(device)
    policy.load_state_dict(state["policy"])
    policy.eval()
    print(f"체크포인트 {checkpoint.name} · 업데이트 {state.get('update')} · "
          f"시드 {state.get('seed')}", flush=True)
    return policy


def _run(
    store: Store, *, window: tuple[date, date], episode_days: int, steps: int,
    envs: int, policy: AllocatorPolicy | None, device, now: datetime, seed: int,
) -> dict[str, float]:
    """한 구간을 굴린다. ``policy`` 가 None 이면 균등가중 대조군이다.

    **정책은 평균 행동을 쓴다**(표본 추출 안 함). 평가에서 표본을 뽑으면
    같은 정책이 매번 다른 성적을 내서, 구간 차이인지 뽑기 운인지 갈리지 않는다.
    """
    base = EnvParams.from_store(
        store, as_of=datetime.combine(window[0], time(0, 0), tzinfo=UTC),
        hyper_as_of=now,
    )
    env = VecLatticeEnv(
        store=store, train_start=window[0], train_end=window[1], market="KR",
        n_envs=envs, oracle_leak=False, seed=seed,
        params=replace(base, episode_days=episode_days), hyper_as_of=now,
    )
    obs = env.reset()
    n_slots = int(obs["mask"].shape[1])
    rewards: list[float] = []
    cash: list[float] = []
    reflection: list[float] = []

    for _ in range(steps):
        if policy is None:
            # 균등가중: 살아 있는 칸에 고르게. 현금 칸은 비운다.
            weights = np.zeros((envs, n_slots + 1), dtype=np.float32)
            for i in range(envs):
                live = np.where(obs["mask"][i])[0]
                if live.size:
                    weights[i, live] = 1.0 / live.size
                else:
                    weights[i, -1] = 1.0
        else:
            with torch.no_grad():
                out = policy(
                    torch.as_tensor(obs["portfolio"], dtype=torch.float32),
                    torch.as_tensor(obs["assets"], dtype=torch.float32),
                    torch.as_tensor(obs["mask"], dtype=torch.bool),
                )
                # Dirichlet 의 평균 = α / Σα.
                alpha = out.concentration
                weights = (alpha / alpha.sum(-1, keepdim=True)).cpu().numpy().astype(np.float32)
        obs, reward, _, _, info = env.step(weights)
        rewards.append(float(np.mean(reward)))
        cash.append(float(np.mean(info["cash_weight"])) if "cash_weight" in info else float("nan"))
        reflection.append(float(np.mean(info["action_reflection_rate"]))
                          if "action_reflection_rate" in info else float("nan"))
    arr = np.asarray(rewards)
    return {
        "reward_mean": float(arr.mean()),
        "reward_sum": float(arr.sum()),
        "reward_std": float(arr.std()),
        "cash": float(np.nanmean(cash)),
        "reflection": float(np.nanmean(reflection)),
        "steps": float(len(arr)),
    }


def _line(label: str, r: dict[str, float]) -> str:
    return (f"  {label:22s} 보상평균 {r['reward_mean']:+.5f} · 합 {r['reward_sum']:+.4f} "
            f"· 현금 {r['cash']:.3f} · 반영률 {r['reflection']:.3f}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True, type=Path)
    parser.add_argument("--root", default="data")
    parser.add_argument("--episode-days", type=int, default=20,
                        help="OOS 가 36거래일뿐이라 250일 에피소드가 안 들어간다")
    parser.add_argument("--steps", type=int, default=30)
    parser.add_argument("--envs", type=int, default=4)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)

    store = Store(root=Path(args.root))
    now = datetime.now(UTC)  # invariant-allow: wallclock
    device = torch.device("cpu")

    probe = VecLatticeEnv(
        store=store, train_start=OOS[0], train_end=OOS[1], market="KR",
        n_envs=1, oracle_leak=False, seed=args.seed,
        params=replace(
            EnvParams.from_store(
                store, as_of=datetime.combine(OOS[0], time(0, 0), tzinfo=UTC),
                hyper_as_of=now),
            episode_days=args.episode_days),
        hyper_as_of=now,
    )
    policy = _policy_from(args.checkpoint, probe.reset(), device)
    del probe

    print(f"\n에피소드 {args.episode_days}일 · 스텝 {args.steps} · env {args.envs} "
          f"· **두 구간을 같은 자로 잰다**\n")
    out: dict[str, dict[str, float]] = {}
    for name, window in (("학습구간(본 것)", TRAIN), ("OOS(안 본 것)", OOS)):
        for who, pol in (("정책", policy), ("균등가중", None)):
            key = f"{name}·{who}"
            out[key] = _run(
                store, window=window, episode_days=args.episode_days,
                steps=args.steps, envs=args.envs, policy=pol, device=device,
                now=now, seed=args.seed,
            )
            print(_line(key, out[key]), flush=True)

    print("\n판정")
    train_gap = out["학습구간(본 것)·정책"]["reward_mean"] - out["학습구간(본 것)·균등가중"]["reward_mean"]
    oos_gap = out["OOS(안 본 것)·정책"]["reward_mean"] - out["OOS(안 본 것)·균등가중"]["reward_mean"]
    print(f"  균등가중 대비 우위  학습 {train_gap:+.5f} · OOS {oos_gap:+.5f}")
    if oos_gap > 0 and train_gap > 0:
        print("  → 두 구간 다 균등가중을 이긴다. 일반화의 증거다.")
    elif train_gap > 0 >= oos_gap:
        print("  → **학습 구간에서만 이긴다 — 과적합의 정의다.**")
    else:
        print("  → 학습 구간에서도 균등가중을 못 이긴다. 과적합 이전에 학습이 안 됐다.")
    print("\n  ※ OOS 는 36거래일뿐이라 판정력이 약하다. 이 표로 '조금 나빴다' 를")
    print("     결론 삼지 않는다 — 방향만 본다(카나리에서 배운 것).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
