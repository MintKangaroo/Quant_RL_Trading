"""risk_parity 투영의 비중 상한 (portfolio-construction.md 6a, 2026-10-04 정정).

RC 상한만으로는 저변동 종목(우선주·거래 얇은 종목)의 비중이 상한을 넘는다 — 9/28 KR:097955 16.8%. 집행의 위험 한도가 같은 상한을
매수마다 걸어서 넘친 몫은 막힐 뿐이다. 투영이 RC 상한과 비중 상한을 둘 다 만족해야 한다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant_rl_trading.portfolio import constraints as C
from quant_rl_trading.portfolio.risk_parity import risk_contributions

NAMES = [f"KR:{i:06d}" for i in range(10)]
SECTORS = {name: f"s{i}" for i, name in enumerate(NAMES)}


def _low_vol_case() -> tuple[pd.Series, pd.DataFrame]:
    """한 종목만 변동성이 낮다 — 역분산 기준선이 그 종목에 몰린다."""
    variances = [0.0016] + [0.01] * 9
    cov = pd.DataFrame(np.diag(variances), index=NAMES, columns=NAMES)
    inverse = pd.Series([1 / v for v in variances], index=NAMES)
    return inverse / inverse.sum(), cov


def _project(weights: pd.Series, cov: pd.DataFrame, cap: float) -> pd.Series:
    return C.project(
        weights, cov=cov, sectors=SECTORS, downside_beta=pd.Series(dtype=float),
        name_rc_cap=0.15, sector_rc_cap=0.35, downside_beta_cap=1.0, cash_floor=0.0, name_weight_cap=cap,
    )


def test_저변동_종목은_RC_상한만으론_비중_상한을_넘는다() -> None:
    """시험이 무는지 — 비중 상한을 끄면(1.0) 이 입력은 15% 를 넘는다."""
    weights, cov = _low_vol_case()
    assert float(_project(weights, cov, 1.0).max()) > 0.15


def test_비중_상한과_RC_상한을_둘_다_지킨다() -> None:
    weights, cov = _low_vol_case()
    out = _project(weights, cov, 0.15)
    assert float(out.max()) <= 0.15 + C.RC_TOLERANCE
    assert float(risk_contributions(out, cov).max()) <= 0.15 + C.RC_TOLERANCE
    assert out.sum() == pytest.approx(1.0)


def test_넘침이_없으면_결과가_그대로다() -> None:
    """비중 상한은 넘친 종목만 건드린다 — 넘침이 없는 입력에서 켜도 RC 투영 결과와 같다."""
    cov = pd.DataFrame(np.diag([0.01] * 10), index=NAMES, columns=NAMES)
    weights = pd.Series(np.linspace(1.0, 1.5, 10), index=NAMES)
    weights = weights / weights.sum()
    pd.testing.assert_series_equal(_project(weights, cov, 0.15), _project(weights, cov, 1.0))


def test_water_fill_은_넘친_몫을_여유_비례로_나누고_전부_상한이면_현금() -> None:
    out = C.cap_weights(pd.Series({"A": 0.5, "B": 0.3, "C": 0.2}), cap=0.4)
    assert out["A"] == pytest.approx(0.4)
    assert out.sum() == pytest.approx(1.0)
    assert out["B"] - 0.3 == pytest.approx((0.1) * 0.1 / 0.3)   # 여유 0.1·0.2 비례
    full = C.cap_weights(pd.Series({"A": 0.6, "B": 0.4}), cap=0.3)
    assert full.tolist() == pytest.approx([0.3, 0.3])           # 남는 0.4 는 현금
