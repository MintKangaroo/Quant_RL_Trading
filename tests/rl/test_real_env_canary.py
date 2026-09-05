"""오라클 카나리 — **실제 환경 판**. `rl-training.md §0`, M4-kickoff 4-4.

**이 파일은 영구 보존한다.** 학습 파이프라인을 고칠 때마다 재실행한다(§0).

## 합성 판이 통과하는데 왜 또 도는가

`test_oracle_canary.py` 는 `modelops/canary_env.py` 라는 **합성** 세계를 돈다.
그것이 증명한 것은 "PPO 루프가 산다" 하나뿐이다. §0 이 요구하는 관문은
창고·selector·체결·회계가 전부 붙은 `allocator/env.py` 쪽이고, 이 저장소에서
제일 자주 나는 결함은 **코드는 있는데 아무도 안 부르는 것**이라 두 판이 갈릴
수 있다.

두 판의 차이가 곧 진단이다:

- 합성 통과 · 실제 실패 → **환경 배선**이다. 시장이 아니다
- 둘 다 실패 → 학습 루프다 (§5 ①⑥)
- 둘 다 통과 → 배선은 살아 있다. C1 로 넘어가도 된다

그래서 **학습 루프를 새로 만들지 않았다.** `canary_ppo.train_canary` 에 환경만
바꿔 물린다(`modelops/canary_vec.py`) — 루프가 두 벌이면 위 표의 첫 줄을
"환경 차이" 라고 말할 근거가 사라진다.

## 대조군이 왜 같이 있는가

오라클을 끄면 **같은 코드가 EV 0 근처에 머물러야 한다.** 이 짝이 없으면
"무엇을 넣어도 EV 가 높게 나오는 고장" 이 합격으로 찍힌다.

## 2026-08-19 첫 실행 — **불합격**

| 판 | EV(마지막 5중앙값) | 오라클 기여도 순위 |
|---|---|---|
| 오라클 켬 | 0.606 | 2위 / 28 |
| 대조군 | **0.903** | 27위 / 28 |

대조군이 정답 없이 더 잘 설명한다 — 이 환경에서 EV 는 판정력이 없다. 보상이
현금 드래그(평균 현금 59%)와 낙폭으로 설명되기 때문이다. 배선이 끊긴 것은
아니다: 오라클 칸의 정책 그래디언트가 대조군의 68배다.

**아래 시험들은 지금 빨갛다. 그것이 이 파일의 일이다** — §0 은 이 관문이
초록이 되기 전에 4-5 로 넘어가지 말라고 한다. 원인 배제 기록과 사람이 정해야
할 것은 `docs/rl-diagnosis.md` 에 있다.

## 돌리는 법

    OMP_NUM_THREADS=1 QUANT_RL_DUCKDB_MEMORY_LIMIT=1200MB \\
        uv run pytest tests/rl/test_real_env_canary.py -m slow -s

한 판에 40분 남짓이고 대조군까지 두 판이다. `OMP_NUM_THREADS=1` 은 취향이
아니라 실측이다 — 행렬이 작아 BLAS 멀티스레드가 경합만 만든다(35초 → 17초).
"""

from __future__ import annotations

import warnings
from collections.abc import Callable
from datetime import date
from pathlib import Path

import numpy as np
import pytest

from quant_rl_trading.allocator import cache as cache_module
from quant_rl_trading.allocator.env import FEATURE_ORACLE
from quant_rl_trading.modelops.canary_ppo import (
    CanaryResult,
    IterationLog,
    PPOConfig,
    train_canary,
)
from quant_rl_trading.modelops.canary_vec import VecLatticeEnv
from quant_rl_trading.store import Store

#: 학습 구간. **RL 세션 캐시가 덮는 범위 안에서** 잡는다 — 밖으로 나가면 그
#: 세션만 창고 경로로 떨어져 스텝이 20배 느려지고, 증상은 "학습이 안 끝난다"
#: 로만 보인다. 검증·테스트 구간은 walk-forward(§8) 가 따로 자른다.
TRAIN_START = date(2024, 9, 2)
TRAIN_END = date(2026, 7, 3)

#: §0 의 원문 예산. **줄이지 않는다** — 줄이면 배선이 아니라 예산을 재게 된다.
STEPS = 200_000
PASS_EV = 0.5
N_ENVS = 16


def _cache_root() -> Path:
    return cache_module.default_cache_root(Store())


warehouse_missing = pytest.mark.skipif(
    not _cache_root().exists(),
    reason=f"RL 세션 캐시가 없다({_cache_root()}). tools/build_rl_cache.py 를 먼저 돌려라",
)


def run_real_canary(
    *,
    oracle_leak: bool,
    steps: int = STEPS,
    seed: int = 0,
    n_envs: int = N_ENVS,
    on_iteration: Callable[[IterationLog], None] | None = None,
) -> CanaryResult:
    """실제 환경으로 카나리 한 판. **테스트와 수동 실행이 같은 함수를 부른다.**

    보고서에 적힌 숫자와 테스트가 다시 재는 숫자가 다른 함수에서 나오면, 그
    둘이 갈렸을 때 어느 쪽이 맞는지 말할 수 없다.

    보상 계수는 `EnvParams` 가 창고 설정에서 읽은 것을 그대로 쓴다(불변식 10).
    카나리가 자기 값을 따로 들면 학습이 배우는 벌점과 실전의 벌점이 갈린다.
    """
    store = Store()
    with warnings.catch_warnings():
        # oracle_leak=True 는 RuntimeWarning 을 띄운다. 그것이 뜨는지는 아래
        # 별도 시험이 보고, 여기서는 배너 때문에 -W error 가 걸리지 않게 한다.
        warnings.simplefilter("ignore", RuntimeWarning)
        env = VecLatticeEnv(
            store,
            train_start=TRAIN_START,
            train_end=TRAIN_END,
            n_envs=n_envs,
            oracle_leak=oracle_leak,
            use_cache=True,
            seed=seed,
        )
    return train_canary(
        reward_params=env.envs[0].params.reward,
        ppo=PPOConfig(total_timesteps=steps, num_envs=n_envs),
        env=env,
        seed=seed,
        on_iteration=on_iteration,
    )


