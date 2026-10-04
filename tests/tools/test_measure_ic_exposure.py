"""주간 IC 측정이 regime 을 '미통과' 가 아니라 '대상 아님(노출 지표)' 으로 적는다 (modelops-ranker.md ①)."""
from types import SimpleNamespace

from quant_rl_trading.analysts import ic
from tools import measure_ic


def _result(name: str, passed: bool) -> SimpleNamespace:
    return SimpleNamespace(analyst=name, analyst_version="v", market="KR", ic=0.05 if passed else 0.01, threshold=0.03,
                           ic_std=0.1, sample_days=200, sample_rows=1000, min_sample_days=60, weight=1.0 if passed else 0.0,
                           fold_ics=[], passed=passed)


def test_regime_is_exposure_signal():
    assert "regime" in ic.EXPOSURE_ANALYSTS


def test_render_marks_regime_not_failed():
    text = measure_ic.render(_result("regime", passed=True))   # IC 가 우연히 넘어도
    assert "대상 아님 — 노출 지표" in text and "미통과" not in text and "[통과]" not in text
    assert "가중치      0.0" in text
    assert "[미통과] chart" in measure_ic.render(_result("chart", passed=False))
