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
BREAKDOWN = ("excess_return", "drawdown_penalty", "cost", "turnover", "drawdown")


def _window(text: str) -> tuple[date, date]:
    start, end = text.split(":")
    return date.fromisoformat(start), date.fromisoformat(end)


def _policy_from(checkpoint: Path, obs: dict, device) -> tuple[AllocatorPolicy, dict]:
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
    meta = {
        "run_id": str(state.get("run_id") or checkpoint.stem),
        "update": state.get("update"),
        "seed": state.get("seed"),
        "market": str(state.get("market") or "KR"),
        # 3회차부터 체크포인트가 자기 환경 설계(현금 액션·warm start)를 들고 다닌다.
        # 평가는 **학습이 쓴 것과 같은 환경**이어야 한다 — 다른 환경에서 재면 정책이
        # 아니라 환경 차이를 재게 된다.
        "env_overrides": dict(state.get("env_overrides") or {}),
        "train_window": state.get("train_window"),
    }
    return policy, meta


def _run(
    store: Store, *, window: tuple[date, date], episode_days: int, steps: int,
    envs: int, policy: AllocatorPolicy | None, device, now: datetime, seed: int,
    env_overrides: dict | None = None,
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
        params=replace(base, episode_days=episode_days, **(env_overrides or {})), hyper_as_of=now,
    )
    obs = env.reset()
    n_slots = int(obs["mask"].shape[1])
    rewards: list[float] = []
    cash: list[float] = []
    parts: dict[str, list[float]] = {key: [] for key in BREAKDOWN}
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
        # 현금 = 1 − 실현 비중 합. 환경 info 에 현금 칸이 따로 없다.
        realized = np.asarray(info["realized_weights"], dtype=np.float64)
        cash.append(float(np.mean(1.0 - realized.reshape(realized.shape[0], -1).sum(-1))))
        reflection.append(float(np.mean(info["action_reflection_rate"]))
                          if "action_reflection_rate" in info else float("nan"))
        # §8 분해 — 합만 보면 "선택을 못 하나, 비용에 먹히나" 를 못 가른다.
        for key in BREAKDOWN:
            parts[key].append(float(np.mean(info[key])) if key in info else float("nan"))
    arr = np.asarray(rewards)
    return {
        "reward_mean": float(arr.mean()),
        "reward_sum": float(arr.sum()),
        "reward_std": float(arr.std()),
        "cash": float(np.nanmean(cash)),
        "reflection": float(np.nanmean(reflection)),
        "steps": float(len(arr)),
        **{key: float(np.nanmean(values)) for key, values in parts.items()},
    }


def _line(label: str, r: dict[str, float]) -> str:
    return (f"  {label:22s} 보상평균 {r['reward_mean']:+.5f} · 합 {r['reward_sum']:+.4f} "
            f"· 현금 {r['cash']:.3f} · 반영률 {r['reflection']:.3f}\n"
            f"  {'':22s} 초과수익 {r['excess_return']:+.5f} · 낙폭벌점 {r['drawdown_penalty']:+.5f} "
            f"· 비용 {r['cost']:+.5f} · 회전 {r['turnover']:.3f} · 낙폭 {r['drawdown']:.3f}")


