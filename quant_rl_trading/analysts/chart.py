"""Chart Analyst — 가격만 본다. 거래량은 2026-08-21 에 나갔다.

agents.md §2 의 5개 축을 쓰되 **피처를 적게 유지한다.** 기술지표는 상호 상관이
0.9 를 넘는 것이 흔해서, 30개를 넣으면 30개 값어치가 아니라 5개 값어치가
나오고 과적합만 심해진다.

## volume_surge 를 떼어냈다 (2026-08-21) — 남은 것은 가격추세뿐이다

`docs/signal-combination.md` §6 의 피처별 IC(국장 300세션, 835,334행, 타깃은
전방 5일 초과수익의 횡단면 z, t 는 Newey-West lag=4):

    volume_surge     +0.0140   t +2.28   ← 가중치 0.10 · **유일하게 유의했다**
    reversal_5       -0.0024   t -0.28
    ma_gap           -0.0031   t -0.25
    range_position   -0.0043   t -0.39
    momentum_20      -0.0054   t -0.42   ← 가중치 0.30
    momentum_60      -0.0147   t -1.17

**작동하는 축 하나가 안 되는 축 다섯과 한 점수에 섞여 있었다.** 게이트는
피처가 아니라 Analyst 를 판정하므로, 섞여 있는 한 volume_surge 는 영영 따로
평가받지 못한다. 그래서 `analysts/volume.py` 로 내보냈다 — 정의는 손대지
않았다.

남은 다섯은 이 Analyst 가 원래 이름값으로 하던 일, 즉 **가격추세**다. 합산
IC 는 -0.0046(t -0.35) 이고 게이트 근처에도 못 간다. 차원 축소로는 안
고쳐진다 — 군집 PCA 1축이 -0.0044 로 원본과 사실상 같았다. 겹쳐서 나쁜 것이
아니라 **그 축 자체에 알파가 없다.**

**여기서 가중치를 다시 배분하지 않는다.** 빠진 0.10 을 남은 다섯에게 나눠
주는 것은 이 표본의 성적을 보고 가중치를 고르는 일이다. ``combine`` 이 살아
있는 가중치 합으로 나누므로 다섯의 상대 비율은 그대로 유지된다.

## 실측 (2026-08-13, KR 300세션) — **이 Analyst 는 아직 관찰 모드다**

피처별 IC 를 재 보니 chart 고유 피처는 전부 잡음이었다. 폴드 부호가 갈리는
것은 약한 신호가 아니라 잡음이다.

    momentum_20     +0.0000   (가중치 25% — 가장 큰 몫이 IC 0 이었다)
    momentum_60     -0.0095
    reversal_5      -0.0007
    ma_gap          +0.0023
    volume_surge    +0.0118
    range_position  +0.0029

합산 IC 가 +0.0197 이었던 것은 여기 섞여 있던 ``low_volatility``(+0.0894)
때문이고, 그건 risk 의 피처다. 빼고 나면 chart 는 통과하지 못한다 — 그리고
그것이 이 Analyst 의 정직한 성적이다.

**가중치를 low_volatility 쪽으로 몰아 통과시키지 않는다.** 그러면 risk 의
복제본이 되고, 통과 개수는 늘지만 새로 아는 것은 없다.

다음은 가중치 조정이 아니라 **피처 교체**다. 20일·60일 모멘텀이 안 먹히는
표본에서 가중치를 바꾸는 것은 잡음의 배합을 바꾸는 일이다.

M2 는 LightGBM 없이 **가중 합**으로 간다. 이유는 하나다 — 학습을 얹기 전에
피처와 라벨 배관이 정직한지 먼저 봐야 하기 때문이다. 룰로 IC 0.03 이 안 나오는
피처는 LightGBM 을 얹어도 대개 안 나오고, 나온다면 그건 누수를 의심할 일이다.
모델 교체는 이 파일의 ``raw_score`` 하나만 바꾸면 된다.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from quant_rl_trading.analysts.base import Analyst, combine, rank_score

#: 피처 계산에 필요한 최소 관측 창.
LOOKBACK_DAYS = 130

#: 축별 가중치. 부호가 뜻을 담는다 — 단기 반전은 음수다.
#:
#: **low_volatility 를 뺐다 (2026-08-13).** risk 의 주력 피처(가중치 0.45)와
#: 같은 것이라, 여기 두면 chart 점수의 대부분이 risk 에게 빌린 신호가 된다.
#: 실측이 정확히 그랬다 — 피처별 IC 에서 low_volatility 만 +0.0894 로 혼자
#: 서 있고 chart 고유의 가격 피처 여섯은 전부 잡음이었다(아래 표).
#:
#: 빌린 신호로 통과시키면 M3 Selector 가 **같은 신호를 두 번 센다.** 저변동성
#: 한쪽으로 펀드가 쏠리고, 통과 개수만 늘어난다.
#:
#: **volume_surge 를 뺐다 (2026-08-21).** 별도 Analyst(`analysts/volume.py`)로
#: 나갔다. 아래 다섯의 합은 0.95 이고 **여기서 다시 정규화하지 않는다** —
#: ``combine`` 이 살아 있는 가중치의 절대값 합으로 나누므로 남은 다섯의 상대
#: 비율은 그대로다. 빠진 0.10 을 다섯에게 나눠 주는 것은 이 표본의 IC 를 보고
#: 가중치를 고르는 일이고, 그건 표본 적합이다.
WEIGHTS = {
    "momentum_20": 0.30,
    "momentum_60": 0.20,
    "reversal_5": -0.20,
    "ma_gap": 0.15,
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
        # 관측이 얕은 종목은 지표가 잡음이다. 신규 상장주를 여기서 거른다.
        close = close.loc[:, close.notna().sum() >= 60]
        if close.empty or len(close) < 60:
            return pd.DataFrame()

        raw = pd.DataFrame(index=close.columns)
        raw["momentum_20"] = close.iloc[-1] / close.shift(20).iloc[-1] - 1.0
        raw["momentum_60"] = close.iloc[-1] / close.shift(60).iloc[-1] - 1.0
        # 단기 반전. 5일 급등은 되돌리는 경향이 있어 부호를 뒤집어 쓴다.
        raw["reversal_5"] = close.iloc[-1] / close.shift(5).iloc[-1] - 1.0
        raw["ma_gap"] = close.iloc[-1] / close.tail(20).mean() - 1.0
        # 저변동성 프리미엄. 부호를 뒤집어 "낮을수록 좋다" 로 만든다.
        window = close.tail(120)
        span = window.max() - window.min()
        raw["range_position"] = (close.iloc[-1] - window.min()) / span.replace(0.0, np.nan)

        raw = raw.replace([np.inf, -np.inf], np.nan).dropna(how="all")
        # 결측은 그 종목의 중앙값 자리(z=0)로 둔다. 앞뒤로 채우면 미래를 본다.
        return raw.apply(rank_score).fillna(0.0)

    def raw_score(self, features: pd.DataFrame) -> pd.Series:
        return combine(features, WEIGHTS)
