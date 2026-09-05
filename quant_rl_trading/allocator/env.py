"""LatticeEnv — `docs/design/rl-training.md §1` 의 환경. Gymnasium 호환.

    reset(seed, options) -> (obs, info)
    step(action)         -> (obs, reward, terminated, truncated, info)

## 이 파일이 하지 않는 것 — 전략도, 회계도, 체결도

환경은 **이어 붙이는 자리**다. 후보는 `selector.pipeline` 이 고르고, 주문
수량은 `executor.sizing` 이 정하고, 체결은 M1 의 `replay.fills` 가 흉내내고,
NAV 는 `accounting.nav` 가 내고, 보상은 `allocator.reward` 가 낸다. 여기에
그 중 하나라도 다시 구현하면 **학습이 배우는 세계와 백테스트·실전이 도는
세계가 갈라진다** — 그 갈라짐은 성적표에 안 보이고, 승격한 뒤에야 보인다
(불변식 5).

그래서 이 파일에서 새로 만드는 것은 셋뿐이다: 액션을 목표비중으로 푸는 규칙,
에피소드 경계(250일·낙폭 30%), 관측 텐서의 배치.

## 창고를 물지만 창고에 쓰지 않는다

읽기는 전부 `store.get(as_of=...)` 경유다(불변식 1). 반대로 **쓰기는 한 줄도
없다.** 학습은 같은 구간을 수천 번 되풀이하는데, 그 체결을 `trades` 에 적으면
append-only 창고가 가짜 체결로 덮이고 회계가 그걸 실적으로 접는다. 그래서
장부는 `accounting.book.Book` 을 메모리에서 굴린다 — 순수 코드라 store 도
Clock 도 모르므로, 창고에 적힌 장부와 같은 규칙으로 접힌다.

## 시간은 ReplayClock 뿐이다

`datetime.now()` 는 이 파일에 없다(불변식 2). 에피소드 시작 시각을 학습
구간에서 뽑아 `ReplayClock` 을 만들고, 하루가 끝날 때마다 `advance` 로만
움직인다. 시계가 스스로 흐르면 같은 시드로 두 번 돌린 궤적이 달라진다.

## risk 는 관측에 넣되 알파로는 넣지 않는다 (태스크 #32)

`assets` 의 0번 칸(종합점수)은 `selector.weights.analyst_weights` — **제약
Analyst 가 빠진 알파 가중치**로 합성한다. 반면 Analyst별 칸에는 `risk` 의
점수·신뢰도가 그대로 들어간다. RL 이 위험을 **보는** 것은 옳고, 그것이
"알파 점수" 인 척 합성점수에 섞이는 것은 틀리다. 둘은 다른 일이다
(`selector/constraints.py`).

Analyst 이름 목록은 **하드코딩하지 않는다.** 에피소드 시작 시점에 창고가
알고 있는 Analyst 를 이름순으로 슬롯에 앉힌다 — `chart` 처럼 관찰 모드(가중치
0)이거나 노출 제어로 옮겨 가는 중인 Analyst 가 있어도 파일을 고칠 일이 없다.
"""

from __future__ import annotations

import logging
import math
import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import gymnasium as gym
import numpy as np
import numpy.typing as npt
from gymnasium import spaces

from quant_rl_trading.accounting import nav as nav_module
from quant_rl_trading.accounting.book import KRW, USD, Book, Trade
from quant_rl_trading.accounting.book import Side as BookSide
from quant_rl_trading.accounting.ledger import FX_USDKRW
from quant_rl_trading.accounting.rates import Rates
from quant_rl_trading.allocator import cache as cache_module
from quant_rl_trading.allocator.reward import RewardEngine, RewardParams
from quant_rl_trading.backtest import loop as loop_module
from quant_rl_trading.collectors.market_hours import Market, trading_days
from quant_rl_trading.executor import sizing as sizing_module
from quant_rl_trading.replay.clock import ReplayClock
from quant_rl_trading.replay.fills import FillParams, FillStatus, simulate_fill
from quant_rl_trading.schemas.order import Order, Side

if TYPE_CHECKING:
    from quant_rl_trading.store import Store

logger = logging.getLogger(__name__)

Array = npt.NDArray[np.float32]
Obs = dict[str, np.ndarray]

SEOUL = ZoneInfo("Asia/Seoul")


#: 관측 규격. `rl-training.md §1` 의 표 그대로이고, **줄이지 않는다** —
#: 선행 프로젝트의 "obs 42 vs 모델 128" 이 규격에서만 나는 배선 고장이었다.
N_ASSET_FEATURES = 28
N_PORTFOLIO_FEATURES = 24

#: 종목 축에서 Analyst 가 차지하는 칸: (score, confidence) 9쌍 = 18칸.
#: **9 는 Analyst 수가 아니라 슬롯 수다.** Analyst 가 늘면 이름순으로 앞
#: 아홉만 앉고, 줄면 남는 슬롯이 0 으로 남는다 — 어느 쪽이든 관측의 모양은
#: 변하지 않는다. 모양이 변하면 학습된 정책이 그 순간 못 쓰게 된다.
ANALYST_SLOTS = cache_module.ANALYST_SLOTS

#: 종목 축 피처 자리. 이름으로 잡아 두는 이유는 4-4 의 오라클 카나리가
#: "몇 번 칸에 정답을 심었나" 를 이 상수로 가리키기 때문이다.
FEATURE_SCORE = 0
FEATURE_ANALYST_BASE = 1  # 1..18
FEATURE_REALIZED_WEIGHT = 19
FEATURE_MIN_STEP = 20
FEATURE_LIQUIDATION_DAYS = 21
FEATURE_VOLATILITY = 22
FEATURE_BETA = 23
FEATURE_HOLDING_DAYS = 24
FEATURE_UNREALIZED = 25
FEATURE_SECTOR_BASE = 26  # 26..27

#: 오라클 카나리(§0)가 정답을 심는 칸. **26번을 덮어쓴다 — 칸을 늘리지 않는다.**
#:
#: 28칸이 이미 다 찼고, 늘리면 관측 규격이 바뀌어 `policy.py` 의
#: `n_asset_features` 가 따라가고 그 전에 학습한 정책이 통째로 못 쓰게 된다.
#:
#: 왜 하필 26 인가. 섹터 one-hot 축약(26~27)은 지금 **항상 0** 이다 — 창고의
#: ``sectors`` 가 업종이 아니라 KOSDAQ 소속부라 채우지 않기로 한 자리다
#: (`_asset_features` 의 같은 판단). 살아 있는 피처를 덮으면 오라클 판과
#: 대조군이 "미래가 들었나" 말고 "그 피처가 빠졌나" 로도 갈려서, 카나리가
#: 무엇을 쟀는지 말할 수 없게 된다. 0번(종합점수)을 안 쓰는 이유는 첫 칸만
#: 보는 고장이 그대로 합격으로 찍히기 때문이다(`canary_env.ORACLE_IDX` 의
#: 같은 이유). 진짜 업종 분류가 들어와 26 이 채워지면 그때 27 로 옮긴다.
FEATURE_ORACLE = FEATURE_SECTOR_BASE

#: 오라클이 알려주는 지평(거래일). §0 의 ``future_excess_return_5d``.
ORACLE_HORIZON = 5
#: 표준화 나눗셈. 5일 초과수익은 5% 규모라 그대로 넣으면 다른 칸(점수·비중)
#: 보다 두 자리 작다 — 카나리가 배선 대신 스케일을 재게 된다.
ORACLE_SCALE = 0.05

#: 레짐 one-hot 순서. `analysts/regime.py` 가 내는 상태에 `sideways` 를 더한
#: 것이다 — agents.md 는 횡보를 포트폴리오 축 상태로 적는데 현재 룰 판정은
#: 그 상태를 내지 않는다. **칸을 비워 둔다.** 나중에 판정이 늘었을 때 칸을
#: 새로 만들면 그 시점 이전에 학습된 정책이 통째로 못 쓰게 된다.
REGIME_STATES = ("bull", "bear", "sideways", "volatile", "crisis", "unknown")

#: 창 정의는 `allocator/cache.py` 에 산다 — 굽는 쪽과 읽는 쪽이 다른 창을 쓰면
#: 캐시가 조용히 다른 베타를 낸다. 여기서는 이름만 빌린다.
BETA_WINDOW = cache_module.BETA_WINDOW
FX_WINDOW = cache_module.FX_WINDOW