def _progress(tag: str) -> Callable[[IterationLog], None]:
    """긴 판이 살아 있는지 보이게 한다. `-s` 로 돌릴 때만 화면에 뜬다."""

    def report(log: IterationLog) -> None:
        print(
            f"[{tag}] step={log.step:>7} EV={log.explained_variance:+.3f} "
            f"kl={log.approx_kl:.4f} ent={log.entropy:+.2f} "
            f"r={log.reward_mean:+.4f} dd={log.drawdown_mean:.3f} "
            f"reflect={log.action_reflection_rate:.2f} turn={log.turnover:.3f}",
            flush=True,
        )

    return report


@pytest.fixture(scope="module")
def leaked() -> CanaryResult:
    return run_real_canary(oracle_leak=True, on_iteration=_progress("leak"))


@pytest.fixture(scope="module")
def control() -> CanaryResult:
    return run_real_canary(oracle_leak=False, on_iteration=_progress("ctrl"))


@pytest.mark.slow
@warehouse_missing
def test_실제환경에서_가치함수가_설명한다(leaked: CanaryResult) -> None:
    """§0 합격 기준 ①. 마지막 5업데이트 중앙값 — 한 번 튄 값으로 합격을 주지
    않는다."""
    final = leaked.final_explained_variance

    assert final > PASS_EV, (
        f"EV {final:.3f} — 실제 환경의 배선이 끊겼다. 합성 판이 통과하는데 "
        "여기서 떨어지면 시장이 아니라 env·selector·체결·회계 어딘가다. "
        "rl-training.md §5 를 ①부터 순서대로 배제하고 docs/rl-diagnosis.md 에 "
        "기록해라. **불합격 상태로 4-5 로 넘어가지 마라**"
    )


@pytest.mark.slow
@warehouse_missing
def test_정책이_오라클_피처를_실제로_쓴다(leaked: CanaryResult) -> None:
    """§0 합격 기준 ②. **가치함수가 아니라 정책의** 그래디언트다.

    가치는 배웠는데 액션이 안 바뀌는 고장이 실제로 일어난다 — 그때 액션
    반영률이 0 이 되고, 그게 선행 프로젝트가 죽은 방식이다(README).
    """
    attribution = leaked.attribution
    assert attribution is not None
    order = np.argsort(-attribution)

    assert int(order[0]) == FEATURE_ORACLE, (
        f"오라클({FEATURE_ORACLE}) 이 최상위가 아니다. 순위 {list(map(int, order[:3]))}"
    )


@pytest.mark.slow
@warehouse_missing
def test_학습이_실제로_올라간다(leaked: CanaryResult) -> None:
    """처음부터 높았던 EV 는 학습의 증거가 아니다. 상수 보상·죽은 어드밴티지도
    그렇게 보인다."""
    early = float(np.median([log.explained_variance for log in leaked.logs[:3]]))
    late = float(np.median([log.explained_variance for log in leaked.logs[-3:]]))

    assert late > early + 0.3


@pytest.mark.slow
@warehouse_missing
def test_액션_반영률이_바닥이_아니다(leaked: CanaryResult) -> None:
    """§5 ② — 30% 미만이면 시장이 아니라 안전장치가 액션을 덮은 것이다.

    합성 환경에는 이 값이 늘 1.0 이라(마찰 없음) 이 시험은 **실제 환경에만
    있다.** 여기가 깨지면 EV 가 통과해도 그 정책은 자기 결정이 집행되지 않는
    세계를 배운 것이다.
    """
    rates = [log.action_reflection_rate for log in leaked.logs]

    assert float(np.median(rates)) > 0.3, f"액션 반영률 중앙값 {np.median(rates):.2f}"


@pytest.mark.slow
@warehouse_missing
def test_오라클을_끄면_설명하지_못한다(control: CanaryResult) -> None:
    """대조군. 같은 코드·같은 스텝인데 정답만 뺐다.

    여기서도 EV 가 높게 나오면 카나리가 무엇도 판정하지 못하고 있는 것이다 —
    아무것도 하지 않는 구현이 "두 번 돌려 같다" 를 통과하는 것과 같은 함정이다.
    """
    assert control.final_explained_variance < PASS_EV

    attribution = control.attribution
    assert attribution is not None
    # 정답이 빠진 자리(섹터 칸)는 상수 0 이다. 최상위를 차지할 이유가 없다.
    assert attribution[FEATURE_ORACLE] < 2.0 * float(np.median(attribution))


@warehouse_missing
def test_기본값은_꺼져_있고_켜면_크게_경고한다() -> None:
    """실수로 실제 학습에 켜지면 성과가 통째로 가짜가 된다. **느리지 않다** —
    환경을 만들기만 하고 스텝은 돌지 않으므로 기본 실행에 남겨 둔다."""
    store = Store()
    plain = VecLatticeEnv(
        store, train_start=TRAIN_START, train_end=TRAIN_END, n_envs=1
    )
    assert plain.config.oracle_leak is False
    assert plain.envs[0].oracle_leak is False

    with pytest.warns(RuntimeWarning, match="oracle_leak=True"):
        VecLatticeEnv(
            store,
            train_start=TRAIN_START,
            train_end=TRAIN_END,
            n_envs=1,
            oracle_leak=True,
        )
