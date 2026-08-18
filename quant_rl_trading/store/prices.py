"""시세 읽기 경로 — **종가 0 세션을 걷어내는 단 하나의 자리.**

## 왜 이 파일이 따로 있나

창고에는 전 종목 종가가 0 인 세션이 있다. 실측(2026-08-15 기준):

    2026-06-03  2,877종목 close=0   (지방선거 — 임시공휴일)
    2026-07-17  2,872종목 close=0   (제헌절)

둘 다 **진짜 휴장일**이다. 받아올 시세가 애초에 없으므로 정정본으로 메울
수 없다 — 읽는 쪽이 견뎌야 한다.

0 을 그대로 두면 ``pct_change`` 가 그 자리에서 ``-1.0`` 을, 그 다음 자리에서
``+inf`` 를 낸다. **전 종목이 같은 날 동시에 -100%** 이므로 그 하루가 60일
상관을 통째로 지배한다.

아래 수치는 전부 **실전 창고**에서 잰 것이고, 조건은 **KR 상위 300종목**
(entity_id 순) · 창 60거래일 · ``as_of`` 2026-08-14 다. 종목 집합이나 날짜를
바꾸면 숫자가 달라진다 — 재현할 때 보아야 할 것은 소수점이 아니라 **방향과
배율**이다.

    있는 그대로   평균 쌍상관 +0.920,  |corr|>0.7 인 쌍 94.0%
    0 행 제거     평균 쌍상관 +0.302,  |corr|>0.7 인 쌍  3.5%

그 거짓 상관이 후보 절반을 음수 알파로 만들고, Allocator 가 살아남은 소수에
비중을 몰아 MDD 를 -15.9%p 밀어 냈다. 피처 하나의 결측이 아니라 **포트폴리오
구성 전체가 뒤집히는 사고**다.

## 왜 ``Store.get`` 안에서 안 막나

게이트가 행을 말없이 고치면 "창고에 무엇이 들어 있나" 를 게이트로 물을 수
없게 된다. 그 답을 실제로 필요로 하는 모듈이 있다 — ``data_quality`` 는 0 을
**세는 것이 일**이고, 수집기의 중복 판정은 창고를 있는 그대로 봐야 한다.
그래서 게이트는 그대로 두고 **읽기 헬퍼를 하나** 둔다.

호출부마다 방어를 흩뿌리지 않는 이유는 다음 사람이 하나를 빠뜨리기
때문이고, 그 걱정은 헬퍼로는 못 막는다 — ``tools/invariant_guard.py`` 의
``price-read`` 규칙이 막는다. ``store.get("prices", ...)`` 를 직접 부르면
CI 가 잡는다.

## 왜 NaN 이 아니라 행 제거인가

휴장일이므로 **"그 날은 없었던 날"** 이 사실에 가깝다. 6/2 종가 → 6/4 종가는
하나의 진짜 1기간 수익률이고, NaN 으로 두면 그 연결이 끊겨 관측이 2개
사라진다. 실측(20일 창, KR 300종목):

    0 → NaN    변동성 산출 293종목, 관측수 18
    행 제거     변동성 산출 293종목, 관측수 20   ← 창이 온전하다

``dropna(how="all")`` 을 이미 하는 경로(상관행렬)에서는 둘이 같은 답으로
수렴하지만, ``.tail(N)`` 로 창을 자르는 경로(``session/daily.py``,
``backtest/market.py``)에서는 행 제거만 창을 온전히 채운다.

## 두 번째 일 — 기업행위 보정 (``adjusted=True``)

창고에 든 것은 **원주가**다. 액면분할·무상증자·감자·주식병합이 보정되지
않으면 실제 손실이 아닌 가격 급변이 수익률로 계산되고, 모멘텀 창이 250일이면
사건 하나가 그 뒤 250세션을 오염시킨다 (``collectors/corporate_actions.py``).

``adjusted=True`` 를 주면 ``adj_factor`` 를 **뒤에서 앞으로 누적해** 곱한다.

    조정종가(t) = close(t) × Π f(D)     for  t < D ≤ 창의 끝

**기본값은 ``False`` 다. 뒤집으면 안 된다.** 이 헬퍼는 주문·NAV·화면·백테스트
체결이 전부 공유한다. 실제 주문은 원주가로 나가야 하고, 분할 직후에는
``lookback=5``(주문 게이트)·``lookback=30``(NAV) 창 안에 사건이 들어와 **주문
수량이 배율만큼 틀어진다.** 보정가가 필요한 쪽은 수익률·변동성·상관을 만드는
모듈뿐이고, 그쪽이 명시적으로 켠다. 켰는지 여부는
``tools/invariant_guard.py`` 의 ``price-adjust`` 규칙이 지킨다.

**as_of 가 미래 분할을 막는다.** 사건 행의 ``observed_at`` 이 발효일이므로
``as_of`` 보다 나중에 발효한 사건은 게이트에서 아예 안 온다 — 여기서 따로
거를 것이 없다.

**마지막 세션은 언제나 원주가와 같다.** 누적곱이 비어 있기 때문이다. 그래서
"최신 종가" 를 쓰는 코드는 ``adjusted`` 를 무엇으로 주든 같은 값을 본다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:  # pragma: no cover - 순환 import 를 피한다
    from quant_rl_trading.store import Store

PRICES = "prices"

#: 이 컬럼이 0 이면 그 행은 시세가 아니다. 종가 하나만 본다 — 휴장일에는
#: 시가·고가·저가·거래량도 함께 0 이므로 종가로 행 전체가 걸린다.
PRICE_COLUMN = "close"

#: 기업행위 배율. 사건이 있는 세션에만 값이 있다.
FACTOR_COLUMN = "adj_factor"

#: 보정이 곱해지는 컬럼. 거래량은 곱하지 않는다 — 분할하면 주식수가 늘어
#: 거래량은 **반대로** 움직이고, 거래대금(``value``)은 애초에 안 변한다.
#: 셋을 한 배율로 밀면 유동성 필터가 통째로 틀어진다.
ADJUSTED_COLUMNS = ("open", "high", "low", "close")


def read_prices(
    store: Store,
    *,
    as_of: datetime,
    entity: str | Sequence[str] | None = None,
    lookback: timedelta | int | None = None,
    until: datetime | None = None,
    columns: Sequence[str] | None = None,
    market: str | None = None,
    adjusted: bool = False,
) -> pd.DataFrame:
    """``store.get("prices", ...)`` + 종가 0 세션 제거 (+ 선택적 기업행위 보정).

    인자는 ``Store.get`` 과 같다. 게이트를 우회하지 않는다 —
    ``observed_at <= as_of`` 도 정정본 선택도 그대로 걸린다 (불변식 1).

    ``columns`` 에 ``close`` 가 없어도 된다. 거르는 데 필요하므로 몰래 얹어
    읽고 돌려줄 때 다시 뺀다 — 호출부가 받는 컬럼은 요청한 그대로다.
    ``adjusted=True`` 면 ``adj_factor`` 도 같은 식으로 몰래 얹는다.

    ``adjusted`` 는 **수익률·변동성·상관을 만드는 쪽만** 켠다. 기본값을 왜 안
    뒤집는지는 모듈 독스트링을 보라 — 주문 수량이 걸린 문제다.
    """
    requested = list(columns) if columns is not None else None
    fetch = requested
    if requested is not None:
        needed = [PRICE_COLUMN]
        if adjusted:
            # 보정에 필요한 컬럼도 같이 얹는다. 요청한 것만 돌려주므로
            # 호출부가 받는 축은 달라지지 않는다.
            needed = [*needed, FACTOR_COLUMN, *ADJUSTED_COLUMNS]
        extra = [name for name in needed if name not in requested]
        if extra:
            fetch = [*requested, *dict.fromkeys(extra)]

    frame = store.get(
        PRICES,
        as_of=as_of,
        entity=entity,
        lookback=lookback,
        until=until,
        columns=fetch,
        market=market,
    )
    alive = drop_dead_sessions(frame)
    if adjusted:
        alive = adjust(alive)
    return alive if requested is None else _project(alive, requested)


def drop_dead_sessions(
    frame: pd.DataFrame, *, keep: Sequence[str] | None = None
) -> pd.DataFrame:
    """종가가 0 이하이거나 결측인 행을 뺀다.

    ``read_prices`` 를 못 쓰는 자리(이미 손에 프레임이 있는 경우)를 위해
    갈라 둔다. 창고를 읽는 쪽과 프레임을 손질하는 쪽이 같은 규칙을 쓰도록
    규칙 자체는 여기 한 벌만 둔다.
    """
    if frame.empty or PRICE_COLUMN not in frame.columns:
        return frame if keep is None else _project(frame, keep)

    close = pd.to_numeric(frame[PRICE_COLUMN], errors="coerce")
    alive = frame[close.notna() & (close > 0.0)]
    return alive if keep is None else _project(alive, keep)


#: 우리가 몰래 얹을 수 있는 컬럼. 돌려줄 때 이 중 요청 밖인 것만 뺀다.
_HELPER_COLUMNS = frozenset({PRICE_COLUMN, FACTOR_COLUMN, *ADJUSTED_COLUMNS})


def _project(frame: pd.DataFrame, keep: Sequence[str]) -> pd.DataFrame:
    """거르려고 얹은 컬럼을 도로 뺀다. 게이트가 늘 주는 축은 남긴다."""
    # ``entity_id``·``valid_from`` 같은 축은 ``columns`` 에 안 적어도 게이트가
    # 늘 준다. 우리가 얹은 것만 뺀다.
    wanted = set(keep)
    added = [
        name for name in frame.columns if name in _HELPER_COLUMNS and name not in wanted
    ]
    return frame.drop(columns=added) if added else frame


def adjust(frame: pd.DataFrame) -> pd.DataFrame:
    """원주가 → 기업행위 보정가. ``adj_factor`` 를 뒤에서 앞으로 누적해 곱한다.

    ``t`` 의 보정가는 ``t`` **이후에** 발효한 배율만 곱한다. 발효일 당일은
    이미 새 기준가로 거래된 날이므로 자기 자신의 배율은 곱하지 않는다 —
    그래서 누적을 한 칸 밀어(``shift(-1)``) 뒤에서부터 접는다.

    ``adj_factor`` 가 없는 프레임은 그대로 돌려준다. 아직 계수를 안 채운
    구간(미장·오래된 백필)에서 조용히 값이 바뀌는 것보다, 보정이 **안 된
    것이 눈에 보이는** 편이 낫다.

    프레임에 든 마지막 세션은 누적곱이 비어 있어 언제나 원주가와 같다.
    """
    if frame.empty or FACTOR_COLUMN not in frame.columns:
        return frame

    factor = pd.to_numeric(frame[FACTOR_COLUMN], errors="coerce").fillna(1.0)
    if bool((factor == 1.0).all()):
        # 사건이 하나도 없는 창이 대부분이다. 정렬·groupby 를 아예 건너뛴다.
        return frame

    ordered = frame.sort_values(["entity_id", "valid_from"], kind="stable")
    factor = pd.to_numeric(ordered[FACTOR_COLUMN], errors="coerce").fillna(1.0)
    entity = ordered["entity_id"]

    # **``transform`` 에 파이썬 람다를 주지 않는다.** 종목이 3천 개면 그 람다가
    # 3천 번 돌고, 피처 경로(analysts/base.py)가 매번 이 함수를 부른다 —
    # 실측으로 25만 행에 1.1초였다. 아래는 같은 계산을 전부 벡터로 한다(0.05초).
    #
    # 거꾸로 누적곱하면 행 i 에서 "끝에서 i 까지" 의 곱이 나온다. 우리가
    # 원하는 것은 **i 를 뺀** 곱(발효일 당일은 이미 새 기준가로 거래됐다)이라,
    # 종목 안에서 한 칸 당겨(``shift(-1)``) 다음 행의 값을 쓴다.
    reversed_cumprod = factor.iloc[::-1].groupby(entity.iloc[::-1], sort=False).cumprod()
    trailing = reversed_cumprod.iloc[::-1].groupby(entity, sort=False).shift(-1).fillna(1.0)

    adjusted = ordered.copy()
    for name in ADJUSTED_COLUMNS:
        if name in adjusted.columns:
            adjusted[name] = pd.to_numeric(adjusted[name], errors="coerce") * trailing
    return adjusted.reindex(frame.index)
