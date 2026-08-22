"""리스크 패리티 룰 베이스라인 배선 — 창고 없이 orchestration 을 검증한다.

팩터 모델·섹터·베타는 monkeypatch 로 갈아 끼운다. 여기서 보는 것은 "세
조각을 옳게 이어 붙였나" 와 "데이터가 없을 때 스코어 비례로 물러서나" 다.
공분산·투영의 수학은 tests/portfolio 가 이미 본다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant_rl_trading.allocator import baseline as base_mod
from quant_rl_trading.allocator import risk_parity_baseline as rpb
from quant_rl_trading.allocator.baseline import AllocatorParams, Baseline, allocate


def _fallback() -> AllocatorParams:
    # 상한을 넉넉히 둔다 — 종목이 두엇뿐인 테스트에서 0.15 면 둘 다 상한에
    # 눌려 스코어 비례가 사라진다(그건 상한의 정상 동작이지 폴백 검증이 아니다).
    return AllocatorParams(
        baseline=Baseline.SCORE, max_position_weight=0.9, cash_buffer=0.05
    )


def _rp_params() -> rpb.RiskParityParams:
    return rpb.RiskParityParams(
        score_tilt=1.0, name_rc_cap=0.15, sector_rc_cap=0.35,
        downside_beta_cap=1.0, window=250,
    )


def test_순수_allocate_는_risk_parity_를_거부한다() -> None:
    params = AllocatorParams(
        baseline=Baseline.RISK_PARITY, max_position_weight=0.15, cash_buffer=0.05
    )
    with pytest.raises(ValueError, match="allocate_risk_parity"):
        allocate(scores={"KR:A": 1.0}, params=params)


def test_팩터모델이_None이면_스코어로_물러선다(monkeypatch) -> None:
    monkeypatch.setattr(rpb.factor_model, "estimate", lambda *a, **k: None)
    scores = {"KR:A": 2.0, "KR:B": 1.0}
    weights, path = rpb.allocate_risk_parity(
        store=None, as_of=None, market="KR", scores=scores,
        entities=["KR:A", "KR:B"], params=_rp_params(), fallback=_fallback(),
    )
    assert path == "fallback"
    # 스코어 비례: 합 = 1 − cash_buffer, A 가 B 보다 크다.
    assert weights["KR:A"] > weights["KR:B"]
    assert abs(sum(weights.values()) - 0.95) < 1e-9


def test_공분산이_비면_스코어로_물러선다(monkeypatch) -> None:
    class _Model:
        covariance = pd.DataFrame()

    monkeypatch.setattr(rpb.factor_model, "estimate", lambda *a, **k: _Model())
    weights, path = rpb.allocate_risk_parity(
        store=None, as_of=None, market="KR", scores={"KR:A": 1.0},
        entities=["KR:A"], params=_rp_params(), fallback=_fallback(),
    )
    assert path == "fallback"


def test_모델이_있으면_리스크패리티_경로로_간다(monkeypatch) -> None:
    names = ["KR:A", "KR:B", "KR:C"]
    cov = pd.DataFrame(np.diag([0.04, 0.02, 0.03]), index=names, columns=names)

    class _Model:
        covariance = cov

    monkeypatch.setattr(rpb.factor_model, "estimate", lambda *a, **k: _Model())
    monkeypatch.setattr(
        rpb, "sector_map",
        lambda *a, **k: {"KR:A": "c1", "KR:B": "c2", "KR:C": "c3"},
    )
    monkeypatch.setattr(rpb.ksic, "roll_up_map", lambda m: m)

    class _Beta:
        down_beta = 0.6

    monkeypatch.setattr(
        rpb, "estimate_betas", lambda *a, **k: {"c1": _Beta(), "c2": _Beta(), "c3": _Beta()}
    )
    weights, path = rpb.allocate_risk_parity(
        store=None, as_of=None, market="KR",
        scores={"KR:A": 1.0, "KR:B": 1.0, "KR:C": 1.0},
        entities=names, params=_rp_params(), fallback=_fallback(),
    )
    assert path == "risk_parity"
    assert set(weights) == set(names)
    # cash_floor=0 이라 합은 1 근처(투영이 현금을 안 뺀다 — exposure 가 뺀다).
    assert abs(sum(weights.values()) - 1.0) < 1e-6


def test_음수_스코어는_리스크패리티_경로에서도_빠진다(monkeypatch) -> None:
    names = ["KR:A", "KR:B"]
    cov = pd.DataFrame(np.diag([0.04, 0.02]), index=names, columns=names)

    class _Model:
        covariance = cov

    monkeypatch.setattr(rpb.factor_model, "estimate", lambda *a, **k: _Model())
    monkeypatch.setattr(rpb, "sector_map", lambda *a, **k: {"KR:A": "c1", "KR:B": "c2"})
    monkeypatch.setattr(rpb.ksic, "roll_up_map", lambda m: m)

    class _Beta:
        down_beta = 0.5

    monkeypatch.setattr(
        rpb, "estimate_betas", lambda *a, **k: {"c1": _Beta(), "c2": _Beta()}
    )
    weights, path = rpb.allocate_risk_parity(
        store=None, as_of=None, market="KR",
        scores={"KR:A": 1.0, "KR:B": -0.5},
        entities=names, params=_rp_params(), fallback=_fallback(),
    )
    assert path == "risk_parity"
    assert "KR:B" not in weights
