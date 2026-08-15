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

## 거래정지 피처(no_halt)는 뺐다 (2026-08-14)

"유니버스에는 있는데 그날 봉이 없다" 를 거래정지 대용으로 썼는데, **한 번도
발화하지 않았다.** 실측에서 고유값 1개·표준편차 0.0000 — 전 종목이 같은 값인
상수였다. KRX 는 정지 종목도 거래량 0 인 봉을 주기 때문에 "봉이 없는 날" 이
생기지 않는다.

상수 열은 IC 가 nan 이고 rank_score 가 전부 0 을 준다. 그런데 **가중치는
그대로 먹는다** — 이 피처는 처음부터 event 가중치의 45% 를 들고 아무 일도
하지 않았다. 피처별 IC 에서 maturity 단독이 +0.0333 으로 그 시절 event 합산
IC 와 정확히 같았던 것이 그 증거다.

약한 신호를 뺀 것이 아니라 **고장 난 것을 뺐다.** 진짜 거래정지 데이터가
들어오면 그때 다시 붙인다.

상장폐지·거래불가 종목은 **점수를 내지 않는다.** 감점이 아니라 배제다 —
가중 합에 넣으면 "오래됐고 정지 이력도 없다" 는 장점이 상폐를 상쇄해서
상폐 종목이 양수를 받는다. 실제로 그렇게 만들었다가 테스트에 잡혔다.

데이터 유니버스는 상폐 종목을 품지만(생존편향 제거) **매매 유니버스는 아니다.**

## 공시 이벤트 (2026-08-13 추가)

DART 공시목록이 들어오면서 **날짜가 박힌 진짜 이벤트**가 생겼다
(``collectors/dart_filings.py``). 그 전까지 이 Analyst 는 maturity·no_halt
둘뿐이라 이름과 달리 "오래되고 안 멈춘 종목" 을 좋아하는 품질 지표였다.

넣는 것은 **사전에 방향을 말할 수 있는 사건**뿐이다.

    buyback   자사주 취득      +   회사가 자기 주식을 사는 것은 신호다
    dividend  배당             +
    contract  수주             +   실적으로 이어지기 전에 먼저 공시된다
    dilution  유상증자·CB      −   기존 주주 지분이 희석된다
    distress  불성실공시·관리   −   가장 강한 음의 신호

**earnings(실적 공시)는 점수에 넣지 않는다.** 실적 발표 자체는 방향이 없다 —
좋았는지 나빴는지는 숫자를 봐야 알고, 그건 fundamental 소관이다. "최근에
발표했다" 만으로 점수를 주면 어느 쪽으로 줘야 할지 말할 수 없다.

