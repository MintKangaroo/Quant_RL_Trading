"""v2 U1 HMM — 합성 두 국면을 되찾고, 필터가 미래를 안 보는가."""

from __future__ import annotations

import numpy as np

from tools.v2_regime_hmm import filtered, fit


def _two_regimes(seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    states = np.repeat([0, 1, 0, 1], 150)
    mu = np.array([[-0.002, -0.03, 0.35], [0.001, 0.02, 0.12]])
    x = mu[states] + rng.normal(0, [0.005, 0.01, 0.02], size=(len(states), 3))
    return x, states


def test_두_국면을_되찾는다() -> None:
    x, states = _two_regimes()
    params = fit(x, 2)
    p, _ = filtered(x, params)
    assert (p.argmax(axis=1) == states).mean() > 0.9
    assert params["mu"][0, 0] < params["mu"][1, 0]      # 상태 0 이 나쁜 쪽


def test_필터는_미래를_안_본다() -> None:
    x, _ = _two_regimes()
    params = fit(x, 2)
    full, _ = filtered(x, params)
    head, _ = filtered(x[:300], params)
    assert np.allclose(full[:300], head)
