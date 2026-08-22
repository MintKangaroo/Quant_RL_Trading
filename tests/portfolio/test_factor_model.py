"""팩터 모델 — 순수 부품을 창고 없이 검증한다.

LW 축소·직교화·로딩 회귀·공분산 조립을 각각 **아는 답**으로 확인한다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from quant_rl_trading.portfolio import factor_model as fm


def _sessions(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2024-01-01", periods=n, freq="B")


def test_LW_는_대각으로_끌어당긴다() -> None:
    """상관이 강한 표본이면 축소가 비대각을 줄이고 강도는 0~1 이다."""
    rng = np.random.default_rng(1)
    common = rng.normal(0, 1, size=400)
    # 세 팩터가 공통성분을 공유 — 표본상관이 높다.
    obs = np.column_stack([
        common + rng.normal(0, 0.3, 400),
        common + rng.normal(0, 0.3, 400),
        common + rng.normal(0, 0.3, 400),
    ])
    shrunk, intensity = fm.ledoit_wolf(obs)
    assert 0.0 <= intensity <= 1.0
    # 대칭·유한.
    assert np.allclose(shrunk, shrunk.T)
    assert np.isfinite(shrunk).all()
    sample = np.cov(obs, rowvar=False, bias=True)
    off_sample = np.abs(sample - np.diag(np.diag(sample))).sum()
    off_shrunk = np.abs(shrunk - np.diag(np.diag(shrunk))).sum()
    # 비대각(공분산)이 타깃(대각)쪽으로 줄었다.
    assert off_shrunk < off_sample


def test_LW_는_준정부호를_지킨다() -> None:
    """팩터보다 표본이 짧아 표본공분산이 특이해도 축소본은 PSD 다."""
    rng = np.random.default_rng(2)
    obs = rng.normal(size=(8, 6))  # T<K 는 아니지만 얇다
    shrunk, _ = fm.ledoit_wolf(obs)
    eig = np.linalg.eigvalsh(shrunk)
    assert (eig > -1e-9).all()


def test_직교화하면_섹터가_시장과_무상관이_된다() -> None:
    idx = _sessions(300)
    rng = np.random.default_rng(3)
    mkt = pd.Series(rng.normal(0, 0.01, 300), index=idx)
    # 섹터 = 0.8·시장 + 고유. 직교화하면 시장 성분이 빠진다.
    sector = 0.8 * mkt + pd.Series(rng.normal(0, 0.01, 300), index=idx)
    factors = fm.orthogonalize_sectors(pd.DataFrame({"제조업": sector}), mkt)
    corr = factors["MKT"].corr(factors["제조업"])
    assert abs(corr) < 1e-6


def test_로딩_회귀가_베타를_되찾는다() -> None:
    """종목 = 1.5·시장 + 0.5·섹터(직교) + 잡음 이면 로딩이 그 값 근처다."""
    idx = _sessions(300)
    rng = np.random.default_rng(4)
    mkt = pd.Series(rng.normal(0, 0.01, 300), index=idx)
    sector_orth = pd.Series(rng.normal(0, 0.01, 300), index=idx)
    factors = pd.DataFrame({"MKT": mkt, "제조업": sector_orth})
    stock = 1.5 * mkt + 0.5 * sector_orth + pd.Series(
        rng.normal(0, 0.001, 300), index=idx
    )
    returns = pd.DataFrame({"KR:A": stock})
    loadings, idio = fm._fit_loadings(
        returns, factors, {"KR:A": "제조업"}, ["KR:A"]
    )
    assert abs(loadings.loc["KR:A", "MKT"] - 1.5) < 0.05
    assert abs(loadings.loc["KR:A", "제조업"] - 0.5) < 0.05
    assert idio["KR:A"] > 0


def test_섹터를_모르면_시장에만_실린다() -> None:
    idx = _sessions(200)
    rng = np.random.default_rng(5)
    mkt = pd.Series(rng.normal(0, 0.01, 200), index=idx)
    factors = pd.DataFrame({"MKT": mkt, "제조업": pd.Series(rng.normal(0, 0.01, 200), index=idx)})
    stock = 1.0 * mkt + pd.Series(rng.normal(0, 0.001, 200), index=idx)
    loadings, _ = fm._fit_loadings(
        pd.DataFrame({"KR:X": stock}), factors, {}, ["KR:X"]
    )
    assert loadings.loc["KR:X", "제조업"] == 0.0
    assert abs(loadings.loc["KR:X", "MKT"] - 1.0) < 0.05


def test_공분산_조립은_EΩEᵀ_더하기_D() -> None:
    """작은 손계산으로 조립식을 확인한다."""
    loadings = pd.DataFrame(
        {"MKT": [1.0, 2.0], "제조업": [0.0, 1.0]}, index=["KR:A", "KR:B"]
    )
    omega = pd.DataFrame(
        [[0.04, 0.0], [0.0, 0.09]], index=["MKT", "제조업"], columns=["MKT", "제조업"]
    )
    idio = pd.Series({"KR:A": 0.01, "KR:B": 0.02})
    cov = fm.assemble_covariance(loadings, omega, idio)
    # A: 1²·0.04 + 0.01 = 0.05 ; B: 2²·0.04 + 1²·0.09 + 0.02 = 0.27
    assert abs(cov.loc["KR:A", "KR:A"] - 0.05) < 1e-9
    assert abs(cov.loc["KR:B", "KR:B"] - 0.27) < 1e-9
    # A,B 공분산 = 1·2·0.04 + 0·1·0.09 = 0.08
    assert abs(cov.loc["KR:A", "KR:B"] - 0.08) < 1e-9
    assert np.allclose(cov.to_numpy(), cov.to_numpy().T)
