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

import math
from datetime import date, datetime
from typing import TYPE_CHECKING

from quant_rl_trading.executor.ticks import tick_size as kr_tick_size
from quant_rl_trading.executor.ticks import us_tick_size
from quant_rl_trading.replay.fills import MarketState
from quant_rl_trading.store.prices import adjust, read_prices

if TYPE_CHECKING:
    from quant_rl_trading.store import Store

#: 평균 거래량을 재는 창(거래일). session/daily.py 의 STATS_WINDOW 와 같다.
VOLUME_WINDOW = 20

#: 변동성을 계산할 표본이 없을 때 쓰는 값. 낙관적인 0 보다 낫다.
DEFAULT_VOLATILITY = 0.02


def _positive(value: object) -> float | None:
    """저가·고가는 결측일 수 있다. 0 이나 NaN 을 그대로 넘기면 매수 지정가가
    항상 닿은 것이 되어 체결이 공짜가 된다 — 모르면 ``None`` 으로 넘겨
    시뮬레이터가 종가로 판단하게 둔다."""
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def states(
    store: Store,
    *,
    as_of: datetime,
    entities: list[str],
    market: str,
    session_day: date | None = None,
) -> dict[str, MarketState]:
    """체결일의 종목별 시장 상태. **그날 봉이 없는 종목은 빠진다.**

    빠진 종목은 호출자가 미체결로 처리한다. 직전 종가로 때우면 거래정지
    종목을 정지 중에 사고파는 백테스트가 된다.

    ``session_day`` 는 **봉의 날짜**다. 주지 않으면 ``as_of`` 의 달력 날짜를 쓰는데,
    그건 국장에서만 맞는다 — 미장 세션 시각(ET 16:20 = KST 익일 05:20)의 KST 날짜는
    봉 날짜(ET 기준)보다 하루 뒤라, 2026-09-03 미장 첫 체결이 전부 "체결일 시세 없음"
    으로 빠졌다. 호출자(backtest/loop)는 자기가 도는 세션 날짜를 안다 — 그걸 준다.
    """
    if not entities:
        return {}
    frame = read_prices(
        store,
        as_of=as_of,
        entity=entities,
        lookback=VOLUME_WINDOW * 3,
        market=market,
        # low·high 는 지정가 체결 판정에 쓴다(replay/fills.py). 종가만 퍼오면
        # 저가가 지정가를 스친 날이 전부 미체결로 적힌다.
        columns=["close", "volume", "low", "high", "adj_factor"],
    )
    if frame.empty:
        return {}

    session_day = session_day or as_of.date()
    # **체결가는 원주가, 변동성은 보정가.** 체결은 실제로 거래된 값으로 해야
    # 하고(보정가로 체결하면 분할 직후 수량·금액이 배율만큼 틀어진다),
    # 변동성은 수익률로 재므로 반대다 — 분할 하루가 -90% 로 남으면 충격비용이
    # 그만큼 부풀어 그 종목만 체결이 불가능해진다. session/daily.py 의
    # ``market_stats`` 와 같은 규칙이다 (불변식 5: 백테스트와 라이브는 같은
    # 코드를 쓴다).
    ordered = frame.sort_values("valid_from")
    adjusted_close = {
        str(entity): group["close"].astype(float)
        for entity, group in adjust(ordered).groupby("entity_id")
    }
    out: dict[str, MarketState] = {}
    for entity, group in ordered.groupby("entity_id"):
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
        series = adjusted_close.get(str(entity), group["close"].astype(float))
        returns = series.tail(VOLUME_WINDOW + 1)
        volatility = float(returns.pct_change(fill_method=None).dropna().std())
        out[str(entity)] = MarketState(
            entity_id=str(entity),
            close=close,
            volume=volume,
            adv=adv,
            # 변동성이 없으면 충격비용이 0 이 된다. 그건 공짜 체결이라
            # 백테스트를 실제보다 좋게 만든다 — 그럴 바엔 최근 시장 평균을 쓴다.
            volatility=volatility if volatility > 0 else DEFAULT_VOLATILITY,
            low=_positive(today["low"].iloc[-1]),
            high=_positive(today["high"].iloc[-1]),
            # 2026-08-15 발견: 이 필드가 비어 있어(기본값 0.0) fills.py 의
            # tick 반올림이 통째로 no-op 이었다 — 체결가가 실제 호가단위
            # 격자 밖에 놓일 수 있었다. 지정가 자체(orders.limit_price)는
            # 이미 반올림돼 들어오므로 영향은 충격비용을 얹은 뒤의 값에만
            # 있었지만, 그 값도 유효 호가여야 한다.
            tick_size=(
                us_tick_size(close) if market.upper() == "US" else kr_tick_size(close)
            ),
        )
    return out
