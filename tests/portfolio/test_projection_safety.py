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
        {"downside_beta": pd.Series({"A": float("inf"), "B": 0.5})},
        {"cov": pd.DataFrame([[1.0, np.nan], [np.nan, 1.0]], index=["A", "B"], columns=["A", "B"])},
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


def test_unknown_beta_or_sector_is_tolerated_not_rejected():
    """업종 미상 종목은 베타를 모른다 — 누르지 않을 뿐 세션을 막지 않는다.

    2026-09-11 모의 세션: 업종 미상 17/710 종목 때문에 후보 24 전체가 0 주문.
    설계(_downside_betas 독스트링)는 '모르면 NaN, 투영이 안 누른다' 다.
    """
    out = project(downside_beta=pd.Series({"A": float("nan"), "B": 0.5}))
    assert abs(out.sum() - 0.9) < 1e-9
    out = project(sectors={"A": "s1"})
    assert abs(out.sum() - 0.9) < 1e-9
    # 베타를 아는 종목이 하나도 없어도 공분산 제약만으로 투영한다.
    out = project(downside_beta=pd.Series({"A": float("nan"), "B": float("nan")}))
    assert abs(out.sum() - 0.9) < 1e-9
