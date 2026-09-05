"""가격 지표. **한 벌만 둔다.**

RSI 는 평활 방식이 두 가지로 돌아다닌다 — Wilder 의 지수평활과 단순이동
평균 변형이다. **값이 다르게 나온다.** 화면과 메일이 각자 고르면 같은 지수의
RSI 가 두 개가 되고, 둘 중 어느 것이 맞는지 물었을 때 답할 사람이 없다.

그래서 계산은 여기 한 벌만 두고 부르는 쪽은 전부 이걸 쓴다.
"""

from __future__ import annotations

import pandas as pd


def wilder_rsi(closes: pd.Series, period: int) -> float | None:
    """RSI. 표본이 모자라면 **None** — 지어내지 않는다.

    Wilder 원식이다(지수평활, alpha=1/period).

    **하락이 하나도 없는 구간은 100 이다.** 0 으로 나누는 자리라 그냥 두면
    inf 가 나오고, inf 하나가 조용히 퍼지는 사고를 이 저장소는 이미 겪었다
    (종가 0 세션이 Analyst 를 침묵시킨 건).

    **아예 안 움직인 구간은 50 이다.** 상승도 하락도 0 인데 100 을 주면
    "쉼 없이 오르는 중" 과 "안 움직임" 이 같은 값이 된다 — 정반대다.
    """
    values = closes.dropna()
    if len(values) < period + 1:
        return None
    delta = values.diff().dropna()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean().iloc[-1]
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean().iloc[-1]
    if avg_loss <= 0:
        return 100.0 if avg_gain > 0 else 50.0
    return float(100.0 - 100.0 / (1.0 + avg_gain / avg_loss))


def wilder_rsi_series(closes: pd.Series, period: int) -> list[float | None]:
    """RSI 시계열. 창이 모자라는 앞머리는 **None** 으로 둔다.

    화면이 선을 그으려면 마지막 값 하나가 아니라 줄이 필요하다. 앞머리를
    0 이나 50 으로 채우지 않는 이유는 `wilder_rsi` 와 같다 — 못 잰 구간과
    잰 구간은 화면에서 달라 보여야 한다.
    """
    values = closes.astype(float)
    delta = values.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rsi = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    # 손실이 0 인 구간은 위에서 inf 가 된다. Wilder 정의대로 100 으로 둔다.
    rsi = rsi.where(avg_loss > 0, 100.0)
    # 움직임이 아예 없으면 50.
    rsi = rsi.where((avg_loss > 0) | (avg_gain > 0), 50.0)
    out: list[float | None] = []
    for index, value in enumerate(rsi):
        if index < period or pd.isna(value):
            out.append(None)
        else:
            out.append(round(float(value), 2))
    return out
