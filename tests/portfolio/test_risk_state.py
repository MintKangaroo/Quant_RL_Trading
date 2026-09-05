"""리스크 상태값 (§5, 6b) — 순수 요약을 아는 답으로 검증한다."""

from __future__ import annotations

import numpy as np
import pandas as pd

from quant_rl_trading.portfolio.factor_model import FactorRiskModel
from quant_rl_trading.portfolio.risk_state import N_RISK_STATE, risk_state


def _model(names, variances, idio) -> FactorRiskModel:
    cov = pd.DataFrame(np.diag(variances), index=names, columns=names)
    return FactorRiskModel(
        covariance=cov,
        factor_covariance=pd.DataFrame([[1.0]], index=["MKT"], columns=["MKT"]),
        loadings=pd.DataFrame(index=names),
        idiosyncratic=pd.Series(dict(zip(names, idio))),
        shrinkage=0.0,
    )


def test_빈_비중은_위험이_없다() -> None:
    model = _model(["KR:A"], [0.04], [0.02])
    state = risk_state(
        pd.Series(dtype=float), model=model, sectors={}, downside_beta=pd.Series(dtype=float)
    )
    assert state.as_array().shape == (N_RISK_STATE,)
    assert np.allclose(state.as_array(), 0.0)


def test_현금은_리스크_기여에서_빠진다() -> None:
    """비중 합이 1 미만이어도 RC 는 투자분 안에서 정규화된다."""
    names = ["KR:A", "KR:B"]
    model = _model(names, [0.04, 0.04], [0.01, 0.01])
    sectors = {"KR:A": "s1", "KR:B": "s2"}
    beta = pd.Series({"KR:A": 0.5, "KR:B": 0.5})
    # 합 0.6 (현금 0.4). 두 종목 동일 → RC 각 0.5.
    weights = pd.Series({"KR:A": 0.3, "KR:B": 0.3})
    state = risk_state(weights, model=model, sectors=sectors, downside_beta=beta)
    assert abs(state.max_name_rc - 0.5) < 1e-6
    assert abs(state.max_sector_rc - 0.5) < 1e-6


def test_하방베타_가중평균() -> None:
    names = ["KR:A", "KR:B"]
    model = _model(names, [0.04, 0.04], [0.01, 0.01])
    beta = pd.Series({"KR:A": 0.4, "KR:B": 1.2})
    weights = pd.Series({"KR:A": 0.5, "KR:B": 0.5})
    state = risk_state(
        weights, model=model, sectors={"KR:A": "s1", "KR:B": "s2"}, downside_beta=beta
    )
    assert abs(state.portfolio_downside_beta - 0.8) < 1e-9


def test_한_섹터_집중이_top3에_잡힌다() -> None:
    names = ["KR:A", "KR:B", "KR:C"]
    model = _model(names, [0.09, 0.01, 0.01], [0.0, 0.0, 0.0])
    sectors = {"KR:A": "반도체", "KR:B": "반도체", "KR:C": "통신"}
    beta = pd.Series({n: 0.5 for n in names})
    weights = pd.Series({"KR:A": 0.5, "KR:B": 0.3, "KR:C": 0.2})
    state = risk_state(weights, model=model, sectors=sectors, downside_beta=beta)
    # 반도체(A,B)가 위험을 거의 다 진다 → max_sector_rc 높다.
    assert state.max_sector_rc > 0.7
    assert state.top3_sector_rc <= 1.0 + 1e-9


def test_고유분산이_전부면_idio_share_1() -> None:
    """공분산이 대각(=고유분산뿐)이면 idio_share 는 1 이다."""
    names = ["KR:A", "KR:B"]
    # 종목 총분산 = 고유분산 (팩터 기여 0).
    model = _model(names, [0.04, 0.04], [0.04, 0.04])
    weights = pd.Series({"KR:A": 0.5, "KR:B": 0.5})
    state = risk_state(
        weights, model=model, sectors={"KR:A": "s1", "KR:B": "s2"},
        downside_beta=pd.Series({"KR:A": 0.5, "KR:B": 0.5}),
    )
    assert abs(state.idio_share - 1.0) < 1e-9


def test_변동성은_연율화된다() -> None:
    names = ["KR:A"]
    model = _model(names, [0.0004], [0.0004])  # 일분산 0.0004 → 일변동성 2%
    weights = pd.Series({"KR:A": 1.0})
    state = risk_state(
        weights, model=model, sectors={"KR:A": "s1"},
        downside_beta=pd.Series({"KR:A": 0.5}),
    )
    # 연율변동성 = 0.02·√252 ≈ 0.317
    assert abs(state.portfolio_volatility - 0.02 * np.sqrt(252)) < 1e-6
