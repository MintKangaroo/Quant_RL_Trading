"""리스크 상태값 — §5 가 RL 에 먹일 위험 구조 (6b 스캐폴딩).

§5 의 핵심: **최적화기를 RL 뒤가 아니라 앞에 둔다.** Risk Analyst 가 팩터
노출·하방 베타·섹터 리스크 기여를 **상태값으로** 내보내고, RL 이 그것을 보며
비중을 낸다. Executor 는 위반 시에만 최소 투영을 한다. 그래야 액션 반영률이
떨어지지 않는다 — 최적화기가 RL 출력을 덮어쓰면 선행 프로젝트처럼 룰로
전락한다.

## 이 모듈은 아직 RL env 에 배선하지 않는다

env.py 의 관측 공간을 바꾸는 것은 **카나리 판정을 흐린다** — 지금 카나리는
"비중 머리가 애초에 배우나" 를 묻고 있고(2026-08 아직 미해결), 그 위에 상태
차원을 얹으면 안 배우는 원인이 하나 더 늘 뿐이다. 그래서 여기서는 상태값을
**내는 순수 함수만** 두고, env 로의 배선은 카나리가 갈린 뒤에 한다.

## 무엇을 상태로 내나

RL 이 "지금 이 포트폴리오가 위험 구조상 어디에 서 있나" 를 알아야 스스로
제약을 만족하는 비중을 낸다. 그래서 **현재 비중의** 위험 구조를 요약한다:

    portfolio_downside_beta   하방 베타 가중평균 (1.0 넘으면 위험)
    max_name_rc               가장 큰 단일 종목 리스크 기여
    max_sector_rc             가장 큰 섹터 리스크 기여
    top3_sector_rc            상위 3섹터 리스크 기여 합 (집중도)
    portfolio_volatility      연율화 포트폴리오 변동성
    idio_share                고유위험 비율 (분산이 팩터로 안 잡히는 몫)

전부 스케일 프리(비율·베타)라 자본 크기와 무관하다 — 백테스트와 라이브가
같은 상태를 본다(불변식 5).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
import pandas as pd

from quant_rl_trading.portfolio.factor_model import FactorRiskModel
from quant_rl_trading.portfolio.risk_parity import (
    risk_contributions,
    sector_risk_contributions,
)

#: 연율화 계수(거래일). 변동성을 연율로 내 사람이 읽기 쉽게 한다.
TRADING_DAYS = 252

#: 상태 벡터의 길이. env 관측 공간이 이 수를 고정으로 잡는다(배선 시).
N_RISK_STATE = 6


@dataclass(frozen=True)
class RiskState:
    """현재 비중의 위험 구조 요약 — §5 의 RL 상태값."""

    portfolio_downside_beta: float
    max_name_rc: float
    max_sector_rc: float
    top3_sector_rc: float
    portfolio_volatility: float
    idio_share: float

    def as_array(self) -> np.ndarray:
        """고정 길이 벡터. env 관측이 이 순서를 그대로 쓴다(배선 시)."""
        return np.array([
            self.portfolio_downside_beta,
            self.max_name_rc,
            self.max_sector_rc,
            self.top3_sector_rc,
            self.portfolio_volatility,
            self.idio_share,
        ], dtype=np.float32)


def _weighted_downside_beta(
    weights: pd.Series, downside_beta: pd.Series
) -> float:
    """하방 베타 가중평균. 베타를 아는 종목만으로 낸다(NaN 은 뺀다)."""
    beta = downside_beta.reindex(weights.index)
    valid = beta.notna()
    if not valid.any():
        return float("nan")
    w = weights[valid]
    total = w.sum()
    if total <= 0:
        return float("nan")
    return float((w * beta[valid]).sum() / total)


def risk_state(
    weights: pd.Series,
    *,
    model: FactorRiskModel,
    sectors: Mapping[str, str],
    downside_beta: pd.Series,
) -> RiskState:
    """현재 비중의 위험 구조를 요약한다.

    ``weights`` 는 투자 비중(합 ≤ 1, 나머지는 현금). 리스크 기여는 투자분
    안에서의 분수라 현금은 자동으로 빠진다 — 현금이 위험을 지지 않기 때문이다.
    빈 비중이면 모든 값이 0 인 상태다(위험 없음).
    """
    invested = weights[weights > 0]
    if invested.empty:
        return RiskState(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    cov = model.covariance.reindex(index=invested.index, columns=invested.index)
    # 리스크 기여는 투자분 안에서의 분수다 — 현금을 뺀 비중으로 정규화한다.
    normed = invested / invested.sum()
    rc = risk_contributions(normed, cov)
    sec_rc = sector_risk_contributions(rc, dict(sectors))
    top3 = float(sec_rc.sort_values(ascending=False).head(3).sum())

    port_var = float(normed.to_numpy() @ cov.to_numpy() @ normed.to_numpy())
    vol = float(np.sqrt(max(port_var, 0.0) * TRADING_DAYS))

    idio = model.idiosyncratic.reindex(invested.index).fillna(0.0)
    # 고유위험 비율: 종목 분산 중 팩터로 안 잡히는 몫을 비중²로 가중.
    total_var = pd.Series(np.diag(cov.to_numpy()), index=cov.index)
    share = (idio / total_var.replace(0.0, np.nan)).clip(0.0, 1.0).fillna(0.0)
    idio_share = float((normed**2 * share).sum() / (normed**2).sum())

    return RiskState(
        portfolio_downside_beta=_weighted_downside_beta(invested, downside_beta),
        max_name_rc=float(rc.max()),
        max_sector_rc=float(sec_rc.max()),
        top3_sector_rc=top3,
        portfolio_volatility=vol,
        idio_share=idio_share,
    )
