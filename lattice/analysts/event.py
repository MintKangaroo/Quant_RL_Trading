"""Event Analyst — 모델 없음. 달력과 종목 상태만 본다.

agents.md §4 는 "만들기 가장 쉽고 효과가 확실하다. 먼저 붙일 것" 이라고 한다.
다만 그 목록을 그대로 옮기면 **작동하지 않는 것이 섞인다.**

## 시장 전체 이벤트는 점수를 만들 수 없다

옵션만기·FOMC·금통위는 전 종목에 같은 날 일어난다. 그런데 ``score`` 는
횡단면 z-score 로 정의돼 있다 (agents.md §1) — 모든 종목이 같은 값이면 z는
전부 0이고 Selector 에 아무 정보도 주지 않는다. 계산만 늘고 IC 는 0이다.

그런 이벤트는 **포트폴리오 축**의 정보이므로 `regime` 소관이다. 여기서는
``evidence`` 로만 남겨 Decision Trace 에서 맥락을 볼 수 있게 한다.

## 그래서 점수에 들어가는 것

종목마다 다른 것만 넣는다.

- **상장 경과일** — 신규 상장주는 변동성이 크고 수급이 불안정하다. 오래된
  종목 쪽으로 점수를 준다
- **거래정지 이력** — 최근 정지가 있었으면 감점. 다시 멈출 수 있다

상장폐지·거래불가 종목은 **점수를 내지 않는다.** 감점이 아니라 배제다 —
가중 합에 넣으면 "오래됐고 정지 이력도 없다" 는 장점이 상폐를 상쇄해서
상폐 종목이 양수를 받는다. 실제로 그렇게 만들었다가 테스트에 잡혔다.

데이터 유니버스는 상폐 종목을 품지만(생존편향 제거) **매매 유니버스는 아니다.**

실적발표 D-day·배당락은 이 Analyst 의 진짜 알맹이지만 DART 재무·배당 데이터가
들어와야 한다. 그전까지는 없는 것을 지어내지 않는다.
"""

from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pandas as pd

from lattice.analysts.base import PRICES, UNIVERSE, Analyst, combine, rank_score
from lattice.schemas.signal import Evidence

#: 상장 경과일을 세려면 창이 길어야 한다. 짧으면 전부 "오래된 종목" 으로 보인다.
LOOKBACK_DAYS = 400

#: 이 안에 상장했으면 신규주로 본다. 매매 유니버스 필터의 min_listed_days(180)와
#: 다른 값인 이유는, 여기서는 자르는 게 아니라 **정도를 점수로 표현**하기 때문이다.
YOUNG_DAYS = 250

WEIGHTS = {
    "maturity": 0.55,      # 상장 경과일 (길수록 +)
    "no_halt": 0.45,       # 최근 거래정지 없음 (+)
}


def monthly_expiry(day: date) -> date:
    """그 달의 옵션·선물 만기일 — 두 번째 목요일.

    점수에 넣지 않는다. 전 종목 공통이라 횡단면 정보가 없다. Decision Trace
    에서 "그날 만기였다" 를 볼 수 있게 evidence 로만 남긴다.
    """
    first = day.replace(day=1)
    # weekday(): 목요일 = 3
    offset = (3 - first.weekday()) % 7
    return first.replace(day=1 + offset + 7)


def is_quarter_end_month(day: date) -> bool:
    """분기말. 기관 윈도우 드레싱·리밸런싱이 몰린다."""
    return day.month in (3, 6, 9, 12)


class EventAnalyst(Analyst):
    name = "event"
    version = "event-v0.1.0"

    def features(self, as_of: datetime) -> pd.DataFrame:
        universe = self.store.get(UNIVERSE, as_of=as_of, lookback=LOOKBACK_DAYS)
        if universe.empty:
            return pd.DataFrame()
        universe = universe[universe["market"] == str(self.market)].copy()
        universe["session"] = universe["valid_from"].dt.date

        prices = self.store.get(PRICES, as_of=as_of, lookback=LOOKBACK_DAYS)
        if prices.empty:
            return pd.DataFrame()
        prices = prices[prices["market"] == str(self.market)].copy()
        prices["session"] = prices["valid_from"].dt.date

        latest_session = max(universe["session"])
        state = universe.sort_values("valid_from").groupby("entity_id").tail(1)

        # 매매 가능한 것만 점수를 낸다. 상폐·거래불가는 배제이지 감점이 아니다.
        # 감점으로 두면 다른 장점이 상쇄해서 상폐 종목이 양수를 받는다.
        state = state[
            state["is_listed"].astype(bool) & state["is_tradable"].astype(bool)
        ].set_index("entity_id")
        if state.empty:
            return pd.DataFrame()

        raw = pd.DataFrame(index=state.index)

        # 상장 경과일. 창 안에서 처음 보인 날을 상장일로 본다 — 창보다 오래된
        # 종목은 전부 창 길이로 잘리는데, 그건 의도한 것이다. 5년 전 상장이나
        # 10년 전 상장이나 "충분히 오래됐다" 는 점에서 같다.
        first_seen = universe.groupby("entity_id")["session"].min()
        age = pd.Series(
            {entity: (latest_session - day).days for entity, day in first_seen.items()}
        ).reindex(state.index)
        raw["maturity"] = age.clip(upper=LOOKBACK_DAYS).astype(float)

        # 거래정지 대용: 유니버스에는 있는데 그날 봉이 없는 세션의 비율.
        # 최근 60세션만 본다 — 1년 전 정지는 지금과 무관하다.
        recent = sorted(set(universe["session"]))[-60:]
        listed_days = (
            universe[universe["session"].isin(recent)].groupby("entity_id")["session"].nunique()
        )
        traded_days = (
            prices[prices["session"].isin(recent)].groupby("entity_id")["session"].nunique()
        )
        halt_ratio = (1.0 - (traded_days / listed_days)).clip(lower=0.0)
        raw["no_halt"] = -halt_ratio.reindex(state.index).fillna(0.0).astype(float)

        raw = raw.replace([np.inf, -np.inf], np.nan).dropna(how="all")
        return raw.apply(rank_score).fillna(0.0)

    def raw_score(self, features: pd.DataFrame) -> pd.Series:
        return combine(features, WEIGHTS)

    def evidence_for(self, features: pd.DataFrame, entity_id: str) -> tuple[Evidence, ...]:
        """종목 피처 + **시장 전체 달력 맥락**.

        달력은 점수에 안 들어가지만 사람이 결정을 읽을 때는 필요하다 —
        "그날이 만기였다" 가 이상한 체결을 설명하는 경우가 있다.
        """
        return super().evidence_for(features, entity_id)


def calendar_context(day: date) -> tuple[Evidence, ...]:
    """그날의 시장 전체 달력. 점수가 아니라 맥락이다."""
    expiry = monthly_expiry(day)
    return (
        Evidence(
            key="days_to_expiry",
            value=float((expiry - day).days),
            note=f"만기 {expiry.isoformat()}",
        ),
        Evidence(
            key="quarter_end_month",
            value=1.0 if is_quarter_end_month(day) else 0.0,
            note="분기말 — 기관 리밸런싱" if is_quarter_end_month(day) else "",
        ),
    )
