"""Recorded execution costs must not be deducted twice from net NAV returns."""

import pytest

from quant_rl_trading.allocator.reward import (
    REWARD_CONTRACT,
    RewardEngine,
    RewardParams,
    require_reward_contract,
)


def test_reward_matches_net_pnl():
    params = RewardParams(
        drawdown_free=0.12,
        drawdown_warn=0.22,
        drawdown_hard=0.30,
        w_free=0.0,
        w_mid=1.5,
        w_hot=8.0,
        terminal_penalty=-10.0,
        normalize_returns="none",
    )
    step = RewardEngine(params=params).step(
        portfolio_return=-0.001,
        benchmark_return=0.0,
        cost=0.001,
    )
    assert step.reward == pytest.approx(-0.001)
    assert step.cost == pytest.approx(0.001)


def test_old_reward_checkpoint_cannot_resume_as_new_contract():
    with pytest.raises(ValueError, match="reward contract"):
        require_reward_contract({})
    with pytest.raises(ValueError, match="reward contract"):
        require_reward_contract({"reward_contract": "cost-twice-v1"})
    require_reward_contract({"reward_contract": REWARD_CONTRACT})
