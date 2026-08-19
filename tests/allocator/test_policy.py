"""정책망 계약 시험 — `rl-training.md §2`, M4-kickoff 4-3.

**합성 텐서만 쓴다.** 정책망은 순수 torch 라 창고가 필요 없고, 창고를 읽으면
시험 결과가 그날의 수집 상태에 달리게 된다.

여기서 증명하는 넷은 전부 "학습이 안 될 때 원인을 특정" 하기 위한 것이다.

1. **순열 불변성** — 위치 인코딩을 넣지 않은 이유의 증명 (§5 의 ④)
2. **마스킹** — 살 수 없는 슬롯에 비중이 가지 않는가
3. **심플렉스** — Dirichlet 표본이 지지집합 위에 있는가
4. **log_prob 유한성** — concentration 극단에서 NaN 이 안 나는가

배치는 2~4 로 둔다. 이 시험은 정책망의 계약을 보는 것이지 성능을 보는 것이
아니라, 큰 텐서는 시간만 먹는다.
"""

from __future__ import annotations

import numpy as np
import pytest
import torch
from gymnasium import spaces

from quant_rl_trading.allocator.policy import (
    CONCENTRATION_FLOOR,
    CONCENTRATION_MAX,
    MASKED_CONCENTRATION,
    AllocatorPolicy,
    PolicyConfig,
    PolicyOutput,
    to_env_action,
)

N_MAX = 8
N_VALID = 5
BATCH = 2


@pytest.fixture
def config() -> PolicyConfig:
    """작게 만든 정책망. 폭·층수는 §2 그대로 두고 후보 수만 줄인다 —
    순열 불변성과 마스킹은 후보 수에 의존하지 않는 성질이다."""
    return PolicyConfig(
        n_max=N_MAX,
        n_asset_features=28,
        n_portfolio_features=24,
        n_delay_choices=4,
        seed=7,
    )


@pytest.fixture
def policy(config: PolicyConfig) -> AllocatorPolicy:
    net = AllocatorPolicy(config)
    net.eval()
    return net