방향을 모르는 피처를 넣고 IC 가 정해 주기를 기다리는 것은, 표본에 사후적으로
맞추는 일이다.
"""

from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pandas as pd

from quant_rl_trading.analysts.base import UNIVERSE, Analyst, combine, rank_score
from quant_rl_trading.schemas.signal import Evidence
from quant_rl_trading.store.prices import read_prices

#: 상장 경과일을 세려면 창이 길어야 한다. 짧으면 전부 "오래된 종목" 으로 보인다.
LOOKBACK_DAYS = 400

#: 이 안에 상장했으면 신규주로 본다. 매매 유니버스 필터의 min_listed_days(180)와
#: 다른 값인 이유는, 여기서는 자르는 게 아니라 **정도를 점수로 표현**하기 때문이다.
YOUNG_DAYS = 250

#: 공시를 훑는 창(달력일). 자사주·배당·증자의 효과가 남아 있는 기간이다.
FILING_WINDOW_DAYS = 60

#: 부실 신호만 더 길게 본다. 관리종목 지정은 60일이 지나도 여전히 사실이다.
DISTRESS_WINDOW_DAYS = 120

#: 점수에 들어가는 공시 분류와 부호. **없는 분류는 여기 넣지 않는다** —
#: earnings 는 방향을 말할 수 없어서 뺐다 (모듈 docstring).
FILING_SIGNS = {
    "buyback": +1.0,
    "dividend": +1.0,
    "contract": +1.0,
    "dilution": -1.0,
    "distress": -1.0,
}

#: 가중치는 **사전에** 정한다. 측정 결과를 보고 고르면 그 표본에 맞춘 값이
#: 되고 다음 구간에서 사라진다. 공시 이벤트에 무게를 싣는 이유는 그것이 이
#: Analyst 가 하기로 한 일이기 때문이다 — maturity·no_halt 는 이벤트가 아니라
#: 종목 상태이고, 이벤트가 없던 시절의 대타였다.
#:
#: 부호는 값에 이미 들어가 있다(``FILING_SIGNS``). 여기서는 전부 양수다.
WEIGHTS = {
    "buyback": 0.20,       # 자사주 취득
    "distress": 0.20,      # 불성실공시·관리종목 (값이 음수)
    "dilution": 0.15,      # 유상증자·CB (값이 음수)
    "dividend": 0.10,      # 배당
    "contract": 0.10,      # 수주
    "maturity": 0.15,      # 상장 경과일 (길수록 +)
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

        # ``price_panel`` 을 안 타는 자리다. 그래서 여기만 따로 오염됐다 —
        # 시세를 읽는 곳은 예외 없이 ``read_prices`` 를 거친다.
        prices = read_prices(self.store, as_of=as_of, lookback=LOOKBACK_DAYS)
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

        for name, series in self._filing_features(as_of, state.index).items():
            raw[name] = series

        raw = raw.replace([np.inf, -np.inf], np.nan).dropna(how="all")
        return raw.apply(rank_score).fillna(0.0)

    def _filing_features(
        self, as_of: datetime, entities: pd.Index
    ) -> dict[str, pd.Series]:
        """공시 건수 피처. **없으면 빈 dict 를 돌려준다.**

        공시가 아직 안 들어온 창고에서 전 종목 0 인 열을 만들면, 그 열은 분산이
        0이라 rank_score 가 전부 0 을 주고 ``combine`` 은 그 가중치만큼 나머지
        피처의 영향력을 줄인다. 열이 아예 없으면 ``raw_score`` 가 남은 가중치로
        정규화한다 — fundamental 의 밸류 피처와 같은 처리다.

        건수 그대로 쓴다. 한 종목이 60일 안에 자사주 공시를 세 번 냈으면 한 번
        낸 종목보다 강한 신호다. 상한을 두면 그 차이가 사라진다.
        """
        window = max(FILING_WINDOW_DAYS, DISTRESS_WINDOW_DAYS)
        documents = self.store.get("documents", as_of=as_of, lookback=window)
        if documents.empty:
            return {}

        prefix = f"{self.market}:"
        documents = documents[
            documents["entity_id"].astype(str).str.startswith(prefix)
            & documents["doc_type"].isin(FILING_SIGNS)
        ]
        if documents.empty:
            return {}

        # 창 밖의 행을 다시 자른다. lookback 은 가장 긴 창으로 한 번만 읽고,
        # 분류마다 필요한 창은 여기서 좁힌다 — 질의를 두 번 하지 않는다.
        edge = as_of - pd.Timedelta(days=FILING_WINDOW_DAYS)
        distress_edge = as_of - pd.Timedelta(days=DISTRESS_WINDOW_DAYS)

        out: dict[str, pd.Series] = {}
        for doc_type, sign in FILING_SIGNS.items():
            limit = distress_edge if doc_type == "distress" else edge
            subset = documents[
                (documents["doc_type"] == doc_type) & (documents["valid_from"] >= limit)
            ]
            if subset.empty:
                continue
            counts = subset.groupby("entity_id")["doc_id"].nunique()
            # 공시가 없는 종목은 0 이다. 여기서는 결측이 아니라 **사실**이다 —
            # "그 종목은 그 기간에 그런 공시를 내지 않았다".
            out[doc_type] = (
                sign * counts.reindex(entities).fillna(0.0).astype(float)
            )
        return out

    def raw_score(self, features: pd.DataFrame) -> pd.Series:
        # 공시가 안 들어온 구간에는 그 열이 아예 없다. 없는 피처를 0 으로
        # 채워 넣으면 나머지 피처의 영향력만 줄어든다 — fundamental 의 밸류
        # 피처와 같은 처리다.
        present = {
            name: weight for name, weight in WEIGHTS.items() if name in features.columns
        }
        return combine(features, present)

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
