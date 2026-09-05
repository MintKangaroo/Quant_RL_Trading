"""리스크 기여 균등 + 스코어 틸트 — 순수 엔진을 아는 답으로 검증한다."""

from __future__ import annotations

import numpy as np
import pandas as pd

from quant_rl_trading.portfolio import risk_parity as rp


def _cov(values) -> pd.DataFrame:
    arr = np.asarray(values, dtype=float)
    names = [f"KR:{i}" for i in range(len(arr))]
    return pd.DataFrame(arr, index=names, columns=names)


def test_대각_공분산이면_리스크패리티는_변동성_역가중() -> None:
    """상관 0 이면 ERC 는 1/σ 가중이다. 분산 4:1 이면 비중 1:2 근처."""
    cov = _cov([[0.04, 0.0], [0.0, 0.01]])
    budgets = {"KR:0": 0.5, "KR:1": 0.5}
    w = rp.risk_budget_weights(cov, budgets)
    # σ0=0.2, σ1=0.1 → w ∝ 1/σ → 1/0.2 : 1/0.1 = 1:2
    assert abs(w["KR:0"] / w["KR:1"] - 0.5) < 1e-3


def test_리스크_기여가_예산에_비례한다() -> None:
    """풀린 비중의 RC 가 준 예산 비율과 같아야 한다."""
    rng = np.random.default_rng(0)
    a = rng.normal(size=(300, 4))
    cov = pd.DataFrame(np.cov(a, rowvar=False), index=[f"KR:{i}" for i in range(4)],
                       columns=[f"KR:{i}" for i in range(4)])
    budgets = {"KR:0": 0.4, "KR:1": 0.3, "KR:2": 0.2, "KR:3": 0.1}
    w = rp.risk_budget_weights(cov, budgets)
    rc = rp.risk_contributions(w, cov)
    target = pd.Series(budgets)
    assert np.allclose(rc.reindex(target.index).to_numpy(), target.to_numpy(), atol=1e-3)


def test_섹터_예산은_섹터_RC를_균등하게_한다() -> None:
    """반도체 3종 + 통신 1종. 섹터 RC 가 각 0.5 여야 한다 — 종목 수와 무관."""
    rng = np.random.default_rng(1)
    a = rng.normal(size=(500, 4))
    names = ["KR:S1", "KR:S2", "KR:S3", "KR:T1"]
    cov = pd.DataFrame(np.cov(a, rowvar=False), index=names, columns=names)
    sectors = {"KR:S1": "반도체", "KR:S2": "반도체", "KR:S3": "반도체", "KR:T1": "통신"}
    budgets = rp.sector_budgets(names, sectors)
    w = rp.risk_budget_weights(cov, budgets)
    rc = rp.risk_contributions(w, cov)
    sector_rc = rp.sector_risk_contributions(rc, sectors)
    assert abs(sector_rc["반도체"] - 0.5) < 1e-2
    assert abs(sector_rc["통신"] - 0.5) < 1e-2


def test_스코어_틸트_0이면_기준선_그대로() -> None:
    base = pd.Series({"KR:0": 0.6, "KR:1": 0.4})
    tilted = rp.score_tilt(base, {"KR:0": 5.0, "KR:1": 1.0}, tilt=0.0)
    assert np.allclose(tilted.to_numpy(), base.to_numpy())


def test_스코어_틸트가_높은_스코어로_비중을_민다() -> None:
    base = pd.Series({"KR:0": 0.5, "KR:1": 0.5})
    tilted = rp.score_tilt(base, {"KR:0": 2.0, "KR:1": 1.0}, tilt=1.0)
    # 같은 기준선이면 스코어 높은 쪽이 더 커진다. 비율 = exp(2)/exp(1)=e.
    assert tilted["KR:0"] > tilted["KR:1"]
    assert abs(tilted["KR:0"] / tilted["KR:1"] - np.e) < 1e-6
    assert abs(tilted.sum() - 1.0) < 1e-12


def test_음수_스코어는_후보에서_빠진다() -> None:
    cov = _cov([[0.04, 0.0, 0.0], [0.0, 0.02, 0.0], [0.0, 0.0, 0.03]])
    cov.index = cov.columns = ["KR:A", "KR:B", "KR:C"]
    scores = {"KR:A": 1.0, "KR:B": -0.5, "KR:C": 2.0}
    sectors = {"KR:A": "s1", "KR:B": "s1", "KR:C": "s2"}
    w = rp.construct_baseline(scores=scores, cov=cov, sectors=sectors, tilt=0.5)
    assert "KR:B" not in w.index
    assert set(w.index) == {"KR:A", "KR:C"}
    assert abs(w.sum() - 1.0) < 1e-9


def test_공분산에_없는_종목은_빠진다() -> None:
    cov = _cov([[0.04, 0.0], [0.0, 0.02]])
    cov.index = cov.columns = ["KR:A", "KR:B"]
    scores = {"KR:A": 1.0, "KR:B": 1.0, "KR:MISSING": 1.0}
    sectors = {"KR:A": "s1", "KR:B": "s2", "KR:MISSING": "s3"}
    w = rp.construct_baseline(scores=scores, cov=cov, sectors=sectors, tilt=0.0)
    assert "KR:MISSING" not in w.index
