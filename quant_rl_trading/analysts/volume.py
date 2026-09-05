"""Volume Analyst — 거래량만 본다. chart 에서 떼어낸 축이다.

가격을 보지 않는다. 이 Analyst 가 아는 것은 하나뿐이다 — **최근 5일 평균
거래량이 그 종목의 60일 평소보다 얼마나 부풀었나.**

## 왜 떼어냈나 (2026-08-21, `docs/signal-combination.md` §6)

국장 300세션(2025-05-21~2026-08-11, 835,334행)에서 chart 6피처의 단독 IC 를
따로 재 보니 이렇게 나왔다. 타깃은 전방 5일 초과수익의 횡단면 z, t 는
Newey-West(lag=4) 다.

    volume_surge     IC +0.0140   t +2.28   ← 가중치 0.10
    reversal_5       IC -0.0024   t -0.28
    ma_gap           IC -0.0031   t -0.25
    range_position   IC -0.0043   t -0.39
    momentum_20      IC -0.0054   t -0.42   ← 가중치 0.30
    momentum_60      IC -0.0147   t -1.17

**유일하게 유의한 피처가 가중치를 가장 적게 받고 있었다.** 나머지 다섯이
가중치의 90% 를 쥐고 전부 음수를 냈고, chart 합산은 -0.0046 이었다. 한 점수에
섞여 있는 한 이 축은 IC 게이트를 영영 통과하지 못한다 — 게이트가 판정하는
것은 피처가 아니라 Analyst 이기 때문이다.

일별 횡단면 상관도 같은 말을 한다. |상관| ≥ 0.6 으로 묶으면 chart 는 두
덩어리이고, `volume_surge` 는 나머지 다섯 중 어느 것과도 0.37 을 넘지 않는다
(최대는 ma_gap 0.367). **가격추세 하나 + 거래량 하나**였던 것을 이름대로
갈라 놓는 것뿐이다.

## 무엇을 믿는가

- 거래량이 평소보다 몰린 종목에는 **사람이 모르는 사건이 먼저 온다.**
  가격이 아직 안 움직였어도 관심은 거래량에 먼저 찍힌다
- **수준이 아니라 배율이다.** 삼성전자의 하루 거래량과 소형주의 하루
  거래량은 비교할 수 없다. 그 종목 자기 자신의 60일 평소로 나눈다

## 무엇을 안 넣었고 왜인가

- **정의를 손대지 않았다.** 5일/60일 창도, `- 1.0` 도, 결측 처리도 chart 에
  있던 그대로다. 여기서 창을 바꾸면 위 IC +0.0140 은 이 코드의 성적이 아니게
  되고, 갈라낸 이유였던 측정과 연결이 끊긴다. 창 튜닝은 이 축이 게이트를
  통과한 뒤에 할 일이고, 통과 전에 하면 그건 표본 적합이다
- **거래대금(`value`)을 안 넣었다.** 거래량 급증과 거래대금 급증은 가격이
  같이 오른 날 서로 닮는다 — 넣으면 가격추세를 뒷문으로 다시 들이는 셈이고,
  그건 방금 떼어낸 쪽이다
- **부호를 안 뒤집었다.** 급증이 급락의 전조인 경우도 분명히 있는데, 어느
  쪽인지는 표본이 말해야 한다. 위 측정은 양수(+2.28)였고 그 방향을 그대로
  둔다. 성적을 보고 부호를 고르는 것은 `ic.py` 가 이미 금지한다

## 지금은 관찰 모드다

`analyst_weights` 에 이 이름의 행이 없으므로 가중치가 없고
(`selector/weights.py`), 가중치 0 은 합성의 분자·분모 양쪽에서 빠진다
(`selector/combine.py`). **측정 전까지 이 Analyst 의 점수는 매매에 닿지
않는다.** IC 0.03 을 통과해야 가중치를 받는다.

위 +0.0140 은 chart 피처를 분해해 잰 값이지 이 Analyst 를 정식 게이트로
측정한 값이 아니다. 그리고 0.03 에 못 미친다 — 갈라낸 목적은 통과가 아니라
**따로 판정받는 것**이다.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from quant_rl_trading.analysts.base import Analyst, combine, rank_score

#: 60일 평소를 말하려면 이만큼은 있어야 한다. chart 와 같은 값을 쓴다 —
#: 창이 달라지면 "관측이 얕은 종목" 을 거르는 기준이 달라지고, 그러면 같은
#: 정의라도 대상 종목이 달라져 위 측정과 이어지지 않는다.
LOOKBACK_DAYS = 130

#: 피처 하나짜리 Analyst 다. 그래도 ``combine`` 을 통과시키는 이유는 분산 0
#: 가드 때문이다 — 전 종목 거래량이 똑같은 날(휴장 직후 등) 점수를 지어내지
#: 않고 0 을 낸다.
WEIGHTS = {"volume_surge": 1.0}


class VolumeAnalyst(Analyst):
    name = "volume"
    version = "volume-v0.1.0"

    def features(self, as_of: datetime) -> pd.DataFrame:
        prices = self.price_panel(as_of, lookback=LOOKBACK_DAYS)
        if prices.empty:
            return pd.DataFrame()

        close = self.wide(prices, "close")
        volume = self.wide(prices, "volume")
        # 관측이 얕은 종목은 "평소" 를 말할 수 없다. 신규 상장주를 여기서
        # 거른다 — 종가로 세는 것도 chart 그대로다. 거래량은 0 인 날이
        # 있어서 관측 수를 세는 기준으로 쓰기에 나쁘다.
        close = close.loc[:, close.notna().sum() >= 60]
        if close.empty or len(close) < 60:
            return pd.DataFrame()

        volume = volume.reindex(columns=close.columns)

        raw = pd.DataFrame(index=close.columns)
        raw["volume_surge"] = volume.tail(5).mean() / volume.tail(60).mean() - 1.0

        raw = raw.replace([np.inf, -np.inf], np.nan).dropna(how="all")
        # 결측은 그 종목의 중앙값 자리(z=0)로 둔다. 앞뒤로 채우면 미래를 본다.
        return raw.apply(rank_score).fillna(0.0)

    def raw_score(self, features: pd.DataFrame) -> pd.Series:
        return combine(features, WEIGHTS)
