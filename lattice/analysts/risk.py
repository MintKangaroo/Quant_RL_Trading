"""Risk Analyst — 통계만. 모델 없음.

"어느 종목이 오를까" 가 아니라 **"어느 종목이 포트폴리오를 위험하게 만드나"**
를 본다. 점수는 낮은 위험을 선호하는 방향으로 낸다.

세 축:

- **변동성** — 60일 EWMA 실현변동성. 최근에 가중을 준다
- **유동성** — 거래대금. 못 팔 종목은 아무리 좋아도 못 산다
- **군집도** — 시장과의 상관. 시장이 빠질 때 같이 빠지는 종목만 담으면
  분산이 아니라 레버리지다

상관행렬 전체는 M3 Selector 가 쓴다. 여기서는 종목당 하나의 스칼라만 낸다 —
Signal 스키마가 종목당 점수 하나이기 때문이다.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from lattice.analysts.base import Analyst, combine, rank_score

LOOKBACK_DAYS = 100

#: EWMA 반감기(거래일). 짧으면 잡음을 쫓고 길면 레짐 전환을 놓친다.
HALFLIFE = 20

WEIGHTS = {
    "low_volatility": 0.45,
    "liquidity": 0.35,
    "low_beta": 0.20,
}


class RiskAnalyst(Analyst):
    name = "risk"
    version = "risk-v0.1.0"

    def features(self, as_of: datetime) -> pd.DataFrame:
        prices = self.price_panel(as_of, lookback=LOOKBACK_DAYS)
        if prices.empty:
            return pd.DataFrame()

        close = self.wide(prices, "close")
        value = self.wide(prices, "value")
        close = close.loc[:, close.notna().sum() >= 60]
        if close.empty or len(close) < 60:
            return pd.DataFrame()

        returns = close.pct_change().tail(60)
        # 동일가중 시장수익률. 지수를 쓰면 더 정확하지만 indices 백필이
        # 완결되기 전까지는 관측에서 직접 만든다 — 없는 데이터를 가정하지 않는다.
        market = returns.mean(axis=1)

        raw = pd.DataFrame(index=close.columns)
        ewma = returns.ewm(halflife=HALFLIFE).std().iloc[-1]
        raw["low_volatility"] = -ewma

        traded = value.reindex(columns=close.columns).tail(20).mean()
        # 거래대금은 분포가 로그정규에 가깝다. 그대로 z 를 내면 대형주 몇 개가
        # 축을 독점한다.
        raw["liquidity"] = np.log1p(traded.clip(lower=0.0))

        variance = float(market.var())
        beta = (
            returns.apply(lambda column: column.cov(market)) / variance
            if variance > 0
            else pd.Series(0.0, index=close.columns)
        )
        raw["low_beta"] = -beta

        raw = raw.replace([np.inf, -np.inf], np.nan).dropna(how="all")
        return raw.apply(rank_score).fillna(0.0)

    def raw_score(self, features: pd.DataFrame) -> pd.Series:
        return combine(features, WEIGHTS)