def evaluate(argv: list[str] | None = None) -> dict:
    """평가를 돌리고 결과를 돌려준다. `main` 과 `select_checkpoint` 가 같이 쓴다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True, type=Path)
    parser.add_argument("--root", default="data")
    parser.add_argument("--episode-days", type=int, default=20,
                        help="OOS 가 36거래일뿐이라 250일 에피소드가 안 들어간다")
    parser.add_argument("--steps", type=int, default=30)
    parser.add_argument("--envs", type=int, default=4)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--save", action="store_true",
                        help="rl_evaluations 에 적재 — 학습 탭이 이 표를 읽는다")
    parser.add_argument("--train-window", default=None, help="YYYY-MM-DD:YYYY-MM-DD (기본 2025-01-02:2026-06-30)")
    parser.add_argument("--oos-window", default=None, help="YYYY-MM-DD:YYYY-MM-DD (기본 홀드아웃 2026-07-01:2026-08-22)")
    parser.add_argument("--oos-label", default="oos", help="rl_evaluations.eval_window 값 — 검증 폴드면 valid")
    parser.add_argument("--warm-start", dest="warm_start", action="store_true", default=None,
                        help="체크포인트 설정 대신 warm start 강제")
    parser.add_argument("--cash-action", choices=["free", "fixed"], default=None,
                        help="체크포인트 설정 대신 강제")
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
    policy, meta = _policy_from(args.checkpoint, probe.reset(), device)
    del probe
    overrides = dict(meta.get("env_overrides") or {})
    if args.warm_start is not None:
        overrides["warm_start"] = bool(args.warm_start)
    if args.cash_action is not None:
        overrides["cash_action"] = args.cash_action
    train_window = _window(args.train_window) if args.train_window else TRAIN
    oos_window = _window(args.oos_window) if args.oos_window else OOS
    oos_name = "검증폴드(안 본 것)" if args.oos_label == "valid" else "OOS(안 본 것)"
    print(f"환경: {overrides or '기본(free · 현금 출발)'} · 학습 {train_window[0]}~{train_window[1]} "
          f"· {args.oos_label} {oos_window[0]}~{oos_window[1]}")

    print(f"\n에피소드 {args.episode_days}일 · 스텝 {args.steps} · env {args.envs} "
          f"· **두 구간을 같은 자로 잰다**\n")
    out: dict[str, dict[str, float]] = {}
    for name, window in (("학습구간(본 것)", train_window), ("OOS(안 본 것)", oos_window)):
        for who, pol in (("정책", policy), ("균등가중", None)):
            key = f"{name}·{who}"
            out[key] = _run(
                store, window=window, episode_days=args.episode_days,
                steps=args.steps, envs=args.envs, policy=pol, device=device,
                now=now, seed=args.seed, env_overrides=overrides,
            )
            print(_line(key.replace("OOS(안 본 것)", oos_name), out[key]), flush=True)

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

    if args.save:
        verdict = (
            "generalizes" if oos_gap > 0 and train_gap > 0
            else "overfit" if train_gap > 0 >= oos_gap
            else "untrained"
        )
        rows = []
        for (label, window_key), gap in (
            (("학습구간(본 것)", "train"), train_gap), (("OOS(안 본 것)", args.oos_label), oos_gap)
        ):
            for who, arm in (("정책", "policy"), ("균등가중", "equal")):
                r = out[f"{label}·{who}"]
                rows.append({
                    "entity_id": meta["run_id"], "valid_from": now, "observed_at": now,
                    "source": "evaluate_policy",
                    "eval_window": window_key, "arm": arm,
                    "episode_days": args.episode_days, "envs": args.envs,
                    "steps": args.steps, "eval_seed": args.seed,
                    "train_seed": int(meta["seed"]) if meta["seed"] is not None else None,
                    "market": meta["market"],
                    "reward_mean": r["reward_mean"], "reward_sum": r["reward_sum"],
                    "reward_std": r["reward_std"], "cash_weight": r["cash"],
                    "action_reflection": r["reflection"], "cost": r["cost"],
                    "turnover": r["turnover"], "drawdown": r["drawdown"],
                    "gap_vs_equal": gap if arm == "policy" else None,
                    "verdict": verdict if arm == "policy" else None,
                    "checkpoint": str(args.checkpoint),
                    "update": int(meta["update"]) if meta["update"] is not None else None,
                })
        store.append("rl_evaluations", rows, ingest_run_id=f"evaluate-{meta['run_id']}-{now:%Y%m%d%H%M%S}")
        print(f"\n  rl_evaluations 적재 {len(rows)}행 · run {meta['run_id']} · 판정 {verdict}")
    return {"train_gap": train_gap, "oos_gap": oos_gap, "out": out, "meta": meta}


def main(argv: list[str] | None = None) -> int:
    evaluate(argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
