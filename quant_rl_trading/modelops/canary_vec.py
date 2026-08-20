"""실제 환경(`allocator/env.py`)을 카나리 학습 루프에 물리는 벡터 어댑터.

## 왜 어댑터인가 — PPO 를 두 벌 만들지 않기 위해

합성 카나리(`canary_env.py`)는 이미 통과한다. 그것이 증명한 것은 "PPO 루프가
산다" 뿐이고, §0 이 요구하는 것은 **창고·selector·체결·회계가 붙은 진짜
환경**에서 같은 판정을 내는 것이다. 그때 PPO 를 새로 쓰면, 합성 판과 실제
판의 차이가 **환경 차이인지 루프 차이인지 못 가른다** — 이 시험의 존재 이유가
바로 그 구분이라 그 순간 시험이 무의미해진다.

그래서 학습 루프는 `canary_ppo.train_canary` 그대로 쓰고, 이 파일은 모양만
맞춘다: 단일 `LatticeEnv` 16개를 벡터처럼 보이게 하고, 끝난 에피소드를 다시
뽑는다.

## 액션에서 지연·환전은 0 이다

카나리 정책(`canary_policy`)은 비중 하나만 낸다. 지연 0 = 다음 세션 체결이고,
이는 커리큘럼 C1~C2 의 설정 그대로다(§6 — 진입 지연은 C3 에서 켠다). 환전은
환경 자체가 아직 하지 않는다(`LatticeEnv` 독스트링). **여기서 임의로 지연을
섞으면** 오라클이 알려주는 5일 지평과 체결 시점이 어긋나서, 정답을 봐도 못
맞히는 상태가 된다 — 그것은 배선 고장과 구분되지 않는다.

## 절단 관측은 환경이 주는 대로 넘긴다

`LatticeEnv` 는 종료·절단 스텝에서 `_blank()`(0 관측)을 돌려준다. 그러면
250일 절단의 부트스트랩이 "0 관측의 가치" 가 된다 — 합성 판이 진짜 마지막
관측을 넘기는 것과 다르다. **여기서 몰래 메꾸지 않는다.** 메꾸면 학습 루프가
보는 세계와 환경이 실제로 주는 세계가 갈라지고, 그 갈라짐은 성적표에 안
보인다. 250스텝에 한 번이라 영향은 작고, 실제 학습(4-5)에서 고칠지는 사람이
정할 일이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import numpy.typing as npt

from quant_rl_trading.allocator import cache as cache_module
from quant_rl_trading.allocator.env import (
    N_ASSET_FEATURES,
    N_PORTFOLIO_FEATURES,
    EnvParams,
    LatticeEnv,
)

if TYPE_CHECKING:
    from quant_rl_trading.store import Store

Array = npt.NDArray[np.float64]
Obs = dict[str, Array]
BoolArray = npt.NDArray[np.bool_]
StepResult = tuple[Obs, Array, BoolArray, BoolArray, dict[str, Any]]


@dataclass(frozen=True)
class VecConfig:
    """`CanaryConfig` 와 **같은 이름의 필드만** 둔다.

    학습 루프가 `env.config.n_envs` 처럼 이름으로 집어 가므로, 여기 이름이
    갈라지면 루프를 고쳐야 하고 그러면 두 벌이 된다.
    """

    n_envs: int
    n_assets: int
    n_asset_features: int = N_ASSET_FEATURES
    n_portfolio_features: int = N_PORTFOLIO_FEATURES
    oracle_leak: bool = False


class VecLatticeEnv:
    """`LatticeEnv` n개를 벡터 환경처럼 보이게 한다.

    창고는 **한 벌만 공유한다.** 16벌을 열면 DuckDB 연결도 16개가 되고, 램
    4GB 짜리 기계에서 그것만으로 학습이 죽는다. 환경끼리 공유하는 것은 읽기
    전용 창고와 설정뿐이라, 궤적은 서로 독립이다.
    """

    def __init__(
        self,
        store: Store,
        *,
        train_start: date,
        train_end: date,
        n_envs: int = 16,
        market: str = "KR",
        oracle_leak: bool = False,
        use_cache: bool = True,
        cache_root: Path | None = None,
        params: EnvParams | None = None,
        cache_pool: int = 512,
        seed: int = 0,
        #: 학습 설계값을 읽을 시점. 안 주면 학습 구간 첫날이라 오늘 바꾼
        #: 설정을 못 본다 (`EnvParams.from_store` 독스트링).
        hyper_as_of: datetime | None = None,
    ) -> None:
        def build(shared: EnvParams | None) -> LatticeEnv:
            return LatticeEnv(
                store,
                train_start=train_start,
                train_end=train_end,
                market=market,
                params=shared,
                oracle_leak=oracle_leak,
                use_cache=use_cache,
                cache_root=cache_root,
                hyper_as_of=hyper_as_of,
            )

        # 설정은 **한 번만** 읽어 전 환경이 나눠 쓴다. 16번 읽으면 같은 답을
        # 16번 사고(창고 조회 수십 회), 그 사이에 값이 갈릴 이유도 없다.
        first = build(params)
        shared = first.params
        self.envs = [first] + [build(shared) for _ in range(n_envs - 1)]

        # 세션 리더도 한 벌만 쓴다. `SessionReader` 는 스스로 "as_of 는 인자지
        # 상태가 아니다" 를 계약으로 걸어 두었고(그 클래스 독스트링), 들고 있는
        # 상태는 as_of 를 키로 확인하고 쓰는 메모뿐이라 환경끼리 섞여도 값이
        # 갈리지 않는다. 대신 **적재 창이 16개 환경에 공유된다** — 16개가 서로
        # 다른 날을 걷지만 결국 같은 세션들을 되풀이해 밟으므로, 창 하나가
        # 환경 수만큼의 파일 파싱을 지운다(실측 35.7ms → 13.7ms).
        reader = cache_module.build_reader(
            store,
            market,
            cache_root=cache_root,
            use_cache=use_cache,
            # 창을 학습 구간의 세션 수보다 넉넉히 잡는다. 좁으면 16개
            # 환경의 앞머리가 서로를 밀어내서 같은 파일을 몇 번이고 다시
            # 판다 — 실측으로 64스텝에서 스텝 비용이 두 배가 됐다. 세션
            # 하나가 수백 KB 라 2년치를 전부 들어도 램 반 GB 안쪽이다.
            lru=cache_pool,
        )
        for env in self.envs:
            env.reader = reader
        self.reader = reader
        self.config = VecConfig(
            n_envs=n_envs, n_assets=shared.n_max, oracle_leak=oracle_leak
        )
        self._seed = seed
        self._delay = np.zeros(shared.n_max, dtype=np.int64)
        self._fx = np.zeros(1, dtype=np.float32)
        #: 에피소드가 몇 번 새로 뽑혔나. 진행 로그에 찍어 두면 "학습이 도는데
        #: 에피소드가 하나도 안 끝났다" 는 고장이 눈에 보인다.
        self.episodes = 0

    # -- 관측 -----------------------------------------------------------------

    @staticmethod
    def _stack(obs_list: list[dict[str, Any]]) -> Obs:
        return {
            "assets": np.stack([obs["assets"] for obs in obs_list]).astype(np.float64),
            "portfolio": np.stack([obs["portfolio"] for obs in obs_list]).astype(
                np.float64
            ),
            "mask": np.stack([obs["mask"] for obs in obs_list]).astype(bool),
        }

    def reset(self) -> Obs:
        """환경마다 **다른 시드**로 첫 에피소드를 뽑는다.

        같은 시드를 주면 16개가 같은 구간을 걷는다 — 배치가 16배 커진 것이
        아니라 같은 표본을 16번 센 것이 되고, 그러면 어드밴티지 분산이 실제보다
        작게 보인다.

        ``reset`` 이후의 재시작에는 시드를 넘기지 않는다. `LatticeEnv.reset` 은
        시드를 받으면 난수기를 **다시 만들어서**, 매번 같은 시작일이 뽑힌다.
        """
        obs_list = [
            env.reset(seed=self._seed + index)[0] for index, env in enumerate(self.envs)
        ]
        return self._stack(obs_list)

    # -- 진행 -----------------------------------------------------------------

    def step(self, weights: Array) -> StepResult:
        n_envs = self.config.n_envs
        n_assets = self.config.n_assets
        rewards = np.zeros(n_envs)
        terminated = np.zeros(n_envs, dtype=bool)
        truncated = np.zeros(n_envs, dtype=bool)
        drawdown = np.zeros(n_envs)
        turnover = np.zeros(n_envs)
        cost = np.zeros(n_envs)
        reflection = np.zeros(n_envs)
        nav = np.zeros(n_envs)
        realized = np.zeros((n_envs, n_assets))
        targets = np.zeros((n_envs, n_assets))
        final_list: list[dict[str, Any]] = []
        next_list: list[dict[str, Any]] = []

        for index, env in enumerate(self.envs):
            action = {
                "weights": np.asarray(weights[index], dtype=np.float32),
                "delay": self._delay,
                "fx_alloc": self._fx,
            }
            obs, reward, term, trunc, info = env.step(action)
            rewards[index] = reward
            terminated[index] = term
            truncated[index] = trunc
            drawdown[index] = info["drawdown"]
            turnover[index] = info["turnover"]
            cost[index] = info["cost"]
            reflection[index] = info["action_reflection_rate"]
            nav[index] = info["nav"]
            realized[index] = np.asarray(info["realized_weights"])[:n_assets]
            targets[index] = np.asarray(info["target_weights"])[:n_assets]
            final_list.append(obs)
            if term or trunc:
                # 끝난 에피소드는 바로 다시 뽑는다. 그 관측은 이미 새 에피소드의
                # 것이라, 부트스트랩할 마지막 관측은 `final_obs` 로 따로 간다.
                obs = env.reset()[0]
                self.episodes += 1
            next_list.append(obs)

        info: dict[str, Any] = {
            "realized_weights": realized,
            "target_weights": targets,
            # **평균이다.** 합성 판은 마찰이 없어 늘 1.0 이고, 여기서 30% 밑으로
            # 떨어지면 시장이 아니라 안전장치가 액션을 덮은 것이다(§5 ②).
            "action_reflection_rate": float(reflection.mean()),
            "cost": cost,
            "drawdown": drawdown,
            "turnover": turnover,
            "nav": nav,
            "final_obs": self._stack(final_list),
        }
        return self._stack(next_list), rewards, terminated, truncated, info
