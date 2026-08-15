"""호가단위(tick). 순수 코드.

## 표의 근거

`data/curated/prices` 실제 종가(2026-08-12~14, KR 8,615건)를 떠서 얻은 값이다.
KRX 는 2023년에 코스피·코스닥 호가단위 표를 통합 개편했다 — "1,000원 미만
1원, 1,000~5,000원 5원" 같은 교과서 표는 낡았다. 지금은 **2,000원 미만이
1원 구간**이다.

```
      가격대           호가단위
     ~2,000원              1원
 2,000~5,000원              5원
 5,000~20,000원            10원
20,000~50,000원            50원
50,000~200,000원          100원
200,000~500,000원         500원
500,000원~               1,000원
```

## 2,000~5,000원 구간에서 1원으로 보인 4건 — 정체는 청약기 스팩

같은 대역에서 5의 배수가 아닌 종가 4건을 실제로 추적했다. 넷 다 스팩
(예: `유안타제16호스팩`, `신한제12호스팩`)이고, 넷 다 `universe.is_tradable
== False` 였다. 2026년 7월 한 달 전체(스팩 71종목, 1,562건)로 넓혀도 위반은
전부 `is_tradable=False` 구간에서만 나왔고(1.8%), 값은 전부 2,000~2,100원
경계 바로 위 — 청약 단계라 체결이 없고, 체결이 없으니 호가단위를 지킬
근거 자체가 없는 지표성 가격으로 보인다. 집행기는 애초에 `is_tradable=False`
종목에 주문을 내지 않으므로(``sizing.py``) 이 값은 운영에 영향이 없다.

일반 종목(스팩 제외, 61,503건) 위반율은 0.0016% — 단 1건(YW, KR:051390,
2026-07-28, 종가 3,927원)뿐이었고, 같은 종목의 다른 22거래일은 전부 5의
배수였다. 데이터 잡음 한 건으로 판단했다 — 표를 바꿀 근거가 아니다.

## 창고에 종목 유형이 없다 — 이 표가 모든 미래를 보장하지 않는다

`universe` 테이블에는 ETF/ETN/리츠/스팩을 구분하는 필드가 없다(``name``
문자열뿐). ETF/ETN 은 실제로 이 표와 다른(더 촘촘한, 예: 5,000원 미만 균일
5원) 호가단위를 쓴다고 알려져 있다. 다만 지금 유니버스를 훑어도 ETF/ETN
이름 패턴(KODEX·TIGER·ACE·KBSTAR·SOL·HANARO·KOSEF·KINDEX·ETN 등)이 하나도
없다 — 지금 거래 대상은 보통주·리츠·스팩뿐이고, 이 셋은 2023년 개편 이후
같은 표를 쓴다. **ETF/ETN 이 유니버스에 들어오는 순간 이 표는 그 종목엔 안
맞을 수 있다.** 종목 유형을 모른 채로 이 표 하나로 뭉개지 않기 위해, 알 수
없는 경우를 가정하지 않고 여기 명시해 둔다 — 새 유형이 들어오면 이 표부터
다시 검증해야 한다.
"""

from __future__ import annotations

import math

from quant_rl_trading.schemas.order import Side

#: (미만 상한, 그 아래 구간의 호가단위). 오름차순 — 표 근거는 모듈 docstring.
_TICK_BANDS: tuple[tuple[float, int], ...] = (
    (2_000, 1),
    (5_000, 5),
    (20_000, 10),
    (50_000, 50),
    (200_000, 100),
    (500_000, 500),
)
#: 500,000원 이상 — 표의 마지막 칸. 상한이 없어 별도로 둔다.
_TOP_TICK = 1_000

#: float 나눗셈 오차 흡수용. 정확히 tick 배수인 가격이 부동소수점 오차로
#: 한 칸 밀려나는 것(예: 55600/100 이 554.999999996 으로 나오는 것)을 막는다.
_EPS = 1e-9

#: 미장 호가단위 — SEC Rule 612 (sub-penny). $1 미만은 $0.0001, 이상은
#: $0.01. `docs/design/ls-api.md` §0-10 실측(``g3104.untprc``)과 일치한다:
#: AAPL($305.26)·WEN($8.65)·BRK.A($762,000) 전부 0.01, LTRYW($0.0070)만
#: 0.0001. **KR 표(``_TICK_BANDS``)를 달러 가격에 그대로 먹이면 안 된다** —
#: 원화 구간 임계값(2,000 / 5,000 / …)이 달러에서는 아무 의미가 없다. 예:
#: $50 짜리 미국 주식은 KR 표로 "2,000원 미만" 칸에 걸려 tick=1(=$1)이 되고,
#: 실제 필요한 $0.01 보다 100배 거친 호가로 반올림된다.
_US_SUBPENNY_THRESHOLD = 1.0
_US_SUBPENNY_TICK = 0.0001
_US_TICK = 0.01


def tick_size(price: float) -> int:
    """가격이 속한 호가단위(KR). 표 근거는 모듈 docstring.

    **원화 전용이다.** 미장 호가단위는 이 표와 무관하다 — ``round_to_tick``
    이 ``market`` 인자로 갈라 받는다.
    """
    if price <= 0:
        raise ValueError(f"호가단위는 양수 가격에서만 정의된다: {price}")
    for threshold, tick in _TICK_BANDS:
        if price < threshold:
            return tick
    return _TOP_TICK


def us_tick_size(price: float) -> float:
    """미장 호가단위. 표 근거는 ``_US_SUBPENNY_*`` 상수 주석."""
    if price <= 0:
        raise ValueError(f"호가단위는 양수 가격에서만 정의된다: {price}")
    return _US_SUBPENNY_TICK if price < _US_SUBPENNY_THRESHOLD else _US_TICK


def round_to_tick(price: float, *, side: Side, market: str = "KR") -> float:
    """유효 호가로 반올림한다.

    **매수는 내림, 매도는 올림** — 슬리피지 상한을 넘지 않는 방향으로만
    옮긴다. 매수 상한을 올림하면 상한을 넘어 더 비싸게 사고, 매도 하한을
    내림하면 하한 아래로 더 싸게 판다 — 둘 다 ``orders.limit_price`` 가
    약속한 "상한 안에서만" 을 깬다.

    ``market`` 기본값이 ``"KR"`` 인 것은 기존 호출부(국장) 계약을 그대로
    지키기 위해서다. ``"US"`` 면 원화 표 대신 ``us_tick_size`` 를 쓴다 —
    둘을 헷갈리면 미장 주문이 원화 호가단위로 반올림되는 조용한 오류가 된다
    (2026-08-15 발견, `docs/design/ls-api.md` §0-10).
    """
    tick = us_tick_size(price) if market == "US" else tick_size(price)
    scaled = price / tick
    if side is Side.BUY:
        return math.floor(scaled + _EPS) * tick
    return math.ceil(scaled - _EPS) * tick
