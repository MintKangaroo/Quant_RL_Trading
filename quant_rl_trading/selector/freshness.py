"""선정이 읽는 신호가 **그 세션 것인가** — 묵은 신호로 조용히 고르지 않는다 (selector.md §5 0번).

## 왜

선정은 ``store.get(signals, lookback=5)`` 로 최근 5일 신호를 읽고, (종목, Analyst) 마다 **가장 늦은 행**을 쓴다
(`combine.combined_scores`). 그래서 어떤 Analyst 가 오늘 점수를 안 냈으면:

- 1~4일 전 점수가 있으면 **그 묵은 점수로 조용히 고른다** — 후보는 차 있고 rc 는 0 이다
- 5일이 넘으면 그 Analyst 가 통째로 빠진다 — 단독 알파면 후보 0 으로 **동결**(주문 없이 전날 보유 유지), rc 는 0 이다

측정표(`analyst_weights`) 알파는 일일 실행기(`session/signals.produce`)가 같은 프로세스에서 다 같이 내고, 죽으면
`analyst_failures` 에 남아 재부팅 복구 관문이 본다. **덮어쓰기 알파**(`selector.weights_override`, 10/13 BE2)와
**하한·상한 Analyst**(`tsfm`·`ta`)는 일일 실행기 밖의 별도 크론이 쓴다 — 그 크론이 실패해도 세션은 모른다.
2026-10-11 점검에서 재현했다(be2 신호 없이 세션이 fault 없이 후보 0, 4일 묵은 be2 로 조용히 채점).

## 두 규약 — 비교 기준이 다르다

신호는 둘 중 하나의 규약으로 적힌다. **어느 쪽인지가 "그 세션 것" 의 정의를 바꾼다.**

=========  =====================================================  =====================================
규약       누가 · valid_from                                       세션 S(as_of) 의 신선한 신호
=========  =====================================================  =====================================
일일       일일 실행기·`tools/score_be2.py` — ``valid_from =         ``valid_from ≥ S 의 정규장 마감``
           observed_at = 세션 공표 시각``(KR 16:00 KST)             (= S 세션 점수. 전날 것은 전날 마감 뒤라 걸린다)
다음세션   `tools/tsfm_signal.py`·`tools/ta_signal.py` — 밤에 마지막   ``valid_from ≥ S 직전 거래일의 정규장 마감``
           종가까지로 계산, ``valid_from = observed_at = 계산 시각``    (설계상 하루 늦춤 — 그 전날 밤 계산분이 정상)
=========  =====================================================  =====================================

**S 는 as_of 에 마감이 지난 마지막 거래일**이다(``decision_session``). 세션 실행기의 as_of 는 그 세션의 공표 시각이라
그날이고, 화면이 장 중 시각으로 부르면 전날 세션이다 — 둘 다 "지금 결정에 쓰일 세션" 과 같다.
**빈틈**: 15:30(마감)~22:56(be2 채점) 사이 시각으로 부르면 오늘이 세션인데 오늘 점수가 아직 없어 `alpha_stale` 이 뜬다 —
세션 실행기·화면(why·pipeline)은 세션 as_of(16:00)로 부르므로 해당 없지만, 손으로 그 시각에 부르면 오탐이다(독립 검토 10/11).

``observed_at`` 이 아니라 ``valid_from`` 을 보는 이유: 지난 세션의 정정본(rev1)은 오늘 밤 적혀 ``observed_at`` 이
늦지만 ``valid_from`` 은 그 지난 세션이다. ``observed_at`` 으로 재면 어제 점수의 정정본이 오늘 점수로 보인다.

마감 시각은 정규장 마감(`market_hours.SPECS`)이다. 조기 폐장일에도 마감은 그보다 이르므로 일일 규약 신호
(공표 시각 ≥ 실제 마감)는 늘 통과하고, 묵은 신호(전 세션 공표 시각 < 이번 세션 마감)는 늘 걸린다.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

from quant_rl_trading.collectors.market_hours import (
    SPECS,
    Market,
    is_trading_day,
    local_time,
    previous_trading_day,
)

#: 덮어쓰기 알파(`selector.weights_override`)의 신호가 그 세션 것이 아니다 — 묵었거나 없다.
ALPHA_STALE = "alpha_stale"
#: 켜진 하한·상한(`selector.extra_floor_*`·`ceiling_*`·`combo_floor_*`) Analyst 의 신호가 그 세션에 쓸 것이 아니다.
CONSTRAINT_STALE = "constraint_stale"
#: 알파 Analyst 의 신호가 다 있는데 confidence 가 전원 0 이다 — 합성 점수가 0건이라 후보 0 동결(analysts/ic.py
#: `rolling_confidence` 가 롤링 IC ≤ 0 이면 0). 단독 알파(현행 ranker 하나 · BE2 하나)면 한 Analyst 의 나쁜 60일이 매매를 멈춘다.
ALPHA_NO_CONFIDENCE = "alpha_no_confidence"


def session_close(market: Market, day: date) -> datetime:
    """``day`` 정규장 마감 시각(tz-aware)."""
    spec = SPECS[market]
    return datetime.combine(day, spec.regular_close, tzinfo=ZoneInfo(spec.timezone))


def decision_session(market: Market, as_of: datetime) -> date:
    """as_of 에 마감이 지난 마지막 거래일 — 그 시각의 결정이 쓰는 세션."""
    here = local_time(market, as_of)
    day = here.date()
    if is_trading_day(market, day) and here >= session_close(market, day):
        return day
    return previous_trading_day(market, day)


@dataclass(frozen=True)
class Staleness:
    analyst: str
    #: 이 Analyst 의 가장 늦은 valid_from(시장 지역 날짜). 읽은 창(최근 5일)에 행이 없으면 None.
    last: date | None
    session: date

    def describe(self) -> str:
        seen = f"마지막 신호 {self.last.isoformat()}" if self.last else "최근 5일 신호 없음"
        return f"{self.analyst}({seen}, 세션 {self.session.isoformat()})"


def stale(
    signals: pd.DataFrame,
    analysts: Iterable[str],
    *,
    market: str,
    as_of: datetime,
    next_session: bool = False,
) -> list[Staleness]:
    """``analysts`` 중 신호가 세션 S 것이 아닌 것. 빈 목록이면 전부 신선하다.

    ``next_session`` 이 참이면 다음세션 규약(tsfm·ta) — S 직전 거래일 마감 뒤에 계산된 것이면 신선하다.
    거짓이면 일일 규약 — S 마감 뒤(= S 공표 시각)여야 한다. 모듈 독스트링의 표.
    """
    enum = Market(market)
    session = decision_session(enum, as_of)
    threshold = session_close(enum, previous_trading_day(enum, session) if next_session else session)
    out: list[Staleness] = []
    for name in dict.fromkeys(str(a) for a in analysts):
        rows = signals[signals["analyst"].astype(str) == name] if not signals.empty else signals
        if rows.empty:
            out.append(Staleness(name, None, session))
            continue
        latest = pd.Timestamp(rows["valid_from"].max())
        if latest.tzinfo is None:
            latest = latest.tz_localize("UTC")
        if latest.to_pydatetime() < threshold:
            out.append(Staleness(name, local_time(enum, latest.to_pydatetime()).date(), session))
    return out


def zero_confidence(signals: pd.DataFrame, analysts: Iterable[str]) -> bool:
    """알파 Analyst 들의 (종목별 최신) 신호가 있고, 그 confidence 가 **전원 0** 인가.

    `combine.combined_scores` 가 ``|weight × confidence| ≤ EPSILON`` 인 행을 버리는 것과 같은 문턱이다 — 그래서 참이면
    합성 점수는 반드시 0건이고, 이 검사는 결과(후보 0)를 바꾸지 않고 **이름만 붙인다.**
    신호가 아예 없으면 거짓이다 — 그건 이 함수가 아니라 신선도(`stale`)나 배선의 일이다.
    """
    from quant_rl_trading.selector.combine import EPSILON

    names = {str(a) for a in analysts}
    if signals.empty or not names:
        return False
    rows = signals[signals["analyst"].astype(str).isin(names)]
    if rows.empty:
        return False
    if "observed_at" in rows.columns:
        rows = rows.sort_values("observed_at").groupby(["entity_id", "analyst"], as_index=False).tail(1)
    return bool(float(rows["confidence"].astype(float).abs().max()) <= EPSILON)
