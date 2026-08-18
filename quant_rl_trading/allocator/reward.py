"""보상 함수 — `docs/design/reward-and-risk.md §2` 를 그대로 옮긴 것.

    r_t = (r_port,t - r_bench,t) - w(d_t)·Δd_t - cost_t

## 왜 allocator 안인가

보상은 환경의 일부다. `rl-training.md §1` 이 `allocator/env.py` 를, §2 가
`allocator/policy.py` 를 이 패키지에 두라고 못박았고, 보상은 그 둘 사이가
아니라 **환경이 매 스텝 돌려주는 값**이다. `modelops/` 는 "왜 모델이 잘
되나" 를 보는 감시자라, 보상을 거기 두면 감시자가 채점 기준을 소유하게 된다.

## 여기서 하지 않는 것 — NAV 계산

`r_port` 는 **`accounting.nav.twr_return` 이 낸 값을 받아 쓴다.** 낙폭도
`accounting.nav` 의 누적지수·낙폭 함수로 잰다. 이 모듈은 그 위에 페널티만
얹는다.

NAV 를 계산하는 곳은 레포에 한 곳뿐이다(불변식, `accounting.md §8`). 보상이
자기 NAV 를 따로 세면 대시보드에 찍히는 수익률과 에이전트가 학습하는
수익률이 갈라지고, **어느 쪽이 맞는지 판정할 방법이 없다.** 선행 프로젝트가
"RL 이 자기가 하지 않은 행동으로 보상받았다" 로 끝난 것과 같은 종류의
고장이다.

세전을 쓴다. 양도세는 연간 정산이라 일간 보상에 넣으면 매도할 때마다 튄다
(`accounting.md §5`).

## 계수는 전부 store.config("reward")

불변식 10. 12%/22%/30% 와 w 값은 **투자철학이지 하이퍼파라미터가 아니다** —
`rl-training.md §9` 가 Optuna 탐색 공간에서 명시적으로 뺐다. 하드코딩하면
학습·대시보드·리포트가 각자 다른 벽을 보게 된다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from quant_rl_trading.accounting import nav

if TYPE_CHECKING:
    from quant_rl_trading.store import Store


@dataclass(frozen=True)
class RewardParams:
    """`config.reward` 한 벌. 계단의 경계와 높이, 그리고 종료 벌점."""

    drawdown_free: float
    drawdown_warn: float
    drawdown_hard: float
    w_free: float
    w_mid: float
    w_hot: float
    terminal_penalty: float
    normalize_returns: str

    @classmethod
    def from_store(cls, store: Store, *, as_of: datetime) -> RewardParams:
        """섹션을 통째로 읽는다. 키 하나씩 읽으면 키가 늘 때마다 여기를 고쳐야 한다."""
        section: Any = store.config("reward", as_of=as_of)
        return cls(
            drawdown_free=float(section["drawdown_free"]),
            drawdown_warn=float(section["drawdown_warn"]),
            drawdown_hard=float(section["drawdown_hard"]),
            w_free=float(section["w_free"]),
            w_mid=float(section["w_mid"]),
            w_hot=float(section["w_hot"]),
            terminal_penalty=float(section["terminal_penalty"]),
            normalize_returns=str(section["normalize_returns"]),
        )

    def __post_init__(self) -> None:
        if not (0.0 < self.drawdown_free < self.drawdown_warn < self.drawdown_hard):
            raise ValueError(
                "낙폭 경계는 free < warn < hard 여야 한다. 순서가 어긋나면 "
                f"페널티 계단이 뒤집힌다: {self.drawdown_free}/{self.drawdown_warn}/"
                f"{self.drawdown_hard}"
            )


def penalty_weight(depth: float, *, params: RewardParams) -> float:
    """낙폭 깊이별 한계 페널티 `w(d)`. ``depth`` 는 **양수 깊이**(0.12 = -12%).

    읽는 법: 22% 를 넘으면 낙폭 1%p 가 초과수익 8%p 만큼 아프다.

    **자유구간 12% 가 이 펀드의 투자철학이다.** 선형 페널티(`-λ·MDD`)로 바꾸면
    에이전트가 *모든* 낙폭을 피하려 들어 "덜 잃고 덜 버는" 정책으로 수렴한다.
    작은 낙폭은 비용이 아니라 정상 영업이다.
    """
    if depth < params.drawdown_free:
        return params.w_free
    if depth < params.drawdown_warn:
        return params.w_mid
    return params.w_hot


@dataclass(frozen=True)
class RewardBreakdown:
    """한 스텝의 보상과 그 분해. **합계만 돌려주면 어디가 틀렸는지 못 찾는다.**

    학습이 안 될 때 제일 먼저 보는 것이 "초과수익이 죽었나, 페널티가 다
    먹었나" 다. `rl-training.md §5` 의 원인 배제는 이 분해 없이는 못 한다.
    """

    reward: float
    excess_return: float
    drawdown_penalty: float
    cost: float
    depth: float
    delta_depth: float
    index: float
    terminated: bool


class DrawdownTracker:
    """누적지수 위의 낙폭 상태. **NAV 원금액이 아니다** (`accounting.md §6`).

    원금액으로 재면 입금이 낙폭을 지운다 — 30% 빠진 다음 날 증액하면 장부상
    낙폭이 사라지고 MDD 예산이 무의미해진다. 그래서 지수는 `nav.compound`,
    낙폭은 `nav.drawdown` 이 낸 값을 그대로 쓴다.

    ## Δd 는 "신저점 갱신분" 이다

    `reward-and-risk.md §2`: *이번 스텝에 새로 깊어진 낙폭. 신저점 갱신 시에만
    > 0.* 그래서 **직전 스텝 대비**가 아니라 **이번 물속 구간의 최저점 대비**로
    잰다. 100 → 90 → 95 → 92 에서 92 는 신저점이 아니므로 Δd = 0 이다.

    직전 스텝 대비로 재면 오르내림이 이중으로 벌점을 받아, 한 에피소드의
    페널티 합이 실제 MDD 보다 훨씬 커진다. 이 정의에서는 페널티 합이
    **물속 구간별 최대낙폭의 합**이 되어 MDD 를 조밀하게 분해한 값이 된다 —
    희소 보상을 피하면서도 벌점의 총량이 MDD 를 넘지 않는다.

    신고점을 찍으면 ``_worst`` 가 0 으로 돌아간다. 안 돌리면 20% 낙폭을 한 번
    겪은 뒤로는 새 5% 낙폭이 영원히 공짜가 된다.
    """

    def __init__(self, *, base: float = nav.BASE_INDEX) -> None:
        self.index = base
        self._peak = base
        self._worst = 0.0

    @property
    def depth(self) -> float:
        """현재 낙폭 깊이(양수)."""
        return -nav.drawdown([self.index], peak=self._peak)[0]

    def step(self, portfolio_return: float) -> tuple[float, float]:
        """일간 수익률 하나를 반영하고 ``(depth, delta_depth)`` 를 돌려준다."""
        self.index = nav.compound([portfolio_return], base=self.index)[0]
        self._peak = max(self._peak, self.index)
        depth = -nav.drawdown([self.index], peak=self._peak)[0]
        delta = max(0.0, depth - self._worst)
        # 신고점이면 depth 가 0 이라 _worst 도 0 으로 돌아간다.
        self._worst = depth if depth <= 0.0 else max(self._worst, depth)
        return depth, delta


class RewardEngine:
    """에피소드 하나의 보상 계산기. 낙폭 상태를 들고 있어야 하므로 객체다.

    ``cost`` 는 여기서 만들지 않는다 — 수수료·거래세·슬리피지·환전은
    **실비이고 시뮬레이터가 차감한 값**이다(`reward-and-risk.md §5`).
    보상이 비용을 따로 추정하면 시뮬레이터가 뺀 돈과 에이전트가 배우는 벌점이
    갈라진다.
    """

    def __init__(self, *, params: RewardParams, base: float = nav.BASE_INDEX) -> None:
        self.params = params
        self.drawdown = DrawdownTracker(base=base)

    def step(
        self,
        *,
        portfolio_return: float,
        benchmark_return: float,
        cost: float = 0.0,
    ) -> RewardBreakdown:
        """``portfolio_return`` 은 `accounting.nav.twr_return` 이 낸 세전 TWR 이다.

        ``cost`` 는 **양수로 넘긴다**(0.003 = 30bp). 보상에서는 빼는 항이다.
        """
        params = self.params
        depth, delta = self.drawdown.step(portfolio_return)

        excess = portfolio_return - benchmark_return
        penalty = penalty_weight(depth, params=params) * delta
        reward = excess - penalty - cost

        terminated = depth >= params.drawdown_hard
        if terminated:
            # 30% 벽. 자를 뿐 아니라 크게 아파야 한다 — 벌점 없이 자르면
            # 남은 스텝의 벌점을 피하려고 일부러 벽에 부딪히는 것이 이득이 된다.
            reward += params.terminal_penalty

        return RewardBreakdown(
            reward=reward,
            excess_return=excess,
            drawdown_penalty=penalty,
            cost=cost,
            depth=depth,
            delta_depth=delta,
            index=self.drawdown.index,
            terminated=terminated,
        )


class RunningMeanStd:
    """Welford 누적 분산. 전 구간을 들고 있지 않아도 되고, 수치가 안정적이다."""

    def __init__(self, *, epsilon: float = 1e-4) -> None:
        self.mean = 0.0
        self.var = 1.0
        self.count = epsilon

    def update(self, values: list[float]) -> None:
        if not values:
            return
        batch_count = float(len(values))
        batch_mean = sum(values) / batch_count
        batch_var = sum((value - batch_mean) ** 2 for value in values) / batch_count

        delta = batch_mean - self.mean
        total = self.count + batch_count
        m_a = self.var * self.count
        m_b = batch_var * batch_count
        m2 = m_a + m_b + delta * delta * self.count * batch_count / total

        self.mean = self.mean + delta * batch_count / total
        self.var = m2 / total
        self.count = total


class ReturnNormalizer:
    """할인 리턴의 running std 로 보상을 나눈다 (VecNormalize 방식, §3).

    **explained_variance 0 의 1순위 처방이다.** 일간 초과수익은 0.001 규모이고,
    가치함수 헤드의 출력·초기화는 O(1) 을 전제한다. 정규화 없이는 가치함수가
    이 크기를 학습하지 못하고, 그러면 어드밴티지가 통째로 노이즈가 된다.

    - **평균은 빼지 않는다.** 표준편차로만 나눈다. 평균을 빼면 "시장을 이겼다"
      의 부호가 running mean 에 따라 뒤집힌다.
    - **보상에 임의의 상수를 곱하지 않는다.** 낙폭 페널티와 초과수익의 상대
      비율이 깨지면 그건 다른 투자철학이다.
    - 통계는 **학습 중에만** 갱신한다(`train_mode`). 평가에서 갱신하면 평가
      구간이 자기 스케일을 바꾸며 성적이 흔들리고, 재현이 안 된다.
    """

    def __init__(self, *, gamma: float, num_envs: int = 1, epsilon: float = 1e-8) -> None:
        self.gamma = gamma
        self.epsilon = epsilon
        self.rms = RunningMeanStd()
        self.train_mode = True
        self._returns = [0.0] * num_envs

    def __call__(self, rewards: list[float], dones: list[bool]) -> list[float]:
        """스텝 하나의 보상 벡터를 정규화한다. ``dones`` 로 누적을 끊는다."""
        if self.train_mode:
            for index, (reward, done) in enumerate(zip(rewards, dones, strict=True)):
                self._returns[index] = self._returns[index] * self.gamma + reward
                if done:
                    # 끊지 않으면 에피소드 경계를 넘어 할인 리턴이 이어져
                    # 스케일이 부풀고, 그만큼 보상이 작아진다.
                    self._returns[index] = 0.0
            self.rms.update(list(self._returns))

        scale = math.sqrt(self.rms.var) + self.epsilon
        return [reward / scale for reward in rewards]

    def freeze(self) -> None:
        """평가 시작. 이 뒤로 통계는 고정된다."""
        self.train_mode = False
