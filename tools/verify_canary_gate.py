"""오라클 카나리 게이트 — **본 학습을 시작해도 되는가** (M4 전제).

    .venv/bin/python tools/verify_canary_gate.py

## 왜 게이트를 바꿨나 (2026-08-23)

옛 게이트는 "오라클 칸 기여도 ≥ 대조군 10배" 였다. **그 기준은 무효다.**

    2026-08-20  기여도 11.9배로 합격 — 그런데 NAV 는 안 갈렸다
    2026-08-21  갈라서 재니 그 기여도는 **지연 머리**에서 왔다
                (비중 항 26위/28 · 인과 탐침 -0.00002 ≈ 0)

지연은 NAV 를 거의 안 바꾼다. 즉 **통과해도 아무것도 보장하지 않았다.**

더 나쁜 것은 그 다음이다. "정책이 눈에 띄게 집중해야 한다" 로 바꿔 재 봤더니
일곱 가설(κ·KL·엔트로피·구조·집행·보상·표본)을 폐기하도록 만들었는데,
마지막에 드러난 원인은 **게이트가 요구한 것이 그 예산에서 원리적으로
측정 불가능**하다는 것이었다:

    advantage ↔ 행동의 오라클 정렬도 상관  r ≈ 0.043 (t 3.31, 표본 6,048)
    r=0.043 로 학습하는 데 필요한 그래디언트 스텝  ≈ 110,000
    그때까지 카나리가 준 예산                        768   ← 144배 부족

**필요한 것의 0.7% 를 주고 "못 배운다" 고 판정해 온 것이다.**

## 그래서 무엇을 재는가 — 싸게 잴 수 있는 **필요조건 셋**

학습이 되려면 세 고리가 다 살아 있어야 한다. 셋 다 작은 예산에서 잴 수 있고,
하나라도 죽어 있으면 아무리 오래 돌려도 안 배운다.

    ① 환경    정답을 따르면 실제로 보상이 더 큰가        (없으면 배울 것이 없다)
    ② 용량    이 정책망이 그 매핑을 배울 수 있는가        (없으면 못 담는다)
    ③ 신용    그 보상이 advantage 까지 도달하는가         (없으면 신호가 안 온다)

**셋이 통과하면 본 학습으로 간다.** 그다음 "정말 배우나" 는 카나리가 아니라
학습 자체가 답한다 — 그리고 그 판정은 milestones.md 의 "3회 실패 시 룰
베이스라인으로 회귀" 규칙이 맡는다. 카나리가 학습의 대역을 서려 한 것이
애초의 잘못이었다.

**이 셋은 필요조건이지 충분조건이 아니다.** 통과가 "RL 이 돈을 번다" 를
뜻하지 않는다. 뜻하는 것은 오직 "학습을 시작할 자격이 있다" 뿐이다.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import torch  # noqa: E402

from quant_rl_trading.allocator.env import FEATURE_ORACLE  # noqa: E402
from quant_rl_trading.allocator.policy import AllocatorPolicy, PolicyConfig  # noqa: E402
from quant_rl_trading.modelops.canary_vec import OnlyOracleEnv, VecLatticeEnv  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402

#: ① 환경 — 오라클 상위/하위에 실었을 때 누적 보상 차이의 하한.
#: 2026-08-23 실측 0.171 (상위 +0.039 · 하위 -0.132, 10스텝).
ENV_REWARD_GAP = 0.02

#: ② 용량 — 지도학습으로 정답 칸에 실어야 하는 비중. 균등은 1/24 ≈ 0.042.
#: 실측: 200스텝 0.967 · 400스텝 0.993 (lr 3e-5, 본 학습과 같은 규모).
CAPACITY_TARGET = 0.50
CAPACITY_STEPS = 200

#: ③ 신용 — advantage ↔ 정렬도 상관의 t 하한. **크기가 아니라 t 다** —
#: r 0.05 는 표본 100 이면 잡음이고 10,000 이면 확실한 신호다.
CREDIT_T = 2.0

#: 세 검사에 쓰는 표본. ③ 은 t 를 내야 하므로 넉넉해야 한다
#: (r≈0.043 에서 t=2 를 넘기려면 1,418개).
ENVS = 12
N_STEPS = 512
HOLD_STEPS = 10


def _build_env(store: Store, *, market: str, n_envs: int, now: datetime):
    env = VecLatticeEnv(
        store=store, train_start=date(2025, 1, 2), train_end=date(2026, 6, 30),
        market=market, n_envs=n_envs, oracle_leak=True, seed=0, params=None,
        hyper_as_of=now,
    )
    return OnlyOracleEnv(env, FEATURE_ORACLE)


def check_environment(store: Store, *, market: str, now: datetime) -> tuple[bool, str]:
    """① 정답을 따르면 보상이 더 큰가. **없으면 배울 것이 자체가 없다.**

    오라클 상위 6종목 균등 / 하위 6종목 균등으로 ``HOLD_STEPS`` 를 굴려 누적
    보상을 견준다. 한 스텝만 보면 안 된다 — 오라클은 5일 뒤를 말하는데 보상은
    매일 나눠 들어와서, 1스텝 차이는 2.9e-5 로 0 처럼 보인다(실측).
    """
    totals = {}
    for mode in ("top", "bottom"):
        env = _build_env(store, market=market, n_envs=1, now=now)
        obs = env.reset()
        n = obs["mask"].shape[1]
        total = 0.0
        for _ in range(HOLD_STEPS):
            oracle = obs["assets"][0, :, FEATURE_ORACLE]
            live = np.where(obs["mask"][0])[0]
            order = np.argsort(-oracle[live]) if mode == "top" else np.argsort(oracle[live])
            pick = live[order[:6]]
            weights = np.zeros((1, n + 1), dtype=np.float32)
            weights[0, pick] = 1.0 / len(pick)
            obs, reward, _, _, _ = env.step(weights)
            total += float(np.asarray(reward).reshape(-1)[0])
        totals[mode] = total
    gap = totals["top"] - totals["bottom"]
    ok = gap >= ENV_REWARD_GAP
    return ok, (f"상위 {totals['top']:+.4f} · 하위 {totals['bottom']:+.4f} · "
                f"차이 {gap:+.4f} (하한 {ENV_REWARD_GAP})")


def check_capacity() -> tuple[bool, str]:
    """② 이 정책망이 "정답 칸에 실어라" 를 배울 수 있는가.

    **창고를 안 탄다.** 완벽한 신호(지도학습)를 주고 본 학습과 같은 학습률로
    돌린다. 여기서 못 배우면 강화학습 이전에 표현력·최적화 문제다.
    """
    torch.manual_seed(0)
    n_slots, n_features = 24, 20
    policy = AllocatorPolicy(PolicyConfig(
        n_max=n_slots, n_asset_features=n_features, n_portfolio_features=8,
        n_delay_choices=3, concentration_mode="simplex", concentration_total=None,
    ))
    optimizer = torch.optim.Adam(policy.parameters(), lr=3e-5)
    generator = torch.Generator().manual_seed(1)
    weight_on_answer = 0.0
    for _ in range(CAPACITY_STEPS):
        batch = 64
        answer = torch.randint(0, n_slots, (batch,), generator=generator)
        assets = torch.zeros(batch, n_slots, n_features)
        assets[torch.arange(batch), answer, 0] = 1.0
        out = policy(torch.zeros(batch, 8), assets, torch.ones(batch, n_slots, dtype=torch.bool))
        share = out.concentration / out.concentration.sum(-1, keepdim=True)
        picked = share[torch.arange(batch), answer]
        loss = -(picked + 1e-12).log().mean()
        weight_on_answer = float(picked.mean())
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
    ok = weight_on_answer >= CAPACITY_TARGET
    return ok, (f"{CAPACITY_STEPS}스텝 뒤 정답 칸 비중 {weight_on_answer:.3f} "
                f"(균등 {1 / n_slots:.3f} · 하한 {CAPACITY_TARGET})")


def check_credit(store: Store, *, market: str, now: datetime) -> tuple[bool, str]:
    """③ 환경의 보상이 **advantage 까지** 도달하는가.

    정책 그래디언트는 ``E[A·∇log π]`` 다. A 가 행동의 좋고 나쁨과 무관하면
    그 기댓값은 0 이고, 신호가 환경에 있어도 학습은 못 한다.
    """
    from dataclasses import replace

    from quant_rl_trading.allocator import train as train_module
    from quant_rl_trading.allocator.reward import ReturnNormalizer
    from tools.diagnose_allocation import advantage_alignment

    device = torch.device("cpu")
    torch.manual_seed(0)
    ppo = replace(
        train_module.train_config(), num_envs=ENVS, n_steps=N_STEPS,
        minibatch_size=512, n_epochs=4, lr_policy=3e-5, lr_value=9e-5,
    )
    env = _build_env(store, market=market, n_envs=ENVS, now=now)
    obs = env.reset()
    policy = AllocatorPolicy(PolicyConfig(
        n_max=int(obs["mask"].shape[1]),
        n_asset_features=int(obs["assets"].shape[-1]),
        n_portfolio_features=int(obs["portfolio"].shape[-1]),
        n_delay_choices=3, concentration_mode="simplex",
    )).to(device)
    normalizer = ReturnNormalizer(gamma=ppo.gamma, num_envs=ENVS)
    rollout = train_module.collect(
        env, policy, obs, ppo=ppo, device=device, normalizer=normalizer,
    )
    stats = advantage_alignment(rollout, ppo=ppo, device=device)
    r, n = stats["corr"], stats["n"]
    if not np.isfinite(r) or n < 30:
        return False, f"표본 부족 (n={n:.0f})"
    t = abs(r) * ((n - 2) ** 0.5) / max((1 - r * r) ** 0.5, 1e-12)
    ok = (t >= CREDIT_T) and (r > 0)
    return ok, f"상관 {r:+.4f} · t {t:.2f} · 표본 {n:.0f} (하한 t {CREDIT_T})"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", default="KR")
    parser.add_argument("--root", default="data")
    parser.add_argument(
        "--skip", nargs="*", default=[], choices=["env", "capacity", "credit"],
        help="건너뛸 검사. **결과에 '건너뜀' 으로 남는다** — 통과로 세지 않는다.",
    )
    args = parser.parse_args(argv)

    store = Store(root=Path(args.root))
    now = datetime.now(UTC)  # invariant-allow: wallclock
    print("오라클 카나리 게이트 — 본 학습을 시작해도 되는가 (M4 전제)\n")

    checks = [
        ("① 환경  정답을 따르면 보상이 더 큰가",
         "env", lambda: check_environment(store, market=args.market, now=now)),
        ("② 용량  정책망이 그 매핑을 배울 수 있는가",
         "capacity", check_capacity),
        ("③ 신용  보상이 advantage 까지 도달하는가",
         "credit", lambda: check_credit(store, market=args.market, now=now)),
    ]
    results: list[bool | None] = []
    for label, key, run in checks:
        if key in args.skip:
            print(f"[건너뜀] {label}")
            results.append(None)
            continue
        try:
            ok, detail = run()
        except Exception as exc:  # 한 검사가 죽어도 나머지는 본다
            print(f"[오류] {label}\n       {type(exc).__name__}: {exc}")
            results.append(False)
            continue
        print(f"[{'PASS' if ok else 'FAIL'}] {label}\n       {detail}")
        results.append(ok)

    passed = sum(1 for r in results if r is True)
    skipped = sum(1 for r in results if r is None)
    failed = sum(1 for r in results if r is False)
    print(f"\nPASS {passed} · FAIL {failed} · 건너뜀 {skipped} / 3")
    if failed:
        print("→ 본 학습을 시작하지 않는다. 죽은 고리를 먼저 살린다.")
        return 1
    if skipped:
        print("→ 건너뛴 검사가 있다. 전부 통과해야 자격이 생긴다.")
        return 2
    print("→ **세 고리가 다 살아 있다. 본 학습을 시작할 자격이 있다.**")
    print("   이것은 필요조건이다 — 'RL 이 돈을 번다' 를 뜻하지 않는다.")
    print("   실제로 배우는지는 학습이 답하고, 3회 실패하면 룰로 돌아간다")
    print("   (docs/milestones.md M4 중단 기준).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
