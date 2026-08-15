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


def read_prices(
    store: Store,
    *,
    as_of: datetime,
    entity: str | Sequence[str] | None = None,
    lookback: timedelta | int | None = None,
    until: datetime | None = None,
    columns: Sequence[str] | None = None,
    market: str | None = None,
) -> pd.DataFrame:
    """``store.get("prices", ...)`` + 종가 0 세션 제거.

    인자는 ``Store.get`` 과 같다. 게이트를 우회하지 않는다 —
    ``observed_at <= as_of`` 도 정정본 선택도 그대로 걸린다 (불변식 1).

    ``columns`` 에 ``close`` 가 없어도 된다. 거르는 데 필요하므로 몰래 얹어
    읽고 돌려줄 때 다시 뺀다 — 호출부가 받는 컬럼은 요청한 그대로다.
    """
    requested = list(columns) if columns is not None else None
    fetch = requested
    if requested is not None and PRICE_COLUMN not in requested:
        fetch = [*requested, PRICE_COLUMN]

    frame = store.get(
        PRICES,
        as_of=as_of,
        entity=entity,
        lookback=lookback,
        until=until,
        columns=fetch,
        market=market,
    )
    return drop_dead_sessions(frame, keep=requested)


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


def _project(frame: pd.DataFrame, keep: Sequence[str]) -> pd.DataFrame:
    """거르려고 얹은 컬럼을 도로 뺀다. 게이트가 늘 주는 축은 남긴다."""
    extra = [name for name in frame.columns if name not in keep]
    if not extra:
        return frame
    # ``entity_id``·``valid_from`` 같은 축은 ``columns`` 에 안 적어도 게이트가
    # 늘 준다. 우리가 얹은 것만 뺀다.
    added = [name for name in extra if name == PRICE_COLUMN]
    return frame.drop(columns=added) if added else frame
