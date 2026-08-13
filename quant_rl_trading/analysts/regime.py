"""Regime Analyst — 시장 상태를 판정하고, 그 상태에서 유리한 종목을 고른다.

## 왜 '상태 한 줄'로 끝내지 않는가

agents.md 는 Regime 을 포트폴리오 축(강세/약세/횡보/고변동/위기)으로 적는다.
그런데 Signal 의 정의는 **같은 시장 종목 대비 초과수익의 z** 다
(signal.py). 시장 전체에 하나의 점수를 내면 그 값은 정의상 횡단면 z 가 될
수 없고, 모든 종목이 같은 점수를 받아 Selector 안에서 아무 정보도 아니게
된다.

그래서 여기서는 상태를 **판정만 하고 점수는 종목 축으로 낸다**: 지금이 위험
회피 국면이면 저베타·저변동·지수와 덜 붙는 종목에 높은 점수를 준다. 상태
자체는 ``evidence`` 와 :meth:`RegimeAnalyst.state` 로 꺼내 쓴다 — M4 의
Allocator 가 레짐 one-hot 을 상태값으로 받을 때 이 함수를 부른다.

## 상태 판정

HMM 이 아니라 룰이다 (agents.md 는 "HMM 또는 룰"). M2 에서 HMM 을 쓰면
상태 경계가 학습 표본에 맞춰 움직이는데, 그게 좋아진 것인지 과적합인지
구분할 방법이 아직 없다. 룰로 먼저 배관을 세우고 M4 이후에 바꾼다.

지수 20일 수익률과 60일 실현변동성 두 축이면 네 상태가 나온다::

    변동성 높음 + 수익 음수 → crisis      (위험 회피 최대)
    변동성 높음 + 수익 양수 → volatile    (반등이지만 불안정)
    변동성 보통 + 수익 양수 → bull
    변동성 보통 + 수익 음수 → bear

변동성 임계는 **자기 과거 분포의 분위수**다. 절대값(예: 25%)으로 두면
저변동 시대와 고변동 시대에 같은 뜻이 아니게 된다.

VKOSPI·금리·신용스프레드·시장 폭은 아직 창고에 없다. 없는 입력을 있는 척
근사하지 않는다 — 들어오면 그때 버전을 올린다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

import numpy as np
import pandas as pd

from quant_rl_trading.analysts.base import Analyst, combine, rank_score

INDICES = "indices"

#: 베타·변동성을 재는 창 + 상태 판정용 분포를 만들 창.
LOOKBACK_DAYS = 400

#: 종목 축 피처 창 (거래일).
BETA_WINDOW = 60

#: 시장 대용치. 코스피가 창고에 없어 KRX 광의 지수를 쓴다. 순서대로 찾는다.
MARKET_PROXIES = ("KR:IDX:KRX 300", "KR:IDX:KRX 100", "KR:IDX:KRX TMI")

#: 상태 판정용 분위수. 60일 변동성이 자기 과거 분포의 이 위면 '높음'이다.
HIGH_VOL_QUANTILE = 0.75

State = Literal["bull", "bear", "volatile", "crisis", "unknown"]

#: 상태별 종목 축 가중치. 부호가 통째로 뒤집히는 것이 이 Analyst 의 전부다.
#: 위험 회피 국면에서는 저베타·저변동·지수와 덜 붙는 종목을 산다.
REGIME_WEIGHTS: dict[str, dict[str, float]] = {
    "bull": {"beta": 0.45, "idio_volatility": 0.20, "index_correlation": 0.15, "downside_beta": -0.20},
    "bear": {"beta": -0.45, "idio_volatility": -0.20, "index_correlation": -0.15, "downside_beta": -0.20},
    "volatile": {"beta": 0.10, "idio_volatility": -0.35, "index_correlation": -0.20, "downside_beta": -0.35},
    "crisis": {"beta": -0.50, "idio_volatility": -0.25, "index_correlation": -0.10, "downside_beta": -0.15},
    # 지수를 못 찾았을 때. 방향을 찍지 않고 변동성만 낮춘다.
    "unknown": {"beta": 0.0, "idio_volatility": -0.5, "index_correlation": 0.0, "downside_beta": -0.5},
}


class RegimeAnalyst(Analyst):
    name = "regime"
    version = "regime-v0.1.0"

    def features(self, as_of: datetime) -> pd.DataFrame:
        prices = self.price_panel(as_of, lookback=LOOKBACK_DAYS)
        if prices.empty:
            return pd.DataFrame()

        close = self.wide(prices, "close")
        close = close.loc[:, close.notna().sum() >= BETA_WINDOW]
        if close.empty or len(close) < BETA_WINDOW:
            return pd.DataFrame()

        returns = close.pct_change().tail(BETA_WINDOW)
        index_returns = self._index_returns(as_of)

        raw = pd.DataFrame(index=close.columns)
        raw["idio_volatility"] = -returns.std()

        if index_returns is None:
            raw["beta"] = np.nan
            raw["index_correlation"] = np.nan
            raw["downside_beta"] = np.nan
        else:
            aligned = index_returns.reindex(returns.index)
            raw["beta"] = self._beta(returns, aligned)
            raw["index_correlation"] = returns.corrwith(aligned)
            # 하방 베타는 지수가 빠진 날만 본다. 같은 베타라도 내릴 때만 같이
            # 내리는 종목은 다른 물건이다. 부호를 뒤집어 '낮을수록 좋다' 로.
            down = aligned[aligned < 0.0].index
            raw["downside_beta"] = (
                self._beta(returns.loc[down], aligned.loc[down])
                if len(down) >= 10
                else np.nan
            )

        raw = raw.replace([np.inf, -np.inf], np.nan).dropna(how="all")
        if raw.empty:
            return pd.DataFrame()
        return raw.apply(rank_score).fillna(0.0)

    def raw_score(self, features: pd.DataFrame) -> pd.Series:
        # ``as_of`` 를 인자로 못 받는 자리라 상태를 캐시에서 읽는다.
        # features() 가 먼저 돌면서 채워 둔다.
        return combine(features, REGIME_WEIGHTS[self._state])

    def evidence_for(self, features: pd.DataFrame, entity_id: str) -> tuple:
        base = super().evidence_for(features, entity_id)
        from quant_rl_trading.schemas.signal import Evidence

        return (Evidence(key="regime", value=0.0, note=self._state), *base)

    # -- 상태 -------------------------------------------------------------------

    _state: State = "unknown"

    def state(self, as_of: datetime) -> State:
        """as_of 시점의 시장 상태. Allocator 가 one-hot 으로 받는다."""
        index = self._index_close(as_of)
        if index is None or len(index) < 120:
            return "unknown"

        returns = index.pct_change()
        realized = returns.rolling(60).std()
        current_vol = float(realized.iloc[-1])
        threshold = float(realized.dropna().quantile(HIGH_VOL_QUANTILE))
        momentum = float(index.iloc[-1] / index.iloc[-21] - 1.0)

        if not np.isfinite(current_vol) or not np.isfinite(momentum):
            return "unknown"
        if current_vol > threshold:
            return "crisis" if momentum < 0.0 else "volatile"
        return "bull" if momentum >= 0.0 else "bear"

    # -- 관측 -------------------------------------------------------------------

    def _index_close(self, as_of: datetime) -> pd.Series | None:
        frame = self.store.get(
            INDICES, as_of=as_of, lookback=LOOKBACK_DAYS, columns=["close", "revision"]
        )
        if frame.empty:
            return None
        for proxy in MARKET_PROXIES:
            subset = frame[frame["entity_id"] == proxy]
            if subset.empty:
                continue
            # 같은 세션에 정정본이 들어오면 마지막 것을 쓴다 (append-only).
            series = (
                subset.sort_values(["valid_from", "revision"])
                .assign(session=lambda frame: frame["valid_from"].dt.date)
                .groupby("session")["close"]
                .last()
            )
            return series.astype(float)
        return None

    def _index_returns(self, as_of: datetime) -> pd.Series | None:
        index = self._index_close(as_of)
        if index is None or len(index) < BETA_WINDOW + 1:
            return None
        return index.pct_change().tail(BETA_WINDOW)

    @staticmethod
    def _beta(returns: pd.DataFrame, index_returns: pd.Series) -> pd.Series:
        """종목별 지수 베타. 공분산/분산 — 회귀와 같은 값이고 훨씬 싸다."""
        variance = float(index_returns.var())
        if not np.isfinite(variance) or variance == 0.0:
            return pd.Series(np.nan, index=returns.columns)
        centered = index_returns - float(index_returns.mean())
        covariance = returns.sub(returns.mean()).mul(centered, axis=0).mean()
        return covariance / variance

    # -- 실행 ------------------------------------------------------------------

    def run(self, as_of: datetime, *, confidence: float = 0.0):  # type: ignore[override]
        # 상태를 먼저 정하고 그 가중치로 점수를 낸다.
        self._state = self.state(as_of)
        return super().run(as_of, confidence=confidence)