def _observation(
    *, n_valid: int = N_VALID, seed: int = 0
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    """합성 관측 한 배치. 패딩 자리는 환경과 같이 0 으로 둔다."""
    generator = torch.Generator().manual_seed(seed)
    portfolio = torch.randn(BATCH, 24, generator=generator)
    assets = torch.randn(BATCH, N_MAX, 28, generator=generator)
    mask = torch.zeros(BATCH, N_MAX, dtype=torch.bool)
    mask[:, :n_valid] = True
    assets[~mask] = 0.0
    return portfolio, assets, mask


# -- 1. 순열 불변성 -------------------------------------------------------------


def test_permutation_invariance(policy: AllocatorPolicy) -> None:
    """종목 순서를 섞어도 같은 결정이 나온다 (허용 오차 1e-5).

    후보 목록의 순서는 그날 스코어 정렬이 만든 우연이다. 이 시험이 깨지면
    정책망 어딘가에 위치 정보가 들어왔다는 뜻이고, 그 순간 §5 의 ④ 가 열린다.
    """
    portfolio, assets, mask = _observation()
    out = policy(portfolio, assets, mask)

    perm = torch.randperm(N_MAX, generator=torch.Generator().manual_seed(3))
    shuffled = policy(portfolio, assets[:, perm], mask[:, perm])

    # 종목 축 출력은 **같은 순열로** 따라 움직여야 한다 (등변).
    torch.testing.assert_close(
        shuffled.concentration[:, :N_MAX],
        out.concentration[:, perm],
        atol=1e-5,
        rtol=0,
    )
    torch.testing.assert_close(shuffled.delay_logits, out.delay_logits[:, perm], atol=1e-5, rtol=0)
    # 현금 로짓과 가치는 순서에 **불변**이다 — 집합의 함수여야 한다.
    torch.testing.assert_close(
        shuffled.concentration[:, -1], out.concentration[:, -1], atol=1e-5, rtol=0
    )
    torch.testing.assert_close(shuffled.value, out.value, atol=1e-5, rtol=0)


def test_padding_position_does_not_matter(policy: AllocatorPolicy) -> None:
    """유효 후보를 앞이 아니라 **뒤에** 놓아도 같은 답이 나온다.

    환경은 항상 앞에서부터 채우지만(`env._asset_features`), 정책망이 그
    관습에 기대면 후보 수집 순서가 바뀌는 날 조용히 다른 결정을 낸다.
    """
    portfolio, assets, mask = _observation()
    out = policy(portfolio, assets, mask)

    flipped_assets = assets.flip(dims=[1])
    flipped_mask = mask.flip(dims=[1])
    flipped = policy(portfolio, flipped_assets, flipped_mask)

    torch.testing.assert_close(
        flipped.concentration[:, :N_MAX].flip(dims=[1]),
        out.concentration[:, :N_MAX],
        atol=1e-5,
        rtol=0,
    )
    torch.testing.assert_close(flipped.value, out.value, atol=1e-5, rtol=0)


# -- 2. 마스킹 ------------------------------------------------------------------


def test_masked_slots_get_floor_concentration(policy: AllocatorPolicy) -> None:
    """패딩 슬롯의 concentration 은 바닥값이고, 현금은 살아 있다."""
    portfolio, assets, mask = _observation()
    out = policy(portfolio, assets, mask)

    padded = out.concentration[:, N_VALID:N_MAX]
    assert torch.all(padded == MASKED_CONCENTRATION)
    assert torch.all(out.concentration[:, :N_VALID] > MASKED_CONCENTRATION)
    assert torch.all(out.concentration[:, -1] > MASKED_CONCENTRATION)


def test_masked_weights_converge_to_zero(policy: AllocatorPolicy) -> None:
    """패딩 슬롯의 비중이 0 에 수렴한다.

    기댓값(concentration 비율)으로 보고, 표본으로도 확인한다. 환경은 패딩
    비중을 현금으로 되돌리므로(`env._decode`), 여기서 새는 비중은 정책이
    의도하지 않은 현금이 된다.
    """
    portfolio, assets, mask = _observation()
    out = policy(portfolio, assets, mask)

    mean = out.concentration / out.concentration.sum(dim=-1, keepdim=True)
    # 배치 한 줄이 패딩에 얹는 질량. 유효 슬롯 하나의 비중(≈1/6)에 견주면
    # 두 자릿수 아래다 — 바닥값 1e-3 이 만드는 산술적 하한이고, 후보 수가
    # 늘어도 이 비율은 유지된다.
    assert mean[:, N_VALID:N_MAX].sum(dim=-1).max().item() < 1e-2

    # 표본에서는 **거의 항상 정확히 0** 이다. concentration 1e-3 의 Dirichlet
    # 은 질량을 꼭짓점에 몰아넣기 때문이다. 평균이 아니라 "새는 빈도" 로 본다
    # — 드물게 한 번 터지는 표본 하나가 평균을 통째로 흔든다.
    torch.manual_seed(0)
    samples = out.weights_dist.sample((256,))  # (256, B, N+1)
    padded_mass = samples[..., N_VALID:N_MAX].sum(dim=-1)
    assert padded_mass.mean().item() < 1e-2, padded_mass.mean().item()
    # 1e-6 를 넘는 일 자체가 드물다. 남는 몇 %도 환경이 현금으로 되돌린다.
    leaked = (padded_mass > 1e-6).float().mean().item()
    assert leaked < 0.05, leaked


def test_padding_does_not_leak_into_valid_slots(policy: AllocatorPolicy) -> None:
    """패딩 자리에 쓰레기 값을 넣어도 유효 슬롯의 출력이 변하지 않는다.

    attention 쪽 마스크가 실제로 걸려 있는지 보는 시험이다. 헤드에서만
    가리면 이 시험이 깨진다 — §2 가 "양쪽에" 라고 적은 이유다.
    """
    portfolio, assets, mask = _observation()
    out = policy(portfolio, assets, mask)

    polluted = assets.clone()
    polluted[:, N_VALID:] = 37.0  # 0 이 아닌, 눈에 띄게 다른 값
    dirty = policy(portfolio, polluted, mask)

    torch.testing.assert_close(
        dirty.concentration[:, :N_VALID],
        out.concentration[:, :N_VALID],
        atol=1e-5,
        rtol=0,
    )
    torch.testing.assert_close(dirty.value, out.value, atol=1e-5, rtol=0)


def test_all_slots_masked_is_finite(policy: AllocatorPolicy) -> None:
    """후보가 하나도 없는 스텝에서도 NaN 이 나지 않는다.

    CLS 토큰을 항상 유효로 두는 이유가 여기 있다. attention softmax 가 전부
    -inf 를 보면 NaN 이 나고, 그 NaN 은 한 번만 나와도 가중치 전체를 오염시킨다.
    """
    portfolio, assets, mask = _observation(n_valid=0)
    out = policy(portfolio, assets, mask)

    assert torch.isfinite(out.concentration).all()
    assert torch.isfinite(out.value).all()
    # 전부 패딩이면 비중은 사실상 전액 현금이다.
    mean = out.concentration / out.concentration.sum(dim=-1, keepdim=True)
    assert mean[:, -1].min().item() > 0.98


# -- 3. 심플렉스 ---------------------------------------------------------------


def test_dirichlet_sample_lives_on_simplex(policy: AllocatorPolicy) -> None:
    """표본이 심플렉스 위에 있다 — 합 1, 전부 ≥ 0.

    이것이 Dirichlet 을 쓰는 이유 자체다 (§1). softmax+Gaussian 이면 여기서
    클리핑이 필요하고, 클리핑된 액션과 로그확률이 어긋난다.
    """
    portfolio, assets, mask = _observation()
    out = policy(portfolio, assets, mask)

    samples = out.weights_dist.sample((32,))
    assert torch.all(samples >= 0.0)
    torch.testing.assert_close(samples.sum(dim=-1), torch.ones(32, BATCH), atol=1e-5, rtol=0)


def test_act_output_is_accepted_by_env_action_space(policy: AllocatorPolicy) -> None:
    """`act` 의 결과가 환경의 액션 공간을 통과한다.

    "코드는 있는데 아무도 안 부른다" 가 이 저장소에서 제일 자주 나는 결함이라,
    정책망과 환경 사이의 접점을 시험으로 묶어 둔다.
    """
    portfolio, assets, mask = _observation()
    action = policy.act(portfolio, assets, mask)

    space = spaces.Dict(
        {
            "weights": spaces.Box(0.0, 1.0, shape=(N_MAX + 1,), dtype=np.float32),
            "delay": spaces.MultiDiscrete([4] * N_MAX),
            "fx_alloc": spaces.Box(0.0, 1.0, shape=(1,), dtype=np.float32),
        }
    )
    env_action = to_env_action(action, index=0)
    assert space.contains(env_action)


# -- 4. log_prob 유한성 --------------------------------------------------------


@pytest.mark.parametrize("value", [CONCENTRATION_FLOOR, 1.0, CONCENTRATION_MAX])
def test_log_prob_finite_at_concentration_extremes(value: float) -> None:
    """concentration 이 바닥이든 천장이든 log_prob 과 엔트로피가 유한하다.

    Dirichlet 의 밀도는 0 에서 발산한다. 아주 작은 concentration 은 표본을
    0 에 붙이므로, 여기가 NaN 이 나는 첫 자리다. NaN 은 PPO 의 한 스텝에서
    가중치 전체를 죽이고, 로그에는 손실이 nan 이라는 사실만 남는다.
    """
    concentration = torch.full((BATCH, N_MAX + 1), value)
    out = PolicyOutput(
        concentration=concentration,
        delay_logits=torch.zeros(BATCH, N_MAX, 4),
        value=torch.zeros(BATCH),
        mask=torch.ones(BATCH, N_MAX, dtype=torch.bool),
    )
    samples = out.weights_dist.sample((16,))
    delay = torch.zeros(16, BATCH, N_MAX, dtype=torch.long)

    log_prob = out.log_prob(samples, delay)
    assert torch.isfinite(log_prob).all(), log_prob
    assert torch.isfinite(out.entropy()).all()


def test_log_prob_finite_with_mixed_extremes(policy: AllocatorPolicy) -> None:
    """바닥과 천장이 **섞인** 경우. 마스킹이 만드는 것이 정확히 이 모양이다."""
    concentration = torch.full((BATCH, N_MAX + 1), CONCENTRATION_MAX)
    concentration[:, N_VALID:N_MAX] = CONCENTRATION_FLOOR
    out = PolicyOutput(
        concentration=concentration,
        delay_logits=torch.zeros(BATCH, N_MAX, 4),
        value=torch.zeros(BATCH),
        mask=torch.zeros(BATCH, N_MAX, dtype=torch.bool).index_fill_(
            1, torch.arange(N_VALID), True
        ),
    )
    samples = out.weights_dist.sample((16,))
    assert torch.isfinite(
        out.log_prob(samples, torch.zeros(16, BATCH, N_MAX, dtype=torch.long))
    ).all()


def test_log_prob_survives_exact_zero_weights(policy: AllocatorPolicy) -> None:
    """정확한 0 이 섞인 액션에서도 유한하다.

    액션은 numpy float32 로 환경에 갔다가 롤아웃 버퍼를 거쳐 돌아온다. 그
    왕복에서 1e-38 규모가 0 으로 내려앉을 수 있고, 그 0 이 PPO 의 두 번째
    epoch 에서 -inf 로 터진다.
    """
    portfolio, assets, mask = _observation()
    out = policy(portfolio, assets, mask)

    weights = out.weights_dist.sample()
    weights[:, N_VALID:N_MAX] = 0.0
    delay = torch.zeros(BATCH, N_MAX, dtype=torch.long)
    assert torch.isfinite(out.log_prob(weights, delay)).all()


def test_masked_delay_does_not_enter_log_prob(policy: AllocatorPolicy) -> None:
    """패딩 슬롯의 지연을 바꿔도 로그확률이 변하지 않는다.

    존재하지 않는 종목을 언제 살지는 결정이 아니다. 그 항이 섞이면 후보 수에
    따라 로그확률의 눈금이 달라져 PPO 의 비율이 관측마다 다른 척도를 갖는다.
    """
    portfolio, assets, mask = _observation()
    out = policy(portfolio, assets, mask)
    weights = out.weights_dist.sample()

    base = torch.zeros(BATCH, N_MAX, dtype=torch.long)
    other = base.clone()
    other[:, N_VALID:] = 3
    torch.testing.assert_close(
        out.log_prob(weights, base), out.log_prob(weights, other), atol=1e-6, rtol=0
    )


# -- 재현성·규격 ---------------------------------------------------------------


def test_same_seed_same_initialization(config: PolicyConfig) -> None:
    """같은 시드면 같은 초기 가중치 (§11).

    학습 곡선을 비교하려면 출발점이 같아야 한다. 여기가 갈리면 어블레이션
    결과가 전부 시드 잡음이다.
    """
    left = AllocatorPolicy(config)
    right = AllocatorPolicy(config)
    for (name, a), (_, b) in zip(left.named_parameters(), right.named_parameters(), strict=True):
        torch.testing.assert_close(a, b, atol=0, rtol=0, msg=name)


def test_different_seed_different_initialization(config: PolicyConfig) -> None:
    from dataclasses import replace

    left = AllocatorPolicy(config)
    right = AllocatorPolicy(replace(config, seed=config.seed + 1))
    assert not torch.equal(left.weight_head.weight, right.weight_head.weight)


def test_seeded_init_does_not_disturb_global_rng(config: PolicyConfig) -> None:
    """정책망을 만들어도 전역 RNG 스트림이 움직이지 않는다.

    움직이면 정책망 생성 순서가 환경의 에피소드 샘플링을 바꾼다 — 같은
    시드로 두 번 돌린 학습이 갈리는, 찾기 어려운 종류의 비재현성이다.
    """
    torch.manual_seed(11)
    expected = torch.randn(4)

    torch.manual_seed(11)
    AllocatorPolicy(config)
    assert torch.equal(torch.randn(4), expected)


def test_policy_head_starts_near_uniform(policy: AllocatorPolicy) -> None:
    """gain 0.01 의 효과 — 초기 비중이 거의 균일하다 (§2).

    초반부터 한 종목에 쏠리면 그 종목의 궤적만 보게 되고, 탐색이 시작되기
    전에 정책이 굳는다.
    """
    portfolio, assets, mask = _observation()
    out = policy(portfolio, assets, mask)
    valid = out.concentration[:, :N_VALID]
    assert (valid.max() - valid.min()).item() < 0.05


def test_config_from_spaces_matches_env_contract() -> None:
    """관측·액션 공간에서 모양을 읽는다. 규격을 두 곳에 적지 않는다."""
    observation_space = spaces.Dict(
        {
            "portfolio": spaces.Box(-np.inf, np.inf, shape=(24,), dtype=np.float32),
            "assets": spaces.Box(-np.inf, np.inf, shape=(30, 28), dtype=np.float32),
            "mask": spaces.Box(0, 1, shape=(30,), dtype=np.bool_),
        }
    )
    action_space = spaces.Dict(
        {
            "weights": spaces.Box(0.0, 1.0, shape=(31,), dtype=np.float32),
            "delay": spaces.MultiDiscrete([4] * 30),
            "fx_alloc": spaces.Box(0.0, 1.0, shape=(1,), dtype=np.float32),
        }
    )
    config = PolicyConfig.from_spaces(observation_space, action_space, seed=0)
    assert (config.n_max, config.n_asset_features) == (30, 28)
    assert (config.n_portfolio_features, config.n_delay_choices) == (24, 4)


def test_config_from_spaces_rejects_mismatch() -> None:
    """비중 칸이 하나 모자라면 **만들기 전에** 터진다."""
    observation_space = spaces.Dict(
        {
            "portfolio": spaces.Box(-np.inf, np.inf, shape=(24,), dtype=np.float32),
            "assets": spaces.Box(-np.inf, np.inf, shape=(30, 28), dtype=np.float32),
            "mask": spaces.Box(0, 1, shape=(30,), dtype=np.bool_),
        }
    )
    action_space = spaces.Dict(
        {
            "weights": spaces.Box(0.0, 1.0, shape=(30,), dtype=np.float32),
            "delay": spaces.MultiDiscrete([4] * 30),
            "fx_alloc": spaces.Box(0.0, 1.0, shape=(1,), dtype=np.float32),
        }
    )
    with pytest.raises(ValueError, match="현금"):
        PolicyConfig.from_spaces(observation_space, action_space)


def test_shape_mismatch_raises(policy: AllocatorPolicy) -> None:
    """피처 수가 다르면 조용히 브로드캐스팅되지 않고 예외가 난다."""
    with pytest.raises(ValueError, match="피처"):
        policy(
            torch.randn(BATCH, 24),
            torch.randn(BATCH, N_MAX, 27),
            torch.ones(BATCH, N_MAX, dtype=torch.bool),
        )


def test_gradients_reach_encoder(policy: AllocatorPolicy) -> None:
    """정책·가치 손실이 **공유 인코더**까지 흐른다 (§2).

    헤드만 학습되고 인코더가 얼어 있으면 EV 가 0 근처에 고착하는데, 그 증상은
    §5 의 어느 항목으로도 안 보인다.
    """
    portfolio, assets, mask = _observation()
    out = policy(portfolio, assets, mask)
    (out.concentration.sum() + out.value.sum()).backward()

    grad = policy.asset_encoder[0].weight.grad
    assert grad is not None and torch.isfinite(grad).all()
    assert grad.abs().sum().item() > 0.0


def test_act_is_reproducible_with_generator(policy: AllocatorPolicy) -> None:
    """generator 를 주면 같은 액션이 나온다 (§11).

    torch 의 `Dirichlet.sample` 은 generator 를 받지 않아서 전역 RNG 를 쓴다.
    32개 병렬 환경이 도는 중에 전역 스트림에 기대면 롤아웃이 재현되지 않고,
    재현되지 않는 롤아웃 위의 어블레이션은 아무것도 증명하지 못한다.
    """
    portfolio, assets, mask = _observation()

    def draw() -> dict[str, torch.Tensor]:
        return policy.act(portfolio, assets, mask, generator=torch.Generator().manual_seed(5))

    left, right = draw(), draw()
    for key in ("weights", "delay", "log_prob", "value"):
        torch.testing.assert_close(left[key], right[key], atol=0, rtol=0, msg=key)
    assert torch.isfinite(left["log_prob"]).all()
    assert torch.all(left["delay"] < 4)


def test_deterministic_act_uses_dirichlet_mean(policy: AllocatorPolicy) -> None:
    """평가 경로는 표본이 아니라 평균이다 (§8 의 검증·테스트 구간).

    concentration 이 1 보다 작을 때 Dirichlet 의 최빈값은 심플렉스 꼭짓점이라,
    최빈값을 쓰면 평가에서만 한 종목 몰빵이 된다.
    """
    portfolio, assets, mask = _observation()
    out = policy(portfolio, assets, mask)
    action = policy.act(portfolio, assets, mask, deterministic=True)

    expected = out.concentration / out.concentration.sum(dim=-1, keepdim=True)
    torch.testing.assert_close(action["weights"], expected, atol=1e-6, rtol=0)
    torch.testing.assert_close(action["weights"].sum(dim=-1), torch.ones(BATCH), atol=1e-6, rtol=0)


# -- 패딩 슬롯이 분포를 오염시키지 않는다 (2026-08-19) -------------------------


def test_패딩_슬롯이_로그확률을_지배하지_않는다() -> None:
    """**작은 concentration 으로 눌러 두는 것으로는 부족하다.**

    Dirichlet 의 log_prob 에는 `(a-1)·log x` 항이 있다. 패딩 슬롯의
    concentration 이 1e-3 이고 비중이 0 근처면 그 항이 슬롯당 +87 쯤 되는데,
    **NaN 이 아니라 거대한 유한값**이라 아무 데서도 안 걸린다.

    실측(고치기 전, 30슬롯 중 24개 유효): log_prob +514.65 · entropy -6002.60.
    유효 슬롯만 세면 +59.05 · -55.94 였다 — 패딩 6칸이 90%/99% 를 만들었다.

    그래서 **유효 칸 수만 바뀌고 나머지가 같으면 값이 안 변해야 한다.**
    """
    import torch

    from quant_rl_trading.allocator.policy import AllocatorPolicy, PolicyConfig

    torch.manual_seed(0)
    policy = AllocatorPolicy(
        PolicyConfig(
            n_max=30, n_asset_features=28, n_portfolio_features=24, n_delay_choices=3
        )
    )
    portfolio = torch.randn(4, 24)
    assets = torch.randn(4, 30, 28)
    delay = torch.zeros(4, 30, dtype=torch.long)

    narrow = torch.zeros(4, 30, dtype=torch.bool)
    narrow[:, :10] = True

    with torch.no_grad():
        out = policy(portfolio, assets, narrow)
        weights = out.weights_dist.sample()
        # 패딩 비중을 0 으로 둔다 — 환경(`_decode`)이 하는 그대로다.
        weights = torch.where(out.weight_valid, weights, torch.zeros_like(weights))
        log_prob = out.log_prob(weights, delay)
        entropy = out.entropy()

    # 패딩이 20칸이나 되는데도 값이 유한하고 상식적인 규모여야 한다.
    assert torch.isfinite(log_prob).all()
    assert torch.isfinite(entropy).all()
    assert log_prob.abs().max() < 200.0, f"패딩이 로그확률을 지배한다: {log_prob}"
    assert entropy.abs().max() < 200.0, f"패딩이 엔트로피를 지배한다: {entropy}"


def test_마스크_Dirichlet_은_torch_기본_구현과_같다() -> None:
    """식을 직접 썼으므로 참조와 맞는지 고정한다. 전부 유효하면 같은 값이다."""
    import torch
    from torch.distributions import Dirichlet

    from quant_rl_trading.allocator.policy import (
        _masked_dirichlet_entropy,
        _masked_dirichlet_log_prob,
    )

    torch.manual_seed(0)
    concentration = torch.rand(5, 7) + 0.3
    valid = torch.ones(5, 7, dtype=torch.bool)
    sample = Dirichlet(concentration).sample()

    assert torch.allclose(
        _masked_dirichlet_log_prob(concentration, sample, valid),
        Dirichlet(concentration).log_prob(sample),
        atol=1e-5,
    )
    assert torch.allclose(
        _masked_dirichlet_entropy(concentration, valid),
        Dirichlet(concentration).entropy(),
        atol=1e-5,
    )
