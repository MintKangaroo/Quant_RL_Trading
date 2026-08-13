"""Chart Analyst — 가격·거래량만 본다.

agents.md §2 의 5개 축을 쓰되 **피처를 적게 유지한다.** 기술지표는 상호 상관이
0.9 를 넘는 것이 흔해서, 30개를 넣으면 30개 값어치가 아니라 5개 값어치가
나오고 과적합만 심해진다.

M2 는 LightGBM 없이 **가중 합**으로 간다. 이유는 하나다 — 학습을 얹기 전에
피처와 라벨 배관이 정직한지 먼저 봐야 하기 때문이다. 룰로 IC 0.03 이 안 나오는
피처는 LightGBM 을 얹어도 대개 안 나오고, 나온다면 그건 누수를 의심할 일이다.
모델 교체는 이 파일의 ``raw_score`` 하나만 바꾸면 된다.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from lattice.analysts.base import Analyst, combine, rank_score

#: 피처 계산에 필요한 최소 관측 창.
LOOKBACK_DAYS = 130

#: 축별 가중치. 부호가 뜻을 담는다 — 단기 반전은 음수다.
WEIGHTS = {
    "momentum_20": 0.30,
    "momentum_60": 0.20,
    "reversal_5": -0.20,
    "ma_gap": 0.15,
    "volume_surge": 0.10,
    "low_volatility": 0.15,
    "range_position": 0.10,
}


class ChartAnalyst(Analyst):
    name = "chart"
    version = "chart-v0.1.0"

    def features(self, as_of: datetime) -> pd.DataFrame:
        prices = self.price_panel(as_of, lookback=LOOKBACK_DAYS)
        if prices.empty:
            return pd.DataFrame()

        close = self.wide(prices, "close")
        volume = self.wide(prices, "volume")
        # 관측이 얕은 종목은 지표가 잡음이다. 신규 상장주를 여기서 거른다.
        close = close.loc[:, close.notna().sum() >= 60]
        if close.empty or len(close) < 60:
            return pd.DataFrame()

        volume = volume.reindex(columns=close.columns)
        returns = close.pct_change()

        raw = pd.DataFrame(index=close.columns)
        raw["momentum_20"] = close.iloc[-1] / close.shift(20).iloc[-1] - 1.0
        raw["momentum_60"] = close.iloc[-1] / close.shift(60).iloc[-1] - 1.0
        # 단기 반전. 5일 급등은 되돌리는 경향이 있어 부호를 뒤집어 쓴다.
        raw["reversal_5"] = close.iloc[-1] / close.shift(5).iloc[-1] - 1.0
        raw["ma_gap"] = close.iloc[-1] / close.tail(20).mean() - 1.0
        raw["volume_surge"] = volume.tail(5).mean() / volume.tail(60).mean() - 1.0
        # 저변동성 프리미엄. 부호를 뒤집어 "낮을수록 좋다" 로 만든다.
        raw["low_volatility"] = -returns.tail(60).std()
        window = close.tail(120)
        span = window.max() - window.min()
        raw["range_position"] = (close.iloc[-1] - window.min()) / span.replace(0.0, np.nan)

        raw = raw.replace([np.inf, -np.inf], np.nan).dropna(how="all")
        # 결측은 그 종목의 중앙값 자리(z=0)로 둔다. 앞뒤로 채우면 미래를 본다.
        return raw.apply(rank_score).fillna(0.0)

    def raw_score(self, features: pd.DataFrame) -> pd.Series:
        return combine(features, WEIGHTS)
