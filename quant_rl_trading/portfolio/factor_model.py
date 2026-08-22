"""팩터 모델 — 개별 종목 공분산을 **직접 추정하지 않는다** (§2).

30종목이면 상관계수가 435개다. 250일로 추정하면 거의 전부 잡음이고, 그대로
최적화기에 넣으면 추정 오차가 가장 큰 쌍에 극단 비중이 몰린다(mean-variance
의 고전적 실패). 차원을 팩터로 줄인다:

    r_i = β_i·MKT + γ_i·SECTOR(i) + ε_i
    Σ   = E Ω Eᵀ + diag(D)

    E : 종목×팩터 노출        Ω : 팩터 공분산(작다·안정적)     D : 고유분산

추정 대상이 435개에서 **팩터 공분산 25개 + 종목별 노출**로 준다. 무엇보다
"이 섹터가 빠지면 저 섹터가 어떻게 되나" 가 Ω 에 직접 들어온다.

## 팩터 = 시장 + 24섹터(시장에 직교화)

섹터 합성수익률(§1, 시총가중)을 그대로 팩터로 쓰면 시장과 겹친다 — 시총가중
섹터를 다 합치면 시장이다. 그래서 **각 섹터를 시장에 회귀해 잔차만** 팩터로
남긴다. 그러면 시장 팩터가 공통 등락을, 섹터 팩터가 그 위의 업종 고유
움직임을 담당한다. Ω 의 비대각이 직교화 뒤에도 남는 섹터 간 관계다.

## 스타일 팩터는 아직 없다

§2 는 시총·밸류·모멘텀·변동성도 팩터로 든다. 그것들은 **과거 노출 패널**
(매 세션의 종목별 표준화 특성)이 있어야 팩터 수익률을 낼 수 있는데, 지금
그 패널을 싸게 만들 경로가 없다(IC 캐시는 다른 규격이다). 시장+섹터만으로도
공통 등락과 업종 군집은 잡히므로 §3 리스크 패리티를 시작할 수 있다. 스타일은
후속으로 붙인다 — 그때 이 모듈의 팩터 축만 늘리면 된다.

## Ledoit-Wolf 축소

sklearn 이 없어 정준 LW(2004, scaled-identity 타깃)를 직접 둔다. 표본
공분산 S 를 평균분산×I 로 최적 강도만큼 끌어당긴다 — 팩터가 25개라 S 도
아주 안정적이진 않다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

from quant_rl_trading.portfolio.sector_beta import (
    DEFAULT_WINDOW,
    _panels,
    sector_composite_returns,
)
from quant_rl_trading.store import Store

#: 시장 팩터의 이름. 섹터 팩터는 섹터명 그대로 쓴다.
MARKET_FACTOR = "MKT"

#: 로딩 회귀에 필요한 최소 세션. 이보다 짧으면 그 종목은 공분산에서 뺀다 —
#: 회귀가 잡음이면 그 종목이 최적화기에서 극단 비중을 받는다.
MIN_REGRESSION_SESSIONS = 60


def ledoit_wolf(observations: np.ndarray) -> tuple[np.ndarray, float]:
    """정준 Ledoit-Wolf 축소 공분산과 축소 강도.

    ``observations`` 는 T×K (세션×팩터). scaled-identity 타깃 F = m·I 로
    S 를 끌어당긴다. m 은 평균분산, 강도는 추정오차(b²)와 타깃과의 거리(d²)
    의 비다 — S 가 잡음일수록(b² 큼) 더 끌어당기고, 타깃과 이미 가까우면
    (d² 작음) 적게 끌어당긴다.

    내적은 정규화 Frobenius <A,B> = trace(A Bᵀ)/K 를 쓴다(LW 원논문 규격).
    """
    x = np.asarray(observations, dtype=float)
    x = x[~np.isnan(x).any(axis=1)]
    t, k = x.shape
    if t < 2 or k == 0:
        raise ValueError("LW 축소에 표본이 모자란다")
    mean = x.mean(axis=0)
    x = x - mean
    sample = x.T @ x / t  # MLE 표본공분산(T 로 나눈다 — LW 규격)

    def inner(a: np.ndarray, b: np.ndarray) -> float:
        return float(np.trace(a @ b.T) / k)

    m = inner(sample, np.eye(k))
    target = m * np.eye(k)
    d2 = inner(sample - target, sample - target)
    # b²: 각 시점 x_t x_tᵀ 이 S 에서 흩어진 정도의 평균 / T.
    b2_sum = 0.0
    for row in x:
        outer = np.outer(row, row)
        b2_sum += inner(outer - sample, outer - sample)
    b2_bar = b2_sum / t**2
    b2 = min(b2_bar, d2)
    shrink = 0.0 if d2 <= 0 else b2 / d2
    shrunk = shrink * target + (1.0 - shrink) * sample
    return shrunk, float(shrink)


def orthogonalize_sectors(
    sector_returns: pd.DataFrame, market_returns: pd.Series
) -> pd.DataFrame:
    """섹터 팩터를 시장에 직교화한다. 시장 팩터를 첫 열로 붙여 돌려준다.

    각 섹터를 시장에 OLS 회귀해 **잔차**를 그 섹터 팩터로 쓴다. 시장에서
    설명되는 부분을 빼서, 남은 것이 순수한 업종 고유 움직임이 되게 한다.
    """
    aligned = sector_returns.join(market_returns.rename(MARKET_FACTOR), how="inner")
    aligned = aligned.dropna(subset=[MARKET_FACTOR])
    mkt = aligned[MARKET_FACTOR]
    factors = {MARKET_FACTOR: mkt}
    design = np.column_stack([np.ones(len(mkt)), mkt.to_numpy()])
    for sector in sector_returns.columns:
        y = aligned[sector]
        valid = y.notna().to_numpy()
        if valid.sum() < MIN_REGRESSION_SESSIONS:
            continue
        coef, *_ = np.linalg.lstsq(design[valid], y.to_numpy()[valid], rcond=None)
        resid = pd.Series(np.nan, index=aligned.index)
        resid[valid] = y.to_numpy()[valid] - design[valid] @ coef
        factors[sector] = resid
    return pd.DataFrame(factors)


@dataclass(frozen=True)
class FactorRiskModel:
    """팩터 모델이 낸 종목 공분산과 그 부품.

    ``covariance`` 가 §3 리스크 패리티의 입력이다. 나머지는 진단용 —
    설명력(고유분산 비율)을 보고 팩터가 모자란지 안다(§7).
    """

    covariance: pd.DataFrame          # 종목×종목 Σ
    factor_covariance: pd.DataFrame   # 팩터×팩터 Ω (LW 축소)
    loadings: pd.DataFrame            # 종목×팩터 E
    idiosyncratic: pd.Series          # 종목별 고유분산 D
    shrinkage: float                  # LW 축소 강도

    def idio_share(self) -> pd.Series:
        """종목 총분산 중 고유분산 비율. 높으면 팩터가 그 종목을 못 설명한다."""
        total = pd.Series(np.diag(self.covariance.to_numpy()), index=self.covariance.index)
        return (self.idiosyncratic / total).clip(0.0, 1.0)


def _fit_loadings(
    stock_returns: pd.DataFrame,
    factors: pd.DataFrame,
    sectors: dict[str, str],
    entities: list[str],
) -> tuple[pd.DataFrame, pd.Series]:
    """각 종목을 [시장, 자기섹터] 에 시계열 회귀해 노출 E 와 고유분산 D 로.

    노출은 **전체 팩터 공간**에 심는다: 종목 i 는 MKT 와 자기 섹터 열에만
    값이 있고 나머지 섹터 열은 0 이다. 섹터를 모르거나 그 섹터 팩터가 없으면
    시장에만 싣는다(γ=0). 회귀 표본이 모자란 종목은 뺀다.
    """
    factor_names = list(factors.columns)
    rows: dict[str, pd.Series] = {}
    idio: dict[str, float] = {}
    for entity in entities:
        if entity not in stock_returns.columns:
            continue
        sector = sectors.get(entity)
        cols = [MARKET_FACTOR]
        if sector and sector in factors.columns:
            cols.append(sector)
        panel = pd.concat(
            [stock_returns[entity].rename("y"), factors[cols]], axis=1
        ).dropna()
        if len(panel) < MIN_REGRESSION_SESSIONS:
            continue
        design = np.column_stack([np.ones(len(panel)), panel[cols].to_numpy()])
        coef, *_ = np.linalg.lstsq(design, panel["y"].to_numpy(), rcond=None)
        resid = panel["y"].to_numpy() - design @ coef
        exposure = pd.Series(0.0, index=factor_names)
        exposure[MARKET_FACTOR] = coef[1]
        if len(cols) == 2:
            exposure[sector] = coef[2]
        rows[entity] = exposure
        idio[entity] = float(np.var(resid, ddof=len(coef)))
    if not rows:
        return pd.DataFrame(columns=factor_names), pd.Series(dtype=float)
    loadings = pd.DataFrame(rows).T[factor_names]
    return loadings, pd.Series(idio)


def assemble_covariance(
    loadings: pd.DataFrame, factor_cov: pd.DataFrame, idio: pd.Series
) -> pd.DataFrame:
    """Σ = E Ω Eᵀ + diag(D). 팩터 축이 서로 맞는지 정렬해서 곱한다."""
    factors = list(factor_cov.columns)
    e = loadings[factors].to_numpy()
    omega = factor_cov.to_numpy()
    systematic = e @ omega @ e.T
    total = systematic + np.diag(idio.reindex(loadings.index).to_numpy())
    return pd.DataFrame(total, index=loadings.index, columns=loadings.index)


def estimate(
    store: Store,
    *,
    as_of: datetime,
    entities: list[str],
    market: str = "KR",
    window: int = DEFAULT_WINDOW,
) -> FactorRiskModel | None:
    """``entities`` 의 팩터 기반 공분산. 못 내면 None.

    섹터 합성·시장 수익률은 §1 과 같은 판을 재사용한다. 팩터는 그 섹터들을
    시장에 직교화한 것이고, 로딩은 각 종목의 시계열 회귀에서 나온다.
    """
    stock_ret, market_ret, caps, sectors = _panels(
        store, as_of=as_of, market=market, window=window
    )
    if stock_ret.empty or market_ret.empty or not sectors:
        return None
    stock_ret = stock_ret.tail(window)
    market_ret = market_ret.reindex(stock_ret.index)
    sector_ret = sector_composite_returns(stock_ret, caps=caps, sectors=sectors)
    factors = orthogonalize_sectors(sector_ret, market_ret)
    if factors.shape[1] < 1:
        return None

    loadings, idio = _fit_loadings(stock_ret, factors, sectors, entities)
    if loadings.empty:
        return None
    # 로딩이 실제로 실린 팩터만 Ω 에 넣는다 — 아무 종목도 안 실은 섹터 열은
    # 공분산에 넣어봐야 Σ 에 0 으로만 곱해진다.
    used = [f for f in factors.columns if (loadings[f].abs() > 0).any()]
    omega_matrix, shrink = ledoit_wolf(factors[used].to_numpy())
    factor_cov = pd.DataFrame(omega_matrix, index=used, columns=used)

    cov = assemble_covariance(loadings[used], factor_cov, idio)
    return FactorRiskModel(
        covariance=cov,
        factor_covariance=factor_cov,
        loadings=loadings[used],
        idiosyncratic=idio,
        shrinkage=shrink,
    )