_LEAK_BANNER = (
    "\n"
    "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n"
    "!!  oracle_leak=True — 관측에 5일 뒤 실제 초과수익이 들어 있다.     !!\n"
    "!!  이 설정으로 나온 성과는 전부 가짜다. 배선 점검 전용이다.       !!\n"
    "!!  실제 학습·백테스트·실전에서 켜져 있으면 즉시 멈춰라.           !!\n"
    "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
)


@dataclass(frozen=True)
class EnvParams:
    """환경 임계치. **전부 `store.config` 에서 온다** (불변식 10).

    에피소드 하나를 시작할 때 한 벌 읽고 그 에피소드 내내 쓴다. 매일 다시
    읽으면 구간 중간의 설정 정정본이 같은 에피소드를 두 규칙으로 굴려, 같은
    시드로 두 번 돌린 궤적이 갈릴 수 있다 — 재현성이 이 저장소의 규약이다.
    """

    episode_days: int
    n_max: int
    delay_choices: int
    max_position_weight: float
    cash_buffer: float
    initial_capital: float
    settlement_days: int
    kr_policy_rate: str
    us_policy_rate: str
    reward: RewardParams
    fill: FillParams
    sizing: sizing_module.SizingParams
    rates: Rates
    #: 3회차(2026-08-29) — **외울 수 있는 자유도를 정책에서 뺀다.**
    #: "free": 현금 칸이 액션이다(1·2회차). "fixed": 투자 비중은 1 − cash_buffer 로
    #: 고정하고 정책은 후보 사이의 배분만 정한다. 2회차 OOS 판정에서 학습 구간이 가르친
    #: 것이 현금 타이밍뿐이었고 그것이 통째로 외운 것이었다(rl-training.md 2회차 판정).
    #: 노출("얼마나")은 룰(selector/exposure)이 맡는다.
    cash_action: str = "free"
    #: 에피소드를 현금이 아니라 **첫날 후보 균등가중으로 채운 장부**에서 시작한다.
    #: 현금에서 출발하면 짧은 평가 창에서 "얼마나 빨리 들어가나" 가 성적을 지배한다.
    warm_start: bool = False
    #: 보상 기준선(`reward.BASELINES`). 체크포인트의 env_overrides 로 따라다닌다 — 평가가
    #: 학습과 다른 보상으로 "우위" 를 재면 그 숫자는 아무것도 아니다.
    reward_baseline: str = "benchmark"

    @classmethod
    def from_store(
        cls, store: Store, *, as_of: datetime, hyper_as_of: datetime | None = None
    ) -> EnvParams:
        """설정을 읽어 환경 인자를 만든다. **두 시점을 쓴다.**

        ## 왜 시점이 둘인가

        이 표에는 성격이 다른 둘이 섞여 있다.

            세상이 어땠나   결제주기 · 호가단위 · 수수료 · 환율
            우리가 어떻게 하나  슬롯 수 · 에피소드 길이 · 보상 계수 · 비중 상한

        앞은 과거 조회에서 **그때 값**이어야 한다. 2024년 백테스트가 오늘
        수수료로 돌면 그건 백테스트가 아니다.

        뒤는 반대다. **오늘 우리가 고른 설계**여야 한다. 불변식 5 가 "백테스트와
        라이브는 같은 코드를 쓴다" 고 못 박은 이유가 이것이다 — 백테스트가
        2025년의 슬롯 수로 돌고 실전이 오늘 슬롯 수로 돌면 둘은 같은 시스템이
        아니고, 백테스트 성적은 실전을 예측하지 못한다.

        ## 실제로 겪은 일 (2026-08-20)

        `n_max_candidates` 를 30 → 15 로 바꾸고 학습을 돌렸는데 **마지막
        자리까지 똑같은 숫자**가 나왔다. 환경이 이 값을 학습 구간 첫날
        (2025-01-02) 기준으로 읽어서, 그날 발효한 정정본이 그 시점에는
        존재하지 않았던 것이다. 오류가 아니라 **조용히 옛 값**이었다.

        발효일을 앞당기는 길은 막혀 있다 — 그러면 과거 백테스트가 소급해
        바뀌고, M3 를 통과 중인 OOS 결과가 소리 없이 다른 값이 된다.

        ## hyper_as_of 를 안 주면

        ``as_of`` 와 같아진다. 옛 동작 그대로다 — 백테스트처럼 "그때 설계로
        재현" 이 목적인 자리는 그대로 두면 된다. **학습·진단은 반드시 준다.**
        """
        hyper = hyper_as_of or as_of
        return cls(
            episode_days=int(store.config("allocator.episode_days", as_of=hyper)),
            n_max=int(store.config("allocator.n_max_candidates", as_of=hyper)),
            # 액션은 0~max 일 지연이므로 선택지는 max+1 개다. `rl-training.md`
            # 의 Categorical(4) 가 이 값에서 나온다 — 4 를 코드에 박으면
            # 지연 상한을 바꾸는 순간 액션 공간과 설정이 조용히 어긋난다.
            delay_choices=int(
                store.config("allocator.env.max_entry_delay_days", as_of=hyper)
            )
            + 1,
            max_position_weight=float(
                store.config("allocator.max_position_weight", as_of=hyper)
            ),
            cash_buffer=float(store.config("allocator.cash_buffer", as_of=hyper)),
            initial_capital=float(
                store.config("allocator.env.initial_capital", as_of=hyper)
            ),
            # **결제주기는 세상 쪽이다.** T+2 가 T+1 로 바뀌면 그것은 우리가
            # 고른 것이 아니라 시장이 바뀐 것이고, 과거 구간은 그때 규칙으로
            # 굴러야 한다.
            settlement_days=int(store.config("execution.settlement_days", as_of=as_of)),
            kr_policy_rate=str(
                store.config("allocator.env.kr_policy_rate_series", as_of=as_of)
            ),
            us_policy_rate=str(
                store.config("allocator.env.us_policy_rate_series", as_of=as_of)
            ),
            # 보상은 **우리 설계**다. 오늘 고른 보상으로 과거를 평가하는 것이
            # 맞다 — 2025년의 보상 계수로 학습해 놓고 오늘 다른 계수로 실전을
            # 돌리면 학습이 무엇을 위한 것이었는지 알 수 없다.
            reward=RewardParams.from_store(store, as_of=hyper),
            # 체결·호가·환율은 세상 쪽이다. 그때 값으로 재현해야 한다.
            fill=FillParams.from_store(store, as_of=as_of),
            sizing=sizing_module.SizingParams.from_store(store, as_of=as_of),
            rates=Rates.from_store(store, as_of=as_of),
        )


@dataclass
class _Pending:
    """아직 체결일이 오지 않은 주문. 지연 액션이 여기서 실현된다."""

    due: date
    order: Order
    target_weight: float


@dataclass
class _Slot:
    """관측의 한 줄이 가리키는 종목. 마스크된 슬롯에는 이것이 없다."""

    entity_id: str
    score: float = 0.0


@dataclass
class _EpisodeState:
    """에피소드 하나가 들고 있는 전부. `reset` 이 통째로 갈아 끼운다."""

    sessions: list[date] = field(default_factory=list)
    cursor: int = 0
    book: Book = field(default_factory=Book)
    engine: RewardEngine | None = None
    slots: list[_Slot] = field(default_factory=list)
    analysts: tuple[str, ...] = ()
    pending: list[_Pending] = field(default_factory=list)
    #: 종목별 최초 매수일. 보유 경과일 피처가 이걸 뺀다.
    entered: dict[str, date] = field(default_factory=dict)
    #: 결제 전 매도대금. (체결일, 금액) — 주문가능금액에서 빠진다.
    unsettled: list[tuple[date, float]] = field(default_factory=list)
    nav: float = 0.0
    benchmark_index: float | None = None
    #: 직전 스텝의 진단. 포트폴리오 축 피처로 되먹인다.
    last_turnover: float = 0.0
    last_reflection: float = 1.0
    last_drawdown: float = 0.0


