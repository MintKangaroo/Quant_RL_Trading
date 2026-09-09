"""Infeasible or unknown risk cannot be reported as a valid portfolio."""

import numpy as np
import pandas as pd
import pytest

from quant_rl_trading.portfolio import constraints


def project(**overrides):
    names = ["A", "B"]
    args = dict(
        weights=pd.Series([0.5, 0.5], index=names),
        cov=pd.DataFrame(np.eye(2), index=names, columns=names),
        sectors={"A": "s1", "B": "s2"},
        downside_beta=pd.Series([0.5, 0.5], index=names),
        name_rc_cap=0.6,
        sector_rc_cap=0.6,
        downside_beta_cap=1.0,
        cash_floor=0.1,
    )
    args.update(overrides)
    return constraints.project(**args)


@pytest.mark.parametrize(
    "overrides",
    [
        {"downside_beta": pd.Series({"A": 1.5, "B": 1.6})},
        {"name_rc_cap": 0.15},
        {"sectors": {"A": "same", "B": "same"}},
        {"downside_beta": pd.Series({"A": float("nan"), "B": 0.5})},
        {"cash_floor": -0.2},
        {"weights": pd.Series({"A": -0.1, "B": 1.1})},
        {"cov": pd.DataFrame([[1.0, 2.0], [2.0, 1.0]], index=["A", "B"], columns=["A", "B"])},
    ],
)
def test_invalid_or_infeasible_projection_rejected(overrides):
    with pytest.raises(ValueError):
        project(**overrides)


def test_rc_projection_cannot_reintroduce_beta_violation():
    # beta requires A >= 0.8, while equal variances + RC cap require near equal weights.
    with pytest.raises(ValueError):
        project(downside_beta=pd.Series({"A": 0.0, "B": 2.0}), downside_beta_cap=0.4)
