"""카나리 정책망·수치 도구 계약.

## 왜 정책망을 따로 시험하는가

오라클 카나리가 떨어졌을 때 원인을 좁히기 위해서다. 여기가 통과하고 카나리가
떨어지면 문제는 **정책망이 아니라 학습 루프**(어드밴티지·정규화·부트스트랩)에
있다. `rl-training.md §5` 의 배제 순서를 실제로 쓰려면 이 구분이 먼저 있어야
한다.

수치 미분 대조가 여기 있는 이유도 같다. 손으로 쓴 역전파가 조용히 틀리면
증상은 "학습이 안 된다" 하나뿐이고, 그건 §5 의 여섯 원인 어디에도 없다.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from quant_rl_trading.modelops import canary_policy as dirichlet
from quant_rl_trading.modelops.canary_policy import CanaryPolicy
from quant_rl_trading.modelops.numeric import digamma, lgamma, trigamma


def _observation(rng: np.random.Generator, *, batch=4, n_assets=6, features=9, portfolio=5):
    mask = rng.random((batch, n_assets)) < 0.7
    mask[:, 0] = True  # 유효 후보가 하나도 없는 배치는 만들지 않는다
    assets = rng.standard_normal((batch, n_assets, features)) * mask[:, :, None]
    return {
        "assets": assets,
        "portfolio": rng.standard_normal((batch, portfolio)),
        "mask": mask,
    }


def _policy(features=9, portfolio=5, hidden=8, seed=1) -> CanaryPolicy:
    return CanaryPolicy(
        n_asset_features=features, n_portfolio_features=portfolio, hidden=hidden, seed=seed
    )


def test_종목_순서를_섞어도_같은_답이_나온다() -> None:
    """**위치 인코딩이 없다**(§2). 넣는 순간 "3번 슬롯" 이라는 존재하지 않는
    개념을 학습한다. 허용 오차 1e-5."""
    rng = np.random.default_rng(0)
    obs = _observation(rng)
    policy = _policy()
    order = rng.permutation(obs["assets"].shape[1])

    base = policy.forward(obs)
    shuffled = policy.forward(
        {
            "assets": obs["assets"][:, order],
            "portfolio": obs["portfolio"],
            "mask": obs["mask"][:, order],
        }
    )

    assert np.allclose(
        base.concentration[:, :-1][:, order], shuffled.concentration[:, :-1], atol=1e-5
    )
    assert np.allclose(base.concentration[:, -1], shuffled.concentration[:, -1], atol=1e-5)
    assert np.allclose(base.value, shuffled.value, atol=1e-5)


def test_패딩_슬롯에는_비중이_가지_않는다() -> None:
    """0 에 수렴하는 게 아니라 **정확히 0** 이다. 아주 작은 concentration 을
    주는 방식은 log_prob 의 (a-1)·log x 항을 발산시킨다."""
    rng = np.random.default_rng(1)
    obs = _observation(rng, batch=32)
    policy = _policy()
    out = policy.forward(obs)
    valid = np.concatenate([obs["mask"], np.ones((32, 1), dtype=bool)], axis=1)

    weights = dirichlet.sample(rng, out.concentration, valid)

    assert np.all(weights[:, :-1][~obs["mask"]] == 0.0)


def test_표본은_심플렉스_위에_있다() -> None:
    rng = np.random.default_rng(2)
    obs = _observation(rng, batch=64)
    policy = _policy()
    out = policy.forward(obs)
    valid = np.concatenate([obs["mask"], np.ones((64, 1), dtype=bool)], axis=1)

    weights = dirichlet.sample(rng, out.concentration, valid)

    assert np.all(weights >= 0.0)
    assert np.allclose(weights.sum(axis=1), 1.0)


def test_극단적인_concentration_에서도_log_prob_이_유한하다() -> None:
    """상한 1e3, 하한 1e-3 양쪽 끝. NaN 이 나오면 그 배치의 어드밴티지가
    통째로 죽고, 증상은 한참 뒤에 EV 로만 나타난다."""
    rng = np.random.default_rng(3)
    valid = np.ones((3, 5), dtype=bool)
    valid[2, 4] = False
    extreme = np.array(
        [
            [1e-3, 1e-3, 1e-3, 1e-3, 1e-3],
            [1e3, 1e3, 1e3, 1e3, 1e3],
            [1e-3, 1e3, 1.0, 500.0, 0.0],
        ]
    )

    weights = dirichlet.sample(rng, extreme, valid)
    values = dirichlet.log_prob(extreme, weights, valid)

    assert np.all(np.isfinite(values))
    assert np.all(np.isfinite(dirichlet.log_prob_grad(extreme, weights, valid)))
    assert np.all(np.isfinite(dirichlet.entropy(extreme, valid)))
    assert np.all(np.isfinite(dirichlet.entropy_grad(extreme, valid)))


def test_역전파가_수치미분과_일치한다() -> None:
    """손으로 쓴 미분이 틀리면 증상은 "학습이 안 된다" 하나뿐이다."""
    rng = np.random.default_rng(4)
    obs = _observation(rng)
    policy = _policy()
    valid = np.concatenate(
        [obs["mask"], np.ones((obs["mask"].shape[0], 1), dtype=bool)], axis=1
    )
    weights = dirichlet.sample(rng, policy.forward(obs).concentration, valid)

    def loss(pol: CanaryPolicy) -> float:
        out = pol.forward(obs)
        return float(
            dirichlet.log_prob(out.concentration, weights, valid).sum()
            + 0.3 * out.value.sum()
            + 0.7 * dirichlet.entropy(out.concentration, valid).sum()
        )

    out = policy.forward(obs)
    grads, input_grad = policy.backward(
        out,
        d_concentration=dirichlet.log_prob_grad(out.concentration, weights, valid)
        + 0.7 * dirichlet.entropy_grad(out.concentration, valid),
        d_value=np.full(obs["portfolio"].shape[0], 0.3),
        want_input_grad=True,
    )
    assert input_grad is not None

    eps = 1e-6
    for name, value in policy.params.items():
        for _ in range(3):
            index = np.unravel_index(rng.integers(value.size), value.shape)
            original = value[index]
            value[index] = original + eps
            up = loss(policy)
            value[index] = original - eps
            down = loss(policy)
            value[index] = original
            numeric = (up - down) / (2 * eps)
            assert numeric == pytest.approx(grads[name][index], rel=1e-3, abs=1e-6), name

    for _ in range(6):
        index = tuple(int(rng.integers(size)) for size in obs["assets"].shape)
        original = obs["assets"][index]
        obs["assets"][index] = original + eps
        up = loss(policy)
        obs["assets"][index] = original - eps
        down = loss(policy)
        obs["assets"][index] = original
        numeric = (up - down) / (2 * eps)
        assert numeric == pytest.approx(input_grad[index], rel=1e-3, abs=1e-6)


@pytest.mark.parametrize("x", [1e-3, 0.5, 1.0, 2.0, 10.0, 300.0])
def test_특수함수가_정확하다(x: float) -> None:
    """작은 인자에서 틀린 digamma 는 NaN 이 아니라 **조용히 편향된
    그래디언트**로 나타난다. concentration 하한이 1e-3 이라 실제로 들어온다."""
    array = np.array([x])
    step = x * 1e-5

    assert lgamma(array)[0] == pytest.approx(math.lgamma(x), rel=1e-10)
    numeric_digamma = (math.lgamma(x + step) - math.lgamma(x - step)) / (2 * step)
    assert digamma(array)[0] == pytest.approx(numeric_digamma, rel=1e-4)
    numeric_trigamma = (
        math.lgamma(x + step) - 2 * math.lgamma(x) + math.lgamma(x - step)
    ) / (step * step)
    assert trigamma(array)[0] == pytest.approx(numeric_trigamma, rel=1e-3)