class LatticeEnv(gym.Env[Obs, dict[str, Any]]):
    """국장 단일시장 · 일간 · 250거래일 에피소드.

    ## 하루의 순서는 백테스트와 같다

    `backtest/loop.py` 가 못박은 순서(체결 → 신호 → 스냅샷 → 결정)를 스텝
    경계로 자른 것이다::

        obs_t  ← 오늘 체결·평가가 끝난 뒤의 상태 (= 결정 직전)
        a_t    ← 오늘의 결정. 주문은 **내일 이후**에 체결된다
        r_t    ← 내일 체결·평가까지 지난 뒤에 나오는 보상

    **결정한 날 종가로 체결시키지 않는다.** 그건 백테스트에서 가장 흔한 미래
    훔쳐보기이고, RL 에서는 그 훔쳐본 값이 그대로 정책 그래디언트에 실린다.

    ## 미장·환전은 아직 없다

    `fx_alloc` 액션은 받아서 `info` 에 남기지만 환전하지 않는다. 커리큘럼
    C4(4-7) 가 미장 후보를 넣기 전에는 바꿀 달러가 쓸 데가 없고, 쓸 데 없는
    환전은 환차손익이라는 잡음만 보상에 얹는다. 액션 공간을 지금부터 두는
    이유는 나중에 공간이 바뀌면 그 전에 학습한 정책이 못 쓰게 되기 때문이다.
    """

    def __init__(
        self,
        store: Store,
        *,
        train_start: date,
        train_end: date,
        market: str = "KR",
        params: EnvParams | None = None,
        oracle_leak: bool = False,
        curriculum_c1: bool = False,
        use_cache: bool = True,
        cache_root: Path | None = None,
        #: 학습 설계값(슬롯 수·보상 계수 등)을 읽을 시점. 안 주면 학습 구간
        #: 첫날이 되어 **오늘 바꾼 설정을 못 본다** — `EnvParams.from_store`
        #: 독스트링 참고. 학습·진단은 반드시 준다.
        hyper_as_of: datetime | None = None,
    ) -> None:
        """``train_start``~``train_end`` 는 **학습 구간**이다.

        에피소드 시작일을 여기서만 뽑는다. 검증·테스트 구간을 섞으면
        walk-forward(§8)가 무의미해진다 — 그 오염은 성적표에 안 보인다.

        ``params`` 를 주지 않으면 구간 첫날 시점의 설정을 읽는다. 창고를
        읽으므로 as_of 가 필요하고, 그 as_of 를 벽시계에서 가져오면 불변식 2
        를 어긴다.

        ``use_cache`` 는 **속도에만 영향을 준다.** 세션 피처 캐시가 깔려 있으면
        읽고 없으면 창고에서 계산한다 — 두 경로가 같은 값을 낸다는 것이
        `tests/allocator/test_rl_cache.py` 가 지키는 계약이다. 값이 갈리면
        학습 결과 전체가 무효이므로, 캐시를 끄고 켜는 것으로 성적이 바뀌면
        그건 최적화가 아니라 사고다.
        """
        self.store = store
        self.market = Market(market)
        self.oracle_leak = oracle_leak
        # **C1 커리큘럼** (rl-training.md §6 · reward-and-risk.md §8).
        # 보상 = 선택 점수(r_port − invested·r̄)만, 비용 0. "무엇을 살까" 를
        # 먼저 배우게 하는 보조바퀴다 — 1회차는 이 단계를 건너뛰고 전부 켠 채
        # 돌렸고, 벌점(비용·낙폭)만 또렷해서 정책이 현금으로 도망갔다.
        self.curriculum_c1 = curriculum_c1
        self.reader = cache_module.build_reader(
            store,
            str(self.market),
            cache_root=cache_root,
            use_cache=use_cache,
            # 오라클은 5세션 앞을 같이 읽는다. 기본 창(4)으로는 오늘 것이
            # 5스텝 만에 밀려나 같은 파일을 두 번 파고, 스텝 비용이 두 배가
            # 된다 — 카나리가 배선이 아니라 인내심을 재게 된다.
            lru=cache_module.CachedSessionReader.LRU
            + (ORACLE_HORIZON if oracle_leak else 0),
        )
        if oracle_leak:  # pragma: no cover - 배선 점검 전용 경로
            # 로그와 경고 둘 다에 남긴다. 로그만 남기면 테스트 러너가 조용히
            # 삼키고, 경고만 남기면 학습 로그에 흔적이 없다 (`canary_env`).
            logger.warning(_LEAK_BANNER)
            warnings.warn(_LEAK_BANNER, RuntimeWarning, stacklevel=2)

        self._sessions = trading_days(self.market, train_start, train_end)
        self.params = params or EnvParams.from_store(
            store,
            as_of=self._moment(self._sessions[0]),
            hyper_as_of=hyper_as_of,
        )
        if len(self._sessions) <= self.params.episode_days:
            raise ValueError(
                f"{train_start}~{train_end} 에 거래일이 {len(self._sessions)}일뿐이다. "
                f"에피소드 {self.params.episode_days}일을 뽑을 수 없다 — "
                "짧은 구간으로 자르면 MDD 30% 벽이 죽은 항이 된다"
            )

        n_max = self.params.n_max
        self.observation_space = spaces.Dict(
            {
                "portfolio": spaces.Box(
                    -np.inf, np.inf, shape=(N_PORTFOLIO_FEATURES,), dtype=np.float32
                ),
                "assets": spaces.Box(
                    -np.inf, np.inf, shape=(n_max, N_ASSET_FEATURES), dtype=np.float32
                ),
                # bool 이어야 한다(§1 표). gymnasium 은 런타임에 받아 주는데
                # 타입 스텁이 정수·실수만 적어 두었다.
                "mask": spaces.Box(0, 1, shape=(n_max,), dtype=np.bool_),  # type: ignore[arg-type]
            }
        )
        # **공간은 지지집합만 선언한다.** Dirichlet·Beta 는 정책(4-3)이 쓰는
        # 분포이고, 환경이 보는 것은 "심플렉스 위의 벡터" 와 "[0,1] 스칼라"
        # 뿐이다. 여기에 분포를 박으면 정책을 바꿀 때마다 환경을 고쳐야 한다.
        self.action_space = spaces.Dict(
            {
                "weights": spaces.Box(0.0, 1.0, shape=(n_max + 1,), dtype=np.float32),
                "delay": spaces.MultiDiscrete([self.params.delay_choices] * n_max),
                "fx_alloc": spaces.Box(0.0, 1.0, shape=(1,), dtype=np.float32),
            }
        )

        self._state = _EpisodeState()
        self._clock: ReplayClock | None = None
        self._rng = np.random.default_rng()

    # -- 시간 -------------------------------------------------------------------

    def _moment(self, day: date) -> datetime:
        """그 세션의 기준 시각.

        `backtest/loop.snapshot_moment` 를 **한 번만** 부르고 시각 성분만
        재사용한다. 매일 부르면 공표정책 조회가 250번 반복되는데, 그 값의
        시각 성분은 세션마다 같다. 조회 자체는 store 경유이므로 게이트를
        우회하는 것이 아니라 같은 답을 두 번 사지 않는 것뿐이다.
        """
        cached = getattr(self, "_session_time", None)
        if cached is None:
            probe = datetime.combine(day, loop_module.DEFAULT_SNAPSHOT_TIME, tzinfo=SEOUL)
            moment = loop_module.snapshot_moment(self.store, day, as_of=probe)
            # **서울 시각으로 바꿔서 시각 성분을 뽑는다.** 그 함수는 신호
            # 공표 시각(UTC)을 돌려줄 수 있는데, UTC 의 시:분을 그대로 서울에
            # 다시 붙이면 세션이 아홉 시간 앞으로 당겨진다 — 그러면 as_of 가
            # 그날 종가보다 이르러 **체결이 통째로 사라지고**, 증상은 "아무것도
            # 안 사는 정책" 으로만 보인다.
            cached = moment.astimezone(SEOUL).timetz()
            self._session_time = cached
        return datetime.combine(day, time(cached.hour, cached.minute), tzinfo=SEOUL)

    @property
    def clock(self) -> ReplayClock:
        """지금 이 환경이 보는 시각. **주입된 ReplayClock 이 유일한 출처다.**"""
        if self._clock is None:
            raise RuntimeError("reset() 전에는 시계가 없다")
        return self._clock

    @property
    def as_of(self) -> datetime:
        return self.clock.now()

    # -- 에피소드 ---------------------------------------------------------------

    def reset(
        self, *, seed: int | None = None, options: dict[str, Any] | None = None
    ) -> tuple[Obs, dict[str, Any]]:
        """시작일을 학습 구간에서 무작위로 뽑는다 (겹치는 윈도우, §7).

        ``options["start"]`` 로 날짜를 직접 줄 수 있다. 평가·회귀 시험이
        같은 구간을 반복해서 볼 때 쓴다 — 그때 무작위로 뽑으면 두 정책을
        비교하는 것이 아니라 두 구간을 비교하게 된다.
        """
        super().reset(seed=seed)
        if seed is not None:
            self._rng = np.random.default_rng(seed)

        span = self.params.episode_days
        options = options or {}
        if "start" in options:
            start = self._sessions.index(options["start"])
            if start + span > len(self._sessions):
                # **짧은 에피소드를 조용히 내주지 않는다.** 250일은 MDD 정의에
                # 묶여 있어서(§9 가 튜닝 금지 목록에 넣었다), 구간 끝에 걸려
                # 30일짜리 에피소드가 나오면 낙폭 통계가 통째로 다른 물건이
                # 되는데 로그에는 "그냥 짧은 에피소드" 로만 보인다.
                raise ValueError(
                    f"{options['start']} 에서 시작하면 {len(self._sessions) - start}일뿐이다. "
                    f"에피소드 {span}일이 구간 끝을 넘는다"
                )
        else:
            start = int(self._rng.integers(0, len(self._sessions) - span))

        sessions = self._sessions[start : start + span]
        self._clock = ReplayClock(self._moment(sessions[0]))
        self._state = _EpisodeState(
            sessions=sessions,
            cursor=0,
            # 입금은 첫날 한 번. 이후 입출금이 없으므로 TWR 의 inflow 는 0 이고
            # 낙폭은 입금으로 지워지지 않는다 (accounting.md §6).
            book=Book(cash={KRW: self.params.initial_capital, USD: 0.0}),
            engine=RewardEngine(params=self.params.reward, baseline=self.params.reward_baseline),
            analysts=self._analyst_slots(),
            nav=self.params.initial_capital,
        )
        # 직전 에피소드의 평가·시세가 첫 관측에 새어 들지 않게 비운다.
        self._last_valuation = None
        self._last_prices = {}
        if self.params.warm_start:
            self._warm_start()
        obs, info = self._observe()
        return obs, info

    def _warm_start(self) -> None:
        """첫날 종가로 후보를 균등하게 사서 시작한다 (`EnvParams.warm_start`).

        수수료·세금은 실제 요율로 문다 — 공짜로 채운 장부는 첫 스텝의 NAV 를
        높여 보상을 왜곡한다. 최소 스텝(1주 가격)이 배분 몫보다 큰 종목은 못
        사고 현금으로 남는다 — 라이브에서도 그렇다.
        """
        state = self._state
        selection = self.reader.selection(self.as_of, equity=state.nav)
        entities = [entity for entity, _score in selection.candidates][: self.params.n_max]
        if not entities:
            return
        prices = self.reader.stats(self.as_of, entities).prices
        investable = max(0.0, 1.0 - self.params.cash_buffer)
        weight = min(investable / len(entities), self.params.max_position_weight)
        today = state.sessions[state.cursor]
        for entity in entities:
            price = float(prices.get(entity, 0.0))
            if price <= 0:
                continue
            quantity = math.floor(state.nav * weight / price)
            if quantity <= 0:
                continue
            gross = quantity * price
            fee, tax = self.params.rates.costs(side=BookSide.BUY, gross=gross, currency=KRW)
            state.book = state.book.with_trade(
                Trade(
                    entity_id=entity, side=BookSide.BUY, quantity=float(quantity),
                    price=price, currency=KRW, fee=fee, tax=tax,
                )
            )
            state.entered[entity] = today
        valuation = self._value()
        self._last_valuation = valuation
        state.nav = valuation.nav

    def _analyst_slots(self) -> tuple[str, ...]:
        """이 에피소드가 쓸 Analyst 슬롯. **이름을 코드에 적지 않는다.**

        판단은 `allocator/cache.SessionReader.analyst_slots` 하나에 있다 —
        굽는 쪽과 읽는 쪽에 같은 규칙이 두 벌 있으면 언젠가 갈라진다.
        """
        return self.reader.analyst_slots(self.as_of)

    # -- 스텝 -------------------------------------------------------------------

    def step(
        self, action: dict[str, Any]
    ) -> tuple[Obs, float, bool, bool, dict[str, Any]]:
        state = self._state
        if state.engine is None:
            raise RuntimeError("reset() 을 먼저 불러야 한다")

        # 1. 오늘의 결정. 주문은 내일 이후에 체결된다.
        targets, delays = self._decode(action)
        self._plan_orders(targets, delays)

        # 2. 하루를 넘긴다. 시계는 advance 로만 움직인다 (불변식 2).
        previous_nav = state.nav
        # §8 선택/노출 분해의 재료 — **어제 시점**의 후보 가격과 주식 비중.
        # advance 뒤에 읽으면 오늘 가격이라 r̄ 이 0 이 된다.
        slot_entities = [slot.entity_id for slot in state.slots]
        previous_prices = self._prices(slot_entities)
        # 첫 스텝(reset 직후)엔 아직 평가가 없다 — 그날은 노출을 모르는 채로
        # 분해를 끈다(selection=excess). 하루짜리 공백이고, 지어내지 않는다.
        previous_valuation = getattr(self, "_last_valuation", None)
        state.cursor += 1
        today = state.sessions[state.cursor]
        self.clock.advance(self._moment(today) - self.as_of)

        # 3. 체결 — 오늘 만기가 된 주문만.
        filled, cost_krw, turnover_krw = self._settle(today)

        # 4. 평가. NAV 는 회계 한 곳에서만 나온다 (불변식: accounting.md §8).
        valuation = self._value()
        self._last_valuation = valuation
        state.nav = valuation.nav
        portfolio_return = nav_module.twr_return(
            nav=valuation.nav, previous_nav=previous_nav, inflow=0.0
        )
        benchmark_return = self._benchmark_return()

        # 5. 보상. 비용은 **시뮬레이터가 실제로 뺀 값**을 넘긴다 — 여기서 다시
        #    추정하면 장부에서 나간 돈과 배우는 벌점이 갈라진다.
        cost = cost_krw / previous_nav if previous_nav > 0 else 0.0
        if self.curriculum_c1:
            cost = 0.0   # C1: 비용 0 — 행동하면 감점이라는 도망 유인을 치운다
        # §8 — 후보 균등가중 일간수익률 r̄ 과 어제의 주식 비중. 미래를 안 본다:
        # 둘 다 어제 관측(가격·평가)에서 나오고, 오늘 가격은 방금 advance 로
        # 열린 그날 종가다(체결 평가와 같은 시점).
        today_prices = self._prices(slot_entities)
        candidate_returns = [
            today_prices[e] / previous_prices[e] - 1.0
            for e in slot_entities
            if previous_prices.get(e, 0.0) > 0 and today_prices.get(e, 0.0) > 0
        ]
        candidate_mean = (
            sum(candidate_returns) / len(candidate_returns)
            if candidate_returns else None
        )
        invested_share = None
        if previous_valuation is not None and previous_valuation.nav > 0:
            cash_total = (
                previous_valuation.cash_krw
                + previous_valuation.cash_usd * previous_valuation.fx_rate
            )
            invested_share = max(0.0, 1.0 - cash_total / previous_valuation.nav)
        breakdown = state.engine.step(
            portfolio_return=portfolio_return,
            benchmark_return=benchmark_return,
            cost=cost,
            candidate_mean_return=candidate_mean,
            invested_share=invested_share,
        )

        if self.curriculum_c1:
            # C1 보상 = **선택 점수만**. 낙폭 페널티·노출 항을 빼는 이유:
            # 지금 가르치는 과목이 "무엇을 살까" 하나이기 때문이다. 총보상
            # (분해 전과 동일)으로는 §8 분리가 학습에 아무 효과가 없다 —
            # r 재측정도 이 모드로 해야 뜻이 있다.
            breakdown = replace(breakdown, reward=breakdown.selection_return)

        realized = self._realized_weights(valuation.nav)
        reflection = _reflection_rate(targets, realized)
        turnover = turnover_krw / previous_nav if previous_nav > 0 else 0.0
        state.last_turnover = turnover
        state.last_reflection = reflection
        state.last_drawdown = breakdown.depth

        truncated = state.cursor >= len(state.sessions) - 1
        terminated = breakdown.terminated
        obs, observe_info = (
            self._observe() if not (terminated or truncated) else (self._blank(), {})
        )

        info: dict[str, Any] = {
            # `rl-training.md §1` 이 매 스텝 요구하는 여섯. 하나라도 빠지면
            # §5 의 원인 배제(②③)를 못 한다.
            "realized_weights": realized,
            "target_weights": targets,
            "action_reflection_rate": reflection,
            "cost": cost,
            # §8 분해 — 학습 로그가 "선택을 배우나, 노출만 만지나" 를 가른다.
            "selection_return": breakdown.selection_return,
            "exposure_return": breakdown.exposure_return,
            "drawdown": breakdown.depth,
            "turnover": turnover,
            # 진단용. 합계만 보면 어디가 틀렸는지 못 찾는다.
            "as_of": self.as_of,
            "nav": valuation.nav,
            "excess_return": breakdown.excess_return,
            "drawdown_penalty": breakdown.drawdown_penalty,
            "portfolio_return": portfolio_return,
            "benchmark_return": benchmark_return,
            "filled": filled,
            "fx_alloc": float(np.asarray(action["fx_alloc"]).reshape(-1)[0]),
            **observe_info,
        }
        return obs, float(breakdown.reward), terminated, truncated, info

    # -- 액션 -------------------------------------------------------------------

    def _decode(self, action: dict[str, Any]) -> tuple[Array, npt.NDArray[np.int64]]:
        """액션 → (목표비중 N_max+1, 지연 N_max).

        **마스크된 슬롯의 비중은 현금으로 간다.** 정책이 패딩 슬롯에 비중을
        주더라도 그것으로는 아무것도 살 수 없고, 살 수 없는 비중을 나머지에
        다시 나누면 정책이 의도하지 않은 레버리지가 생긴다.

        종목 상한(`allocator.max_position_weight`)에서 깎인 몫도 현금이다.
        `baseline._normalize` 는 남는 몫을 다른 종목에 다시 나누는데, 그건
        **룰이 스스로 정한 비중**이라 그렇게 해도 뜻이 유지된다. 정책의 비중은
        정책이 정한 것이라, 깎은 몫을 다른 종목에 옮기면 하지 않은 결정을
        대신 내는 것이 된다 (불변식 7 의 취지).
        """
        n_max = self.params.n_max
        weights = np.asarray(action["weights"], dtype=np.float64).reshape(-1)
        if weights.shape[0] != n_max + 1:
            raise ValueError(
                f"weights 는 {n_max + 1}칸이어야 한다(마지막이 현금): {weights.shape}"
            )
        if np.any(weights < -1e-6):
            raise ValueError("비중이 음수다. 공매도는 이 펀드에 없다")

        total = float(weights.sum())
        if total <= 0:
            raise ValueError("비중 합이 0 이다. 심플렉스 위의 값이 아니다")
        weights = weights / total

        mask = np.zeros(n_max, dtype=bool)
        mask[: len(self._state.slots)] = True
        assets = np.where(mask, weights[:n_max], 0.0)

        cap = self.params.max_position_weight
        investable = max(0.0, 1.0 - self.params.cash_buffer)
        if self.params.cash_action == "fixed" and float(assets.sum()) > 0:
            # **현금은 액션이 아니다.** 유효 슬롯의 비중을 투자 가능분에 맞춰 늘리고,
            # 상한에 걸려 남는 몫은 상한 아래 종목에 한 번 더 비례 배분한다. 그래도
            # 남으면 현금 — 후보가 적어 상한 × 종목 수 < 투자분인 날이다.
            assets = assets / float(assets.sum()) * investable
            capped = np.minimum(assets, cap)
            leftover = investable - float(capped.sum())
            room = np.where(mask & (capped < cap), cap - capped, 0.0)
            if leftover > 1e-12 and float(room.sum()) > 0:
                capped = capped + room * min(1.0, leftover / float(room.sum()))
            assets = capped
        assets = np.minimum(assets, cap)
        excess = float(assets.sum()) - investable
        if excess > 0:
            # 현금 완충을 침범한 만큼만 비례로 깎는다. 체결·수수료 오차를
            # 흡수할 여유가 없으면 매수가 거부되고, 거부는 재시도와 슬리피지가
            # 된다 (`allocator.cash_buffer`).
            assets = assets * (investable / float(assets.sum()))

        targets = np.zeros(n_max + 1, dtype=np.float32)
        targets[:n_max] = assets
        targets[n_max] = 1.0 - float(assets.sum())

        delays = np.asarray(action["delay"], dtype=np.int64).reshape(-1)
        if delays.shape[0] != n_max:
            raise ValueError(f"delay 는 {n_max}칸이어야 한다: {delays.shape}")
        if np.any(delays < 0) or np.any(delays >= self.params.delay_choices):
            raise ValueError(
                f"지연은 0~{self.params.delay_choices - 1}일이다: {delays.tolist()}"
            )
        return targets, delays

    def _plan_orders(self, targets: Array, delays: npt.NDArray[np.int64]) -> None:
        """목표비중을 주문으로 바꿔 대기열에 넣는다.

        수량은 `executor.sizing.size_orders` 가 정한다 — 라운딩·최소주문금액·
        거래대금 상한·**주문가능금액**이 전부 거기 있다. 여기서 다시 세면
        학습이 배우는 체결 가능성과 실전의 체결 가능성이 갈라지고, 그 갈라짐이
        곧 액션 반영률의 거짓말이 된다.

        ``cash`` 를 반드시 넘긴다. 안 넘기면 없는 돈으로 사고, 그 길로 이
        저장소는 레버리지 2.83배까지 갔다 (`sizing.size_orders` 독스트링).
        """
        state = self._state
        today = state.sessions[state.cursor]
        holdings = {
            entity: int(position.quantity)
            for entity, position in state.book.positions.items()
            if position.quantity > 0
        }
        entities = list(dict.fromkeys([slot.entity_id for slot in state.slots] + list(holdings)))
        if not entities:
            return

        stats = self.reader.stats(self.as_of, entities)
        prices, adv = stats.prices, stats.adv
        weights = {slot.entity_id: float(targets[index]) for index, slot in enumerate(state.slots)}
        # **보유 중인데 슬롯이 없는 종목은 목표 0 이다.** 안 넣으면 팔 기회가
        # 영영 오지 않아 장부가 한 방향 래칫이 된다 (session/daily.py 의 같은 자리).
        plan = [
            sizing_module.Target(
                entity_id=entity,
                weight=weights.get(entity, 0.0),
                price=prices.get(entity, 0.0),
                adv_value=adv.get(entity, 0.0),
            )
            for entity in entities
        ]
        sized, _skipped = sizing_module.size_orders(
            targets=plan,
            holdings=holdings,
            equity=state.nav,
            params=self.params.sizing,
            cash=self._available_cash(today),
        )

        delay_by_entity = {
            slot.entity_id: int(delays[index]) for index, slot in enumerate(state.slots)
        }
        for item in sized:
            # **지연은 매수에만 건다.** 매도를 미루는 것은 빠져나올 길을 미루는
            # 것이고, 액션 공간의 이름(진입 지연)이 뜻하는 바도 아니다.
            wait = delay_by_entity.get(item.entity_id, 0) if item.side is Side.BUY else 0
            due = self._session_after(today, wait + 1)
            if due is None:
                continue  # 에피소드 끝을 넘는 주문은 체결될 자리가 없다
            state.pending.append(
                _Pending(
                    due=due,
                    order=Order(
                        entity_id=item.entity_id,
                        side=item.side,
                        quantity=item.quantity,
                        reason=f"rl:{item.target_weight:.4f}",
                    ),
                    target_weight=item.target_weight,
                )
            )

    def _session_after(self, day: date, steps: int) -> date | None:
        index = self._state.sessions.index(day) + steps
        sessions = self._state.sessions
        return sessions[index] if index < len(sessions) else None

    # -- 체결·회계 --------------------------------------------------------------

    def _settle(self, today: date) -> tuple[int, float, float]:
        """오늘 만기가 된 주문을 체결시킨다. ``(체결주수, 비용, 거래대금)``.

        체결은 M1 의 `replay.fills.simulate_fill` 이 한다. 같은 입력이면 같은
        출력인 순수 함수라, 같은 시드로 두 번 돌린 궤적이 같아진다.
        """
        state = self._state
        due = [item for item in state.pending if item.due == today]
        state.pending = [item for item in state.pending if item.due != today]
        if not due:
            return 0, 0.0, 0.0

        entities = sorted({item.order.entity_id for item in due})
        # **시장 상태는 캐시 대상, 체결은 아니다.** 그날 봉은 세션이 정하고,
        # 무엇을 주문했는지는 액션이 정한다 (`allocator/cache.py` 의 경계).
        states = self.reader.fill_states(self.as_of, entities)
        filled_quantity = 0
        cost = 0.0
        traded = 0.0
        for item in due:
            market_state = states.get(item.order.entity_id)
            if market_state is None:
                # 그날 봉이 없다 — 거래정지이거나 상장폐지다. 직전 종가로
                # 때우면 정지 중에 사고파는 학습이 된다.
                continue
            fill = simulate_fill(item.order, market_state, self.params.fill)
            if fill.status is FillStatus.REJECTED or fill.filled_quantity <= 0:
                continue
            gross = fill.filled_quantity * fill.avg_price
            side = BookSide(str(fill.side))
            fee, tax = self.params.rates.costs(side=side, gross=gross, currency=KRW)
            state.book = state.book.with_trade(
                Trade(
                    entity_id=fill.entity_id,
                    side=side,
                    quantity=float(fill.filled_quantity),
                    price=fill.avg_price,
                    currency=KRW,
                    fee=fee,
                    tax=tax,
                )
            )
            filled_quantity += fill.filled_quantity
            traded += gross
            # 충격비용은 체결가에 이미 녹아 있다. 보상이 보는 비용에는 그것도
            # 넣는다 — 수수료만 세면 대량 주문이 공짜로 보인다.
            cost += fee + tax + gross * abs(fill.impact_bps) / 10_000.0
            if side is BookSide.SELL:
                state.unsettled.append((today, gross - fee - tax))
            elif fill.entity_id not in state.entered:
                state.entered[fill.entity_id] = today
        for entity in list(state.entered):
            position = state.book.positions.get(entity)
            if position is None or position.quantity <= 0:
                del state.entered[entity]
        return filled_quantity, cost, traded

    def _available_cash(self, today: date) -> float:
        """주문가능금액. **NAV 도 아니고 장부 현금도 아니다** (accounting.md §1).

        `ledger.available_cash` 와 같은 규칙이되, 그 함수는 창고의 ``trades``
        에서 최근 매도를 되짚는다. 이 환경은 창고에 체결을 적지 않으므로
        (모듈 독스트링) 같은 값을 메모리에서 센다 — 규칙은 하나다: **오늘 판
        돈은 결제일까지 못 쓴다.**
        """
        state = self._state
        cash = float(state.book.cash.get(KRW, 0.0))
        days = self.params.settlement_days
        if days <= 0:
            return max(0.0, cash)
        index = state.sessions.index(today)
        cutoff = state.sessions[max(0, index - days + 1)]
        state.unsettled = [(day, amount) for day, amount in state.unsettled if day >= cutoff]
        held = sum(amount for _day, amount in state.unsettled)
        return max(0.0, cash - held)

    def _prices(self, entities: Sequence[str]) -> dict[str, float]:
        if not entities:
            return {}
        return self.reader.stats(self.as_of, list(entities)).prices

    def _value(self) -> nav_module.Valuation:
        """장부를 NAV 로 접는다. **가격이 없는 보유 종목은 직전 가격으로 든다.**

        0 으로 치면 그 종목이 사라진 것과 같아져 NAV 가 조용히 떨어지고, 그게
        낙폭으로 기록된다 (`accounting.nav.value`). 그 판단은 호출자 몫이라고
        그 함수가 못박고 있어서 여기서 한다.
        """
        state = self._state
        held = [
            entity
            for entity, position in state.book.positions.items()
            if position.quantity != 0
        ]
        prices = dict(getattr(self, "_last_prices", {}))
        prices.update(self._prices(held))
        self._last_prices = prices
        missing = [entity for entity in held if entity not in prices]
        if missing:
            # 한 번도 가격을 본 적 없는 보유는 있을 수 없다(사려면 가격이
            # 있어야 한다). 남았다면 그건 회계가 아니라 버그다.
            raise KeyError(f"보유 중인데 가격 이력이 없다: {missing}")
        return nav_module.value(
            state.book,
            prices={entity: prices[entity] for entity in held},
            fx_rate=self._fx_rate(),
        )

    def _realized_weights(self, equity: float) -> Array:
        """슬롯별 **실현** 비중. 목표가 아니라 체결 결과다 (불변식 7).

        이것이 다음 관측의 `FEATURE_REALIZED_WEIGHT` 로 그대로 되먹여진다.
        빠지면 RL 은 자기가 하지 않은 행동으로 보상받는다.
        """
        state = self._state
        out = np.zeros(self.params.n_max, dtype=np.float32)
        if equity <= 0:
            return out
        prices = getattr(self, "_last_prices", {})
        for index, slot in enumerate(state.slots):
            position = state.book.positions.get(slot.entity_id)
            if position is None or position.quantity <= 0:
                continue
            price = prices.get(slot.entity_id)
            if price is None:
                continue
            out[index] = float(position.quantity * price / equity)
        return out

    # -- 관측 -------------------------------------------------------------------

    def _blank(self) -> Obs:
        """에피소드가 끝난 뒤의 관측. Gymnasium 은 모양이 맞기만 하면 된다."""
        return {
            "portfolio": np.zeros(N_PORTFOLIO_FEATURES, dtype=np.float32),
            "assets": np.zeros((self.params.n_max, N_ASSET_FEATURES), dtype=np.float32),
            "mask": np.zeros(self.params.n_max, dtype=np.bool_),
        }

    def _observe(
        self, candidates: Sequence[tuple[str, float]] | None = None
    ) -> tuple[Obs, dict[str, Any]]:
        """오늘 결정 직전의 관측. **오늘 종가까지만 본다** — store 게이트가
        ``observed_at <= as_of`` 를 걸어 주므로, 여기서 미래를 보려면 as_of 를
        틀리게 넣는 수밖에 없다."""
        state = self._state
        as_of = self.as_of
        equity = state.nav

        if candidates is None:
            selection = self.reader.selection(as_of, equity=equity)
            picked: tuple[tuple[str, float], ...] = selection.candidates
            notes: tuple[str, ...] = selection.notes
        else:
            # 라이브 세션(observe_live)이 이미 뽑은 후보를 그대로 쓴다. 세션은
            # 보유를 알고 완충 구간(selector.exit_rank)까지 적용해 뽑는데, 여기서
            # 한 번 더 뽑으면 같은 날 후보가 두 벌이 되어 로그와 관측이 갈린다.
            picked = tuple((str(entity), float(score)) for entity, score in candidates)
            notes = ()
        held = [
            entity
            for entity, position in state.book.positions.items()
            if position.quantity > 0
        ]
        # **보유가 먼저다.** 슬롯이 모자랄 때 밀려나는 쪽이 보유면 그 종목은
        # 목표 0 이 되어 강제 청산된다 — 정책이 내리지 않은 결정이다.
        ordered = list(dict.fromkeys(held + [entity for entity, _score in picked]))
        scores = dict(picked)
        state.slots = [
            _Slot(entity_id=entity, score=scores.get(entity, 0.0))
            for entity in ordered[: self.params.n_max]
        ]
        dropped = len(ordered) - len(state.slots)

        assets, mask = self._asset_features()
        portfolio = self._portfolio_features()
        obs: Obs = {"portfolio": portfolio, "assets": assets, "mask": mask}
        info = {
            "candidates": tuple(slot.entity_id for slot in state.slots),
            "selection_notes": notes,
            "slots_dropped": dropped,
        }
        return obs, info

    # -- 라이브 ----------------------------------------------------------------

    def observe_live(
        self,
        *,
        session: date,
        book: Book,
        entered: Mapping[str, date],
        nav: float,
        drawdown_depth: float,
        candidates: Sequence[tuple[str, float]],
        last_turnover: float = 0.0,
        last_reflection: float = 1.0,
    ) -> tuple[Obs, dict[str, Any]]:
        """**실제 장부**로 오늘의 관측을 만든다. 학습이 보던 것과 같은 코드다.

        에피소드 대신 장부를 받는다 — 현금·보유·평균단가는 `accounting.ledger`
        가 창고에서 재구성한 그 장부이고, 낙폭은 `accounting.snapshot` 이 잰
        누적지수 기준 깊이다(양수, 0.12 = -12%). 피처 계산은 `_asset_features`
        `_portfolio_features` 를 그대로 부른다. **관측을 두 벌로 짜지 않는다** —
        학습과 실전이 다른 관측을 보면 정책은 학습 때와 다른 것을 보고 결정하고,
        그 차이는 성적표에 "정책이 나쁘다" 로만 보인다 (불변식 5).

        학습과 다를 수밖에 없는 세 가지는 여기서 정하고 적어 둔다:

        - **남은 스텝 비율**(portfolio[20]): 라이브에는 에피소드 끝이 없다.
          세션을 에피소드 한복판(0.5)에 앉힌다 — 뒤로 에피소드 길이만큼의
          거래일, 앞으로 같은 수의 자리(날짜는 자리표시)를 붙인 창을 쓴다.
          0 이나 1 을 주면 정책이 에피소드 첫날·마지막날의 습관을 꺼내는데,
          그런 날은 라이브에 없다.
        - **보유 경과일**: 창 시작보다 오래 든 종목은 창 시작일로 잘라 센다.
          학습에서는 에피소드가 현금으로 시작해 경과일이 에피소드 길이를 넘을
          수 없었으므로, 넘는 값을 주면 정책이 본 적 없는 입력이 된다.
        - **직전 회전율·반영률**: 호출자가 창고(`trades`·`realized_weights`)
          에서 읽어 넘긴다. 환경 안에서는 스텝이 만들던 값이다.

        관측 뒤 `decide_live` 로 액션을 목표 비중으로 푼다. `step` 은 부르지
        않는다 — 체결·회계는 라이브 집행기(executor)와 장부의 몫이다.
        """
        span = self.params.episode_days
        if session not in self._sessions:
            raise ValueError(f"{session} 은 이 환경의 거래일 목록에 없다")
        index = self._sessions.index(session)
        past = self._sessions[max(0, index - (span - 1)) : index + 1]
        # 앞쪽은 자리만 채운다. 거래일 달력은 1년 앞까지만 있고, 관측이 미래
        # 날짜로 하는 일은 "남은 칸 수" 를 세는 것뿐이다 — 시세·신호 조회는
        # 전부 `as_of`(오늘) 로 간다.
        future = [session + timedelta(days=offset) for offset in range(1, span)]
        window = past + future
        cursor = len(past) - 1
        clamped = {
            entity: (day if day >= window[0] else window[0])
            for entity, day in entered.items()
            if day <= session
        }
        self._clock = ReplayClock(self._moment(session))
        self._state = _EpisodeState(
            sessions=window,
            cursor=cursor,
            book=book,
            analysts=self._analyst_slots(),
            entered=clamped,
            nav=float(nav),
            last_turnover=float(last_turnover),
            last_reflection=float(last_reflection),
            last_drawdown=max(0.0, float(drawdown_depth)),
        )
        self._last_prices = {}
        # 평가(_value)가 보유 종목 시세를 `_last_prices` 에 채운다 — 실현
        # 비중·최소스텝 중앙값이 그 시세를 본다. 학습에서는 직전 스텝의
        # `_settle` 이 채워 두던 자리다. NAV 는 장부 쪽 값을 그대로 든다 —
        # 회계는 한 곳(accounting)에서만 한다.
        self._last_valuation = self._value()
        return self._observe(candidates)

    def decide_live(
        self, action: Mapping[str, Any]
    ) -> tuple[dict[str, float], dict[str, int]]:
        """액션 → (종목별 목표 비중, 종목별 진입 지연). `observe_live` 뒤에 부른다.

        `_decode` 와 같은 규칙이다 — 마스크 밖 비중은 현금, 종목 상한에서 깎인
        몫도 현금. 학습 때 정책이 받던 처리와 실전에서 받는 처리가 같아야
        액션 반영률이 집행기 얘기만 하게 된다.
        """
        if not self._state.sessions:
            raise RuntimeError("observe_live() 를 먼저 불러야 한다")
        targets, delays = self._decode(dict(action))
        slots = self._state.slots
        weights = {slot.entity_id: float(targets[i]) for i, slot in enumerate(slots)}
        waits = {slot.entity_id: int(delays[i]) for i, slot in enumerate(slots)}
        return weights, waits

    def _asset_features(self) -> tuple[Array, npt.NDArray[np.bool_]]:
        """종목 축 (N_max, 28). 후보가 모자라면 0 으로 패딩하고 마스크로 가린다."""
        state = self._state
        n_max = self.params.n_max
        assets = np.zeros((n_max, N_ASSET_FEATURES), dtype=np.float32)
        mask = np.zeros(n_max, dtype=np.bool_)
        if not state.slots:
            return assets, mask

        entities = [slot.entity_id for slot in state.slots]
        as_of = self.as_of
        combined, latest = self.reader.signals(as_of, entities)
        stats = self.reader.stats(as_of, entities)
        prices, adv, volatility = stats.prices, stats.adv, stats.volatility
        betas = self.reader.betas(as_of, entities)
        # 오늘 값은 이미 손에 있다. 다시 읽으면 캐시가 없는 경로에서 같은
        # 세션의 시세를 두 번 산다.
        oracle = self._oracle(entities, prices) if self.oracle_leak else {}
        realized = self._realized_weights(state.nav)
        today = state.sessions[state.cursor]
        equity = max(state.nav, 1.0)

        for index, slot in enumerate(state.slots):
            entity = slot.entity_id
            mask[index] = True
            row = assets[index]
            row[FEATURE_SCORE] = float(combined.get(entity, 0.0))
            for slot_index, analyst in enumerate(state.analysts):
                pair = latest.get((entity, analyst))
                if pair is None:
                    continue
                base = FEATURE_ANALYST_BASE + slot_index * 2
                row[base] = pair[0]
                row[base + 1] = pair[1]
            row[FEATURE_REALIZED_WEIGHT] = realized[index]
            price = float(prices.get(entity, 0.0))
            # 최소 스텝 — 1주를 살 때 비중이 얼마나 점프하나. 소액 구간에서
            # 목표 30% 가 실현 22% 가 되는 이유가 이 값이다 (executor/sizing.py).
            row[FEATURE_MIN_STEP] = price / equity if price > 0 else 0.0
            position = state.book.positions.get(entity)
            quantity = float(position.quantity) if position else 0.0
            adv_value = float(adv.get(entity, 0.0))
            if quantity > 0 and adv_value > 0 and price > 0:
                daily = adv_value * self.params.sizing.max_adv_ratio / price
                row[FEATURE_LIQUIDATION_DAYS] = float(quantity / daily) if daily > 0 else 0.0
            row[FEATURE_VOLATILITY] = float(volatility.get(entity, 0.0))
            row[FEATURE_BETA] = float(betas.get(entity, 0.0))
            entered = state.entered.get(entity)
            # 에피소드 길이로 나눈다 — 일수(126)를 그대로 넣으면 이 칸 하나가
            # 다른 스물일곱 칸을 압도한다 (§1 의 O(1) 규칙).
            row[FEATURE_HOLDING_DAYS] = (
                float(state.sessions.index(today) - state.sessions.index(entered))
                / float(self.params.episode_days)
                if entered in state.sessions
                else 0.0
            )
            if position is not None and position.avg_cost > 0 and price > 0:
                row[FEATURE_UNREALIZED] = float(price / position.avg_cost - 1.0)
            if self.oracle_leak:  # pragma: no cover - 배선 점검 전용 경로
                # **여기가 §0 의 정답이다.** 다른 칸을 다 채운 뒤 덮어쓴다 —
                # 위에서 섹터 칸을 건드리지 않으므로 실제로 겹치는 것은 없지만,
                # 순서를 뒤집으면 나중에 26번을 채우는 날 정답이 조용히 지워진다.
                row[FEATURE_ORACLE] = float(oracle.get(entity, 0.0))
            # 섹터 one-hot 축약(26~27)은 **비워 둔다.** 창고의 ``sectors`` 에
            # 들어 있는 값은 업종이 아니라 KOSDAQ 소속부이고(KOSPI 는 전부
            # 미상), 그걸로 만든 그룹은 상관 노출이 아니라 시장 등급이다
            # (selector/pipeline.py 의 같은 판단). 가짜 그룹을 학습시키느니
            # 칸을 비운다 — 진짜 업종 분류가 들어오면 이 두 줄만 채운다.
        return assets, mask

    def _oracle(
        self, entities: Sequence[str], prices: dict[str, float]
    ) -> dict[str, float]:
        """**미래를 읽는다.** 5거래일 뒤 실제 초과수익, 종목별.

        오라클 카나리(§0)의 정답이다. `oracle_leak=True` 일 때만 불린다 —
        그 플래그가 꺼져 있으면 이 함수는 한 번도 실행되지 않는다.

        ## as_of 를 일부러 앞으로 민다

        불변식 1(창고 읽기는 전부 as_of 게이트 경유)을 **어기는 것이 목적인**
        유일한 자리다. 시계는 건드리지 않고 리더에만 미래 시각을 넘긴다 —
        `self.clock` 이 움직이면 그 뒤의 체결·회계까지 미래로 끌려가서, 카나리가
        "정책이 정답을 쓰는가" 가 아니라 "환경이 통째로 미래인가" 를 재게 된다.

        ## 지수를 빼는 이유

        보상은 초과수익이다(§3). 종목 수익만 알려주면 시장이 통째로 오르내리는
        몫까지 정답에 섞여서, 정책이 정답을 완벽히 봐도 보상을 못 맞힌다 —
        그 미스매치가 EV 를 깎아 배선 고장처럼 보인다.

        구간 끝 5일은 미래가 에피소드 밖이라 0 으로 둔다(250일 중 5일).
        지어낸 값을 넣으면 그 5일만 정답이 거짓말이 되고, 표에는 안 보인다.
        """
        state = self._state
        ahead = self._session_after(state.sessions[state.cursor], ORACLE_HORIZON)
        if ahead is None:
            return {}
        future = self._moment(ahead)
        then = self.reader.stats(future, list(entities)).prices
        # 지수의 5세션 수익률. 세션 캐시에 이미 구워져 있어 따로 읽지 않는다.
        benchmark = self.reader.scalars(future).index_returns[1]
        out: dict[str, float] = {}
        for entity in entities:
            before = float(prices.get(entity, 0.0))
            after = float(then.get(entity, 0.0))
            if before > 0.0 and after > 0.0:
                out[entity] = ((after / before - 1.0) - benchmark) / ORACLE_SCALE
        return out

    def _portfolio_features(self) -> Array:
        """포트폴리오 축 24칸. 순서는 `rl-training.md §1` 표 그대로다.

        표가 21개까지만 이름을 적어 두었고 나머지 3칸은 비어 있다. **직전
        스텝의 회전율·액션 반영률·유효 후보 비율**을 넣는다 — 셋 다 정책이
        자기 행동의 결과를 보는 값이라, 되먹임(불변식 7)의 연장선이다.
        """
        state = self._state
        params = self.params
        out = np.zeros(N_PORTFOLIO_FEATURES, dtype=np.float32)
        equity = max(state.nav, 1.0)
        # **평가를 다시 하지 않는다.** `step` 이 방금 낸 값을 쓴다 — 같은
        # as_of 로 두 번 접으면 비용만 두 배가 되고, 그 사이에 값이 달라지면
        # 관측과 보상이 다른 NAV 를 본다.
        valuation = getattr(self, "_last_valuation", None)

        depth = state.last_drawdown
        out[0] = depth
        # 예산 잔여율 — 30% 벽까지 얼마나 남았나. 낙폭 자체와 다른 정보다:
        # 벽이 임계치에서 오는 값이라 config 가 바뀌면 같은 낙폭도 다른 여유다.
        out[1] = max(0.0, 1.0 - depth / params.reward.drawdown_hard)
        out[2] = float(state.book.cash.get(KRW, 0.0)) / equity
        out[3] = float(state.book.cash.get(USD, 0.0)) * self._fx_rate() / equity
        out[4] = float(valuation.equity_kr / equity) if valuation else 0.0
        out[5] = (
            float(valuation.equity_us * valuation.fx_rate / equity) if valuation else 0.0
        )
        # 세션 스칼라는 **한 번만** 읽는다. 예전에는 여기서 환율·지수 계열을
        # 다시 퍼왔는데, 같은 스텝에서 `_fx_rate`·`_benchmark_return` 도 같은
        # 것을 읽어 같은 답을 서너 번 샀다.
        scalars = self._scalars()
        # **관측 칸은 전부 O(1) 스케일이어야 한다** (rl-training.md §1). 환율
        # 원값 1,478 이 그대로 들어가 가치 헤드 그래디언트를 1,000 대로 키웠다
        # (2026-08-27). 1,000 은 임계치가 아니라 단위 환산이라 config 가 아니다.
        out[6] = (
            math.log(scalars.fx_rate / 1000.0)
            if scalars.fx_rate is not None and scalars.fx_rate > 0
            else 0.0
        )
        out[7] = scalars.fx_volatility
        out[8] = scalars.rate_differential

        state_name = scalars.regime_state
        for index, name in enumerate(REGIME_STATES):
            out[9 + index] = 1.0 if name == state_name else 0.0

        for offset, value in enumerate(scalars.index_returns):
            out[15 + offset] = value

        # log 자본. 자본 단계마다 체결 가능성이 달라지므로(최소 주문금액·
        # 거래대금 상한) 정책이 규모를 알아야 한다. 선형으로 넣으면 억 단위
        # 값이 다른 피처를 압도한다.
        out[18] = float(math.log10(max(equity, 1.0))) / 10.0
        out[19] = self._median_min_step(equity)
        out[20] = 1.0 - state.cursor / max(1, len(state.sessions) - 1)
        out[21] = state.last_turnover
        out[22] = state.last_reflection
        out[23] = len(state.slots) / params.n_max
        return out

    def _median_min_step(self, equity: float) -> float:
        """슬롯들의 최소 스텝 중앙값. 소액 구간에서 이 값이 크면 목표비중을
        아무리 잘 내도 실현이 못 따라온다 — 액션 반영률의 상한이다."""
        prices = getattr(self, "_last_prices", {})
        steps = [
            prices[slot.entity_id] / equity
            for slot in self._state.slots
            if slot.entity_id in prices and equity > 0
        ]
        return float(np.median(steps)) if steps else 0.0

    # -- 시장 데이터 -------------------------------------------------------------
    #
    # 여기 있는 것은 전부 **세션 하나로 결정되는 값**이라 캐시 대상이다. 읽기는
    # `allocator/cache.SessionReader` 한 곳에 모여 있고, 캐시가 깔려 있으면
    # 같은 자리에서 파일을 읽는다 — env 는 어느 쪽인지 모른다.

    def _scalars(self) -> cache_module.SessionScalars:
        return self.reader.scalars(self.as_of)

    def _fx_rate(self) -> float:
        """원달러. **없으면 1.0 으로 때우지 않는다** (`ledger.fx_rate`).

        지금은 달러 포지션이 없어 NAV 에 영향이 없지만, 그렇다고 1.0 을 쓰면
        미장이 붙는 날(C4) 해외분이 1/1350 로 평가되고 그 낙폭이 킬스위치를
        건다. 없으면 없다고 말한다.
        """
        rate = self._scalars().fx_rate
        if rate is None:
            raise LookupError(f"{self.as_of.isoformat()} 시점 {FX_USDKRW} 환율이 없다")
        return rate

    def _benchmark_return(self) -> float:
        """그날의 혼합 벤치마크 수익률.

        `accounting.benchmark.level` 을 쓰지 않는다. 그 함수는 창고의
        ``nav_daily`` 에 적힌 **직전 기준일**에 사슬을 건다 — 이 환경은 창고에
        아무것도 적지 않으므로 걸 자리가 없다. 대신 지수 종가에서 직접 낸다.

        ⚠️ 지금 국장 한 시장만 굴리므로 KR 지수만 본다. 미장이 붙는 C4 에서
        `nav.blended_benchmark` 로 갈아 끼운다 — 그때까지 `benchmark.us_weight`
        는 이 환경에서 쓰이지 않는다.
        """
        return self._scalars().index_returns[0]

    def _regime_state(self) -> str:
        """레짐. `analysts/regime.py` 의 판정을 그대로 쓴다."""
        return self._scalars().regime_state

    def _rate_differential(self) -> float:
        """한미 정책금리차(%p). 못 찾으면 0 — 관측에 결측을 표현할 자리가 없다."""
        return self._scalars().rate_differential


# -- 순수 도우미 ----------------------------------------------------------------


def _reflection_rate(targets: Array, realized: Array) -> float:
    """**액션 반영률** — RL 이 낸 결정 중 실제로 집행된 비율.

    `executor.pipeline.action_reflection_rate` 와 **같은 식**이다. 식이 갈리면
    학습 로그의 30% 와 화면의 30% 가 다른 숫자가 되고, 그러면 CLAUDE.md 의
    경고선이 아무 뜻도 없어진다.
    """
    target = float(np.abs(targets[: realized.shape[0]]).sum())
    if target <= 0:
        return 0.0
    gap = float(np.abs(targets[: realized.shape[0]] - realized).sum())
    return max(0.0, min(1.0, 1.0 - gap / target))
