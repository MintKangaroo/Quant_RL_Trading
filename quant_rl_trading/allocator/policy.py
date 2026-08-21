"""정책 네트워크 — `docs/design/rl-training.md §2` 를 그대로 옮긴 것.

    assets (N,28) ──> per-asset MLP (28→128) ──┐
                                                ├─> Transformer Encoder ×2
    portfolio (24) ──> MLP (24→128) ─ CLS 토큰 ─┘   (d=128, heads=4, ff=256)
                                                     **위치 인코딩 없음**
                              │
            ┌─────────────────┼──────────────────┐
            ▼                 ▼                  ▼
      weights head      delay head          value head
      (128→1) + 현금    (128→4) per asset   (attention pool → MLP → 1)

## 위치 인코딩을 넣지 않는다

넣는 순간 "3번 슬롯" 이라는, 세상에 존재하지 않는 개념을 학습한다. 후보
목록의 순서는 그날 스코어 정렬이 만든 우연이고, 같은 포트폴리오를 다른
순서로 준 것이 다른 결정이 되어서는 안 된다. 선행 프로젝트가 flatten 을
썼다면 그것이 실패 원인 중 하나다 (§2).

이 성질은 주석으로 지켜지지 않는다. `tests/allocator/test_policy.py` 의
순열 불변성 시험이 1e-5 로 잡는다.

## 왜 Dirichlet 인가 (§1)

softmax+Gaussian 은 심플렉스 제약(합=1, ≥0)을 깨서 클리핑이 필요하고,
**클리핑된 액션과 로그확률이 어긋나 정책 그래디언트가 편향된다.** 편향된
그래디언트는 학습이 안 될 때 원인 목록에도 안 올라온다 — 로그에는 아무것도
안 찍히기 때문이다. Dirichlet 은 심플렉스 위에 직접 정의된다.

## 인코더는 공유, 헤드만 분리

데이터가 적다 (§7: 5년 = 겹치지 않는 에피소드 5개). 가치망을 따로 두면
같은 표본으로 두 벌의 표현을 학습해야 한다.

## 환경과의 접점

관측·액션 규격은 `allocator/env.py` 가 정본이고, 여기서는 **공간에서
읽는다** (`PolicyConfig.from_spaces`). 규격을 양쪽에 손으로 적으면 언젠가
한쪽만 바뀌고, 그때 나는 고장은 "obs 42 vs 모델 128" 처럼 조용하다.

`fx_alloc` 액션에는 **헤드가 없다.** §2 의 구조도에 헤드가 셋뿐이고, 환경도
지금은 그 값을 `info` 에 남길 뿐 환전하지 않는다 (C4 이전). Beta 헤드를
지금 다는 것은 아무 데도 쓰이지 않는 액션 차원에 그래디언트를 흘리는 일이다
— `to_env_action` 이 0.0 을 채워 보낸다. 미장이 들어오는 4-7 에서 헤드를
붙이면 그 전에 학습한 정책이 못 쓰게 되므로, 그때 이 파일을 다시 연다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import numpy as np
import torch
import torch.nn.functional as F
from torch import Tensor, nn
from torch.distributions import Categorical, Dirichlet

if TYPE_CHECKING:  # pragma: no cover - 타입 전용
    from gymnasium import spaces

#: §2 의 숫자. 하이퍼파라미터로 열지 않는다 — §9 의 튜닝 우선순위에 구조가
#: 없다. 구조를 흔들면 어느 폴드의 성적이 무엇의 결과인지 말할 수 없게 된다.
D_MODEL = 128
N_HEADS = 4
FEEDFORWARD = 256
N_LAYERS = 2

#: §1 의 수치 안정 규약. 바닥은 0 나눗셈을, 천장은 폭주를 막는다.
#: concentration 합이 튀는 것은 §10 이 감시하는 경고 지표이기도 하다.
CONCENTRATION_FLOOR = 1e-3
CONCENTRATION_MAX = 1e3

#: 패딩 슬롯의 concentration. **바닥값을 그대로 쓴다** — 0 을 주면 Dirichlet
#: 이 정의되지 않고, 어중간하게 크면 살 수 없는 종목에 비중이 배정되어
#: 환경이 그것을 현금으로 되돌린다(= 정책이 하지 않은 결정이 된다).
MASKED_CONCENTRATION = CONCENTRATION_FLOOR

#: 정책 마지막 층의 직교 초기화 gain (§2). 초기 정책을 거의 균일하게 두어
#: 학습 초반에 한 종목으로 쏠리는 것을 막는다.
POLICY_HEAD_GAIN = 0.01


@dataclass(frozen=True)
class PolicyConfig:
    """정책망의 모양. **환경의 공간에서 유도하는 것이 정상 경로다.**"""

    n_max: int
    n_asset_features: int
    n_portfolio_features: int
    n_delay_choices: int
    d_model: int = D_MODEL
    n_heads: int = N_HEADS
    feedforward: int = FEEDFORWARD
    n_layers: int = N_LAYERS
    seed: int | None = None
    #: α 를 만드는 방식. ``"softplus"``(기존) | ``"simplex"``.
    #:
    #: **왜 갈랐는가.** softplus 는 "어느 종목을 좋아하나" 와 "얼마나
    #: 확신하나" 를 한 숫자에 얹는다. 한 종목의 비중을 키우려면 그 칸의 α 를
    #: 올려야 하는데, 그러면 Σα 도 같이 올라가 표본의 흩어짐이 줄어든다.
    #: 둘이 엉켜 있어서 **선호를 크게 말하려면 탐색을 포기해야 한다.**
    #:
    #: 실측 2026-08-20: 오라클을 관측에 꽂아 기여도 1위(z +4.29)를 만들어도
    #: NAV 는 +1.47% 였다(정답대로 따르면 2.9배인 환경에서). α 변동계수는
    #: 0.095 에 머물렀다 — 정책은 무엇이 좋은지 알면서 그것을 10% 어치로만
    #: 말했다.
    #:
    #: ``simplex`` 는 α = softmax(logits) · κ 다. softmax 라 상대 비중이
    #: 지수적으로 벌어지고, Σα 는 κ 로 고정돼 탐색량이 따로 논다.
    concentration_mode: str = "softplus"
    #: ``simplex`` 의 총 집중도 κ. ``None`` 이면 살아 있는 칸 수 — 그러면
    #: 평균 α 가 1 이라 기존 규모(softplus(0)≈0.69)와 크게 다르지 않다.
    concentration_total: float | None = None

    @classmethod
    def from_spaces(
        cls,
        observation_space: spaces.Dict,
        action_space: spaces.Dict,
        *,
        seed: int | None = None,
    ) -> PolicyConfig:
        """`LatticeEnv` 의 공간을 그대로 읽는다.

        규격을 두 곳에 적지 않기 위해서다. 여기서 던지는 예외는 학습을
        시작하기 전에 나므로, 200k 스텝을 돌린 뒤에 "모양이 안 맞았다" 를
        알게 되는 것보다 싸다.
        """
        # gymnasium 의 `Dict.__getitem__` 은 `Space[Any]` 를 준다. 모양을
        # 읽으려면 좁혀야 하고, 어긋나면 아래 검사가 잡는다.
        n_max, n_asset_features = cast("spaces.Box", observation_space["assets"]).shape
        (n_portfolio_features,) = cast("spaces.Box", observation_space["portfolio"]).shape
        (n_mask,) = cast("spaces.Box", observation_space["mask"]).shape
        (n_weights,) = cast("spaces.Box", action_space["weights"]).shape
        delay_nvec = cast("spaces.MultiDiscrete", action_space["delay"]).nvec

        if n_mask != n_max:
            raise ValueError(f"mask {n_mask} 칸 vs assets {n_max} 줄 — 규격 불일치")
        if n_weights != n_max + 1:
            raise ValueError(f"weights 는 {n_max + 1} 칸이어야 한다(마지막이 현금): {n_weights}")
        if len(delay_nvec) != n_max or len(set(delay_nvec.tolist())) != 1:
            raise ValueError(f"delay 는 종목마다 같은 선택지 수여야 한다: {delay_nvec}")

        return cls(
            n_max=int(n_max),
            n_asset_features=int(n_asset_features),
            n_portfolio_features=int(n_portfolio_features),
            n_delay_choices=int(delay_nvec[0]),
            seed=seed,
        )


@dataclass(frozen=True)
class PolicyOutput:
    """한 번의 forward 가 낸 분포 모수와 가치.

    분포 객체가 아니라 **모수**를 들고 다닌다. PPO(4-5)는 같은 관측을 여러
    epoch 돌려보므로, 분포는 필요할 때 만들고 모수는 로깅한다 (§10 의
    "Dirichlet concentration 합" 이 이 값이다).
    """

    concentration: Tensor  # (B, N+1) — 마지막 칸이 현금
    delay_logits: Tensor  # (B, N, C)
    value: Tensor  # (B,)
    mask: Tensor  # (B, N) bool

    @property
    def weights_dist(self) -> Dirichlet:
        return Dirichlet(self.concentration)

    @property
    def delay_dist(self) -> Categorical:
        return Categorical(logits=self.delay_logits)

    @property
    def weight_valid(self) -> Tensor:
        """비중 축의 유효 칸 (B, N+1). 현금은 언제나 유효하다."""
        cash = torch.ones_like(self.mask[:, :1])
        return torch.cat([self.mask, cash], dim=-1)

    def log_prob(self, weights: Tensor, delay: Tensor) -> Tensor:
        """결합 로그확률 (B,). 두 헤드는 조건부 독립이라 더한다.

        **패딩 슬롯은 분포에서 뺀다. 작은 concentration 으로 눌러 두는 것으로는
        부족하다.** `MASKED_CONCENTRATION` 은 1e-3 이고, Dirichlet 의 log_prob
        에는 `(a-1)·log x` 항이 있다. a=1e-3 이고 x 가 1e-38 근처면 그 항이
        슬롯당 +87 쯤 된다 — **NaN 이 아니라 거대한 유한값**이라 아무 데서도
        안 걸리고 어드밴티지를 통째로 오염시킨다.

        실측 2026-08-19(30슬롯 중 24개 유효):

            전체(패딩 포함)  log_prob +514.65 · entropy -6002.60
            유효 슬롯만      log_prob  +59.05 · entropy   -55.94

        패딩 6칸이 log_prob 의 90%, entropy 의 99% 를 만들고 있었다. PPO 비율은
        `exp(logp - old_logp)` 라 그 오염이 그대로 실린다 — 실제로 approx KL 이
        목표 0.02 에 대해 172 까지 튀었다.

        `modelops/canary_policy.py` 는 처음부터 이 방식을 피했고 독스트링에
        이유까지 적어 뒀다. 진짜 정책망만 그 함정에 있었다.

        지연도 유효 슬롯만 더한다 — 존재하지 않는 종목을 언제 살지는 결정이
        아니고, 그 항을 더하면 후보 수에 따라 로그확률의 눈금이 달라진다.
        """
        valid = self.weight_valid
        # 유효 칸만으로 다시 정규화한 심플렉스. 패딩 비중은 애초에 0 이라
        # 합에 영향이 없지만, 샘플이 numpy float32 를 거쳐 오면 1e-38 같은
        # 값이 남아 있어서 명시적으로 지운다.
        clean = _sanitize_simplex(torch.where(valid, weights, torch.zeros_like(weights)))
        weights_lp = _masked_dirichlet_log_prob(self.concentration, clean, valid)
        delay_lp: Tensor = self.delay_dist.log_prob(delay)  # type: ignore[no-untyped-call]
        return weights_lp + (delay_lp * self.mask).sum(dim=-1)

    def weights_log_prob(self, weights: Tensor) -> Tensor:
        """**비중 항만.** 지연을 안 뽑는 커리큘럼에서 쓴다.

        고정한 행동의 로그확률을 손실에 넣으면, 정책이 그 확률만 밀어서
        손실을 줄일 수 있다 — 행동은 하나도 안 바뀌는데. `train.DELAY_FIXED`
        주석에 실측이 있다.
        """
        valid = self.weight_valid
        clean = _sanitize_simplex(
            torch.where(valid, weights, torch.zeros_like(weights))
        )
        return _masked_dirichlet_log_prob(self.concentration, clean, valid)

    def weights_entropy(self) -> Tensor:
        """비중 항만의 엔트로피. `weights_log_prob` 과 짝이다."""
        return _masked_dirichlet_entropy(self.concentration, self.weight_valid)

    def entropy(self) -> Tensor:
        """(B,). 비중·지연 둘 다 **유효 슬롯만** 센다 — 이유는 `log_prob` 과 같다."""
        weights_ent = _masked_dirichlet_entropy(self.concentration, self.weight_valid)
        delay_ent: Tensor = self.delay_dist.entropy()  # type: ignore[no-untyped-call]
        return weights_ent + (delay_ent * self.mask).sum(dim=-1)


def _masked_dirichlet_log_prob(
    concentration: Tensor, weights: Tensor, valid: Tensor
) -> Tensor:
    """유효 칸만으로 세운 Dirichlet 의 log_prob (B,).

    `torch.distributions.Dirichlet` 은 차원을 골라 쓸 수 없어서 식을 직접
    쓴다. 배치마다 유효 칸 수가 다르므로 마스크로 접는 편이 텐서를 쪼개
    도는 것보다 싸고, 무엇보다 **모양이 고정**이라 컴파일·배치가 깨지지 않는다.

        log p = Σ_valid (a_i - 1)·log x_i + lgamma(Σ_valid a_i)
                - Σ_valid lgamma(a_i)
    """
    a = torch.where(valid, concentration, torch.ones_like(concentration))
    x = torch.where(valid, weights, torch.ones_like(weights))
    # log x 는 유효 칸에서만 살아 있다. 무효 칸은 a=1·x=1 로 두어 항이 0 이 된다.
    term = ((a - 1.0) * torch.log(x)) * valid
    log_norm = torch.lgamma((a * valid).sum(dim=-1)) - (torch.lgamma(a) * valid).sum(dim=-1)
    return term.sum(dim=-1) + log_norm


def _masked_dirichlet_entropy(concentration: Tensor, valid: Tensor) -> Tensor:
    """유효 칸만으로 세운 Dirichlet 의 엔트로피 (B,).

        H = log B(a) + (a0 - K)·ψ(a0) - Σ (a_i - 1)·ψ(a_i)

    K 는 **유효 칸 수**다. 전체 칸 수를 쓰면 패딩이 몇 개냐에 따라 엔트로피
    보너스의 눈금이 달라져, 후보가 적은 날 정책이 까닭 없이 넓어진다.
    """
    a = torch.where(valid, concentration, torch.ones_like(concentration))
    a0 = (a * valid).sum(dim=-1)
    k = valid.sum(dim=-1).to(a.dtype)
    log_beta = (torch.lgamma(a) * valid).sum(dim=-1) - torch.lgamma(a0)
    digamma_term = ((a - 1.0) * torch.digamma(a) * valid).sum(dim=-1)
    return log_beta + (a0 - k) * torch.digamma(a0) - digamma_term


#: 표본 비중의 바닥. **`finfo.tiny`(1e-38)를 쓰지 않는다.**
#:
#: Dirichlet 의 log_prob 에는 `(a-1)·log x` 항이 있고, 이 정책은 concentration
#: 이 1 보다 작을 수 있다(softplus(0)=0.693). 그러면 `a-1` 이 음수라 **x 가
#: 작을수록 항이 커진다.** 1e-38 이면 log x = -87 이라 슬롯당 +26 쯤 되고,
#: 그 값이 PPO 비율 `exp(logp - old_logp)` 에 그대로 실린다.
#:
#: 참조 구현(`modelops/canary_policy.SAMPLE_FLOOR`)이 처음부터 1e-9 를 쓴다.
#: 같은 값으로 맞춘다.
#:
#: **이걸 바꿔도 approx KL 은 안 달라졌다**(2026-08-19 실측 172.388 →
#: 172.388). 즉 지금 관측되는 KL 폭주의 원인은 이것이 아니다 — 실제 표본이
#: 1e-38 까지 내려가지 않는다. 그래도 되돌리지 않는 이유는, 이 항이 언제
#: 커지는지가 **concentration 에 달려 있고** 학습이 진행되면 1 보다 훨씬
#: 작아질 수 있기 때문이다. 안 터진 지뢰를 그대로 두지 않는다.
#:
#: **지지집합을 바꾸는 클리핑이 아니다.** 1e-9 아래의 비중은 1주도 못 사는
#: 값이라 어차피 체결에서 사라진다(`executor.sizing` 최소주문금액).
SAMPLE_FLOOR = 1e-9


def _sanitize_simplex(weights: Tensor) -> Tensor:
    """정확한 0 과 **극단적으로 작은 값**을 지운다.

    Dirichlet 의 log_prob 은 0 에서 발산한다. 0 만 막으면 충분할 것 같지만,
    concentration 이 1 보다 작을 때는 0 근처의 값도 로그확률을 지배한다 —
    `SAMPLE_FLOOR` 주석 참조.
    """
    clamped = weights.clamp_min(SAMPLE_FLOOR)
    return clamped / clamped.sum(dim=-1, keepdim=True)


class AllocatorPolicy(nn.Module):
    """비중·지연·가치를 함께 내는 순열 불변 정책망 (§2)."""

    def __init__(self, config: PolicyConfig) -> None:
        super().__init__()
        self.config = config
        d = config.d_model
        # 하위 모듈 생성이 전역 RNG 를 먹는다(nn.Linear 의 기본 초기화). 시드를
        # 준 경우에는 그 소비까지 되돌린다 — 안 그러면 "정책망을 언제 만들었나"
        # 가 환경의 에피소드 샘플링을 바꾼다.
        rng_state = torch.random.get_rng_state() if config.seed is not None else None

        self.asset_encoder = nn.Sequential(
            nn.Linear(config.n_asset_features, d),
            nn.GELU(),
            nn.Linear(d, d),
        )
        self.portfolio_encoder = nn.Sequential(
            nn.Linear(config.n_portfolio_features, d),
            nn.GELU(),
            nn.Linear(d, d),
        )

        layer = nn.TransformerEncoderLayer(
            d_model=d,
            nhead=config.n_heads,
            dim_feedforward=config.feedforward,
            dropout=0.0,  # PPO 는 같은 표본을 여러 epoch 돈다. 드롭아웃이 켜져
            # 있으면 같은 관측의 로그확률이 매번 달라져 비율이 잡음이 된다.
            activation="gelu",
            batch_first=True,
            norm_first=True,  # 잔차 앞 정규화. 워밍업 없이도 초기 학습이 안정적이다
        )
        self.encoder = nn.TransformerEncoder(
            layer,
            num_layers=config.n_layers,
            # **nested tensor 최적화를 끈다.** 켜면 패딩을 접어서 계산하는데,
            # 그 경로는 마스크된 자리에 다른 수치를 남긴다. 순열 불변성 시험의
            # 1e-5 를 통과하지 못하고, 무엇보다 학습·평가 경로가 갈린다.
            enable_nested_tensor=False,
        )

        self.weight_head = nn.Linear(d, 1)  # 종목당 로짓
        self.cash_head = nn.Linear(d, 1)  # CLS → 현금 로짓
        self.delay_head = nn.Linear(d, config.n_delay_choices)

        self.value_pool = nn.Linear(d, 1)  # attention pooling 점수
        self.value_head = nn.Sequential(
            nn.Linear(d, d),
            nn.GELU(),
            nn.Linear(d, 1),
        )

        self._reset_parameters()
        if rng_state is not None:
            torch.random.set_rng_state(rng_state)

    # -- 초기화 -----------------------------------------------------------------

    def _reset_parameters(self) -> None:
        """직교 초기화 (§2). 시드를 주면 같은 가중치가 나온다 (§11).

        전역 RNG 를 건드리지 않고 지역 generator 로 뽑는다. 전역 시드를
        갈아 끼우면 정책망을 만든 순간 환경의 에피소드 샘플링까지 따라
        움직여, 같은 시드로 두 번 돌린 학습이 갈릴 수 있다.
        """
        generator = None
        if self.config.seed is not None:
            generator = torch.Generator().manual_seed(self.config.seed)

        def init(module: nn.Module, gain: float) -> None:
            for name, param in module.named_parameters(recurse=True):
                if param.dim() >= 2:
                    nn.init.orthogonal_(param, gain=gain, generator=generator)
                elif "bias" in name:
                    nn.init.zeros_(param)
                # LayerNorm 의 weight 는 1 차원이다. 기본값(전부 1)이 곧
                # 항등이라 손대지 않는다 — 직교화 대상이 아니다.

        root2 = 2.0**0.5
        init(self.asset_encoder, root2)
        init(self.portfolio_encoder, root2)
        init(self.encoder, root2)
        init(self.value_pool, 1.0)
        init(self.value_head, root2)
        # 가치 헤드의 마지막 층은 1.0 으로 되돌린다. 출력이 스칼라 하나라
        # sqrt(2) 를 두면 초기 가치 추정이 쓸데없이 크게 흔들린다.
        last = cast(nn.Linear, self.value_head[-1])
        nn.init.orthogonal_(last.weight, gain=1.0, generator=generator)

        # 정책 마지막 층만 gain 0.01 (§2).
        for head in (self.weight_head, self.cash_head, self.delay_head):
            nn.init.orthogonal_(head.weight, gain=POLICY_HEAD_GAIN, generator=generator)
            nn.init.zeros_(head.bias)

    # -- forward ----------------------------------------------------------------

    def forward(self, portfolio: Tensor, assets: Tensor, mask: Tensor) -> PolicyOutput:
        """(B,24) · (B,N,28) · (B,N) → 분포 모수와 가치.

        ``mask`` 는 True 가 **유효 후보**다 (환경의 규격 그대로). torch 의
        `src_key_padding_mask` 는 반대로 True 가 "무시" 라, 뒤집는 곳을 여기
        한 군데로 모은다.
        """
        self._check_shapes(portfolio, assets, mask)
        mask = mask.bool()

        cls = self.portfolio_encoder(portfolio).unsqueeze(1)  # (B,1,d)
        tokens = torch.cat([cls, self.asset_encoder(assets)], dim=1)  # (B,N+1,d)

        # CLS 는 언제나 유효하다. 후보가 하나도 없는 스텝에서도 이 토큰이
        # 살아 있어야 attention softmax 가 전부 -inf 를 보고 NaN 을 내지 않는다.
        valid = torch.cat([torch.ones_like(mask[:, :1]), mask], dim=1)  # (B,N+1)
        encoded = self.encoder(tokens, src_key_padding_mask=~valid)

        cls_out, asset_out = encoded[:, 0], encoded[:, 1:]

        # -- weights: 헤드 쪽에도 마스크를 건다 (§2) --------------------------
        asset_logits = self.weight_head(asset_out).squeeze(-1)  # (B,N)
        cash_logit = self.cash_head(cls_out)  # (B,1)
        logits = torch.cat([asset_logits, cash_logit], dim=-1)  # (B,N+1)

        cash_valid = torch.ones_like(valid[:, :1])
        weight_valid = torch.cat([mask, cash_valid], dim=-1)

        if self.config.concentration_mode == "simplex":
            # α = softmax(logits) · κ. **"무엇을" 과 "얼마나 확신하나" 를
            # 가른다** — PolicyConfig.concentration_mode 주석 참고.
            #
            # 죽은 칸은 softmax 전에 -inf 로 눌러 확률을 0 으로 만든다.
            # 나중에 바닥값으로 덮긴 하지만, 여기서 안 빼면 그 칸이 살아
            # 있는 칸들의 몫을 나눠 가져 κ 가 조용히 새어 나간다.
            masked_logits = logits.masked_fill(~weight_valid, float("-inf"))
            share = torch.softmax(masked_logits, dim=-1)
            if self.config.concentration_total is not None:
                kappa = float(self.config.concentration_total)
            else:
                # 살아 있는 칸 수. 평균 α 가 1 이 되어 기존 규모와 맞는다.
                kappa = 0.0  # 아래에서 배치별로 센다
            total = (
                torch.full_like(share[:, :1], kappa)
                if kappa
                else weight_valid.sum(dim=-1, keepdim=True).to(share.dtype)
            )
            concentration = (share * total + CONCENTRATION_FLOOR).clamp(
                max=CONCENTRATION_MAX
            )
        else:
            concentration = (F.softplus(logits) + CONCENTRATION_FLOOR).clamp(
                max=CONCENTRATION_MAX
            )
        # 패딩 슬롯은 로짓과 무관하게 바닥값으로 눌러 앉힌다. softplus 의
        # 언더플로에 기대지 않는 이유는, 기댈 경우 로짓이 커지는 방향으로
        # 그래디언트가 흘러 언젠가 바닥을 뚫고 올라오기 때문이다.
        concentration = torch.where(
            weight_valid,
            concentration,
            torch.full_like(concentration, MASKED_CONCENTRATION),
        )

        # -- delay: 패딩 슬롯은 균일분포로 눌러 둔다 --------------------------
        delay_logits = self.delay_head(asset_out)  # (B,N,C)
        delay_logits = torch.where(mask.unsqueeze(-1), delay_logits, torch.zeros_like(delay_logits))

        # -- value: attention pooling -----------------------------------------
        scores = self.value_pool(encoded).squeeze(-1)  # (B,N+1)
        scores = scores.masked_fill(~valid, float("-inf"))
        pooled = (torch.softmax(scores, dim=-1).unsqueeze(-1) * encoded).sum(dim=1)
        value = self.value_head(pooled).squeeze(-1)  # (B,)

        return PolicyOutput(
            concentration=concentration,
            delay_logits=delay_logits,
            value=value,
            mask=mask,
        )

    def _check_shapes(self, portfolio: Tensor, assets: Tensor, mask: Tensor) -> None:
        """규격 불일치를 **첫 스텝에서** 터뜨린다.

        브로드캐스팅이 조용히 삼키면 학습 곡선만 이상해지고, 그때는 원인이
        구조인지 시장인지 구분할 수 없다 (§5 의 ④).
        """
        cfg = self.config
        if assets.shape[-1] != cfg.n_asset_features:
            raise ValueError(
                f"assets 피처 {assets.shape[-1]} 개, 정책망은 {cfg.n_asset_features} 개"
            )
        if portfolio.shape[-1] != cfg.n_portfolio_features:
            raise ValueError(
                f"portfolio {portfolio.shape[-1]} 칸, 정책망은 {cfg.n_portfolio_features} 칸"
            )
        if assets.shape[:-1] != mask.shape:
            raise ValueError(f"assets {assets.shape} 와 mask {mask.shape} 가 어긋난다")
        if portfolio.dim() != 2 or assets.dim() != 3:
            raise ValueError("배치 축이 필요하다: portfolio (B,F), assets (B,N,F)")

    # -- 액션 -------------------------------------------------------------------

    @torch.no_grad()
    def act(
        self,
        portfolio: Tensor,
        assets: Tensor,
        mask: Tensor,
        *,
        deterministic: bool = False,
        generator: torch.Generator | None = None,
    ) -> dict[str, Tensor]:
        """관측 → 액션·로그확률·가치. 롤아웃 수집용.

        ``deterministic`` 은 평가 전용이다 (§8 의 검증·테스트). Dirichlet 의
        평균을, Categorical 의 argmax 를 쓴다 — 최빈값이 아니라 평균인 이유는
        concentration 이 1 보다 작을 때 최빈값이 심플렉스 꼭짓점이라 사실상
        한 종목 몰빵이 되기 때문이다.
        """
        out = self(portfolio, assets, mask)
        if deterministic:
            weights = out.concentration / out.concentration.sum(dim=-1, keepdim=True)
            delay = out.delay_logits.argmax(dim=-1)
        else:
            weights = _dirichlet_sample(out.concentration, generator)
            delay = _categorical_sample(out.delay_logits, generator)
        return {
            "weights": weights,
            "delay": delay,
            "log_prob": out.log_prob(weights, delay),
            "value": out.value,
            "entropy": out.entropy(),
        }

    def evaluate_actions(
        self, portfolio: Tensor, assets: Tensor, mask: Tensor, action: dict[str, Tensor]
    ) -> tuple[Tensor, Tensor, Tensor]:
        """PPO(4-5)가 부른다. (log_prob, entropy, value)."""
        out = self(portfolio, assets, mask)
        return (
            out.log_prob(action["weights"], action["delay"]),
            out.entropy(),
            out.value,
        )


def _dirichlet_sample(concentration: Tensor, generator: torch.Generator | None) -> Tensor:
    """Dirichlet 표본. generator 를 주면 재현된다 (§11).

    torch 의 `Dirichlet.sample` 은 generator 를 받지 않는다. Gamma 를 직접
    뽑아 정규화하는 것이 정의 그대로이고, 이 경로에서도 표본은 표현 가능한
    최소 양수 이상이라 log_prob 이 유한하다.
    """
    if generator is None:
        return Dirichlet(concentration).sample()
    gamma = torch._standard_gamma(concentration, generator=generator)
    gamma = gamma.clamp_min(torch.finfo(gamma.dtype).tiny)
    return gamma / gamma.sum(dim=-1, keepdim=True)


def _categorical_sample(logits: Tensor, generator: torch.Generator | None) -> Tensor:
    if generator is None:
        # Dirichlet 과 달리 Categorical.sample 에는 주석이 없다 (torch 쪽 사정).
        drawn: Tensor = Categorical(logits=logits).sample()  # type: ignore[no-untyped-call]
        return drawn
    probs = torch.softmax(logits, dim=-1)
    flat = probs.reshape(-1, probs.shape[-1])
    drawn = torch.multinomial(flat, 1, generator=generator)
    return drawn.reshape(probs.shape[:-1])


def to_env_action(action: dict[str, Tensor], index: int = 0) -> dict[str, Any]:
    """배치의 한 줄을 `LatticeEnv.step` 이 받는 모양으로 바꾼다.

    ``fx_alloc`` 은 **학습되는 값이 아니다.** 헤드가 없어서 0.0 을 채운다
    (모듈 독스트링). 환경은 이 값을 `info` 에 남길 뿐 환전하지 않으므로 지금
    궤적에 영향이 없고, 영향이 생기는 시점(C4)에 헤드를 붙인다.
    """
    return {
        "weights": action["weights"][index].detach().cpu().numpy().astype(np.float32),
        "delay": action["delay"][index].detach().cpu().numpy().astype(np.int64),
        "fx_alloc": np.zeros(1, dtype=np.float32),
    }
