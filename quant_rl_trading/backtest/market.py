"""체결 시점의 시장 상태를 창고에서 만든다.

``replay/fills.py`` 는 순수 함수다 — 창고를 모른다. 그 입력을 만드는 일이
여기다. 두 개를 한 파일에 두면 시뮬레이터가 조회를 하게 되고, 그 순간 같은
입력이 같은 출력을 준다는 성질을 잃는다.

## 수량 축과 금액 축을 섞지 않는다

``MarketState.adv`` 와 ``volume`` 은 **주식 수**다(충격비용이 주문 수량을 그것으로
나눈다). Selector·Executor 가 쓰는 ``adv_value`` 는 **거래대금(원)** 이다.
둘을 섞으면 충격비용이 수천 배로 틀리고, 틀린 방향은 언제나 백테스트에 유리한
쪽이다 — 원 단위 ADV 로 나누면 비용이 0 에 수렴한다.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from quant_rl_trading.replay.fills import MarketState

if TYPE_CHECKING:
    from quant_rl_trading.store import Store

PRICES = "prices"

#: 평균 거래량을 재는 창(거래일). session/daily.py 의 STATS_WINDOW 와 같다.
VOLUME_WINDOW = 20

#: 변동성을 계산할 표본이 없을 때 쓰는 값. 낙관적인 0 보다 낫다.
DEFAULT_VOLATILITY = 0.02


def states(
    store: Store, *, as_of: datetime, entities: list[str], market: str
) -> dict[str, MarketState]:
    """체결일의 종목별 시장 상태. **그날 봉이 없는 종목은 빠진다.**

    빠진 종목은 호출자가 미체결로 처리한다. 직전 종가로 때우면 거래정지
    종목을 정지 중에 사고파는 백테스트가 된다.
    """
    if not entities:
        return {}
    frame = store.get(
        PRICES,
        as_of=as_of,
        entity=entities,
        lookback=VOLUME_WINDOW * 3,
        market=market,
        columns=["close", "volume"],
    )
    if frame.empty:
        return {}

    session_day = as_of.date()
    out: dict[str, MarketState] = {}
    for entity, group in frame.sort_values("valid_from").groupby("entity_id"):
        today = group[group["valid_from"].dt.date == session_day]
        if today.empty:
            continue
        close = float(today["close"].iloc[-1])
        volume = float(today["volume"].iloc[-1])
        if close <= 0 or volume <= 0:
            # 종가 0 은 데이터 사고이고, 거래량 0 은 그날 아무도 못 산 것이다.
            continue
        history = group["volume"].astype(float).tail(VOLUME_WINDOW)
        adv = float(history.mean())
        if adv <= 0:
            continue
        returns = group["close"].astype(float).tail(VOLUME_WINDOW + 1)
        volatility = float(returns.pct_change(fill_method=None).dropna().std())
        out[str(entity)] = MarketState(
            entity_id=str(entity),
            close=close,
            volume=volume,
            adv=adv,
            # 변동성이 없으면 충격비용이 0 이 된다. 그건 공짜 체결이라
            # 백테스트를 실제보다 좋게 만든다 — 그럴 바엔 최근 시장 평균을 쓴다.
            volatility=volatility if volatility > 0 else DEFAULT_VOLATILITY,
        )
    return out
