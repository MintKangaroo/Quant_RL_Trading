"""flow_kr Analyst — 국장 투자자별 수급만 본다.

가격을 보지 않는다. 가격은 chart 소관이고, 여기서 또 보면 두 Analyst 가
같은 것을 두 번 세게 된다. 거래대금은 **분모로만** 쓴다 — 순매수 100억은
삼성전자에서 아무것도 아니고 소형주에서는 사건이라, 규모로 나누지 않으면
피처가 사실상 시가총액 순위가 된다.

## 무엇을 믿는가

- 외인·기관 순매수는 **지속된다**. 하루치보다 20일 누적이 낫다
- 개인 순매수는 반대로 읽는다. 부호를 뒤집어 쓴다
- 금액보다 **꾸준함**이 정보다. 한 방에 산 것과 20일 내내 산 것은 다르다

## 없는 것을 0으로 채우지 않는 문제

``flows`` 는 그날 그 주체가 손대지 않은 종목의 행을 아예 갖지 않는다
(tables.py). 누적 순매수를 구할 때는 **없는 날을 0으로 봐도 된다** — "그날
순매수가 없었다" 와 "0원이었다" 는 누적합에서 같은 값이기 때문이다. 반면
지속성(순매수 양수인 날의 비율)은 다르다. 거래가 없던 날을 분모에 넣으면
거래가 뜸한 종목이 자동으로 낮은 점수를 받는다. 그래서 지속성은 **행이
있는 날만** 분모로 센다.

공매도(``shorting``)는 여기 넣지 않았다. T+1~2 공표라 시점 정합을 맞추려면
백필 쪽 lag 설정에 의존해야 하는데, 그 의존을 IC 측정에 섞으면 수급 자체의
값어치가 안 보인다. 별도 버전에서 붙인다.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from lattice.analysts.base import Analyst, combine, rank_score

FLOWS = "flows"

#: 20일 누적 + 20일 평균 거래대금을 만들려면 이만큼은 있어야 한다.
LOOKBACK_DAYS = 45

#: 누적 창 (거래일).
SHORT_WINDOW = 5
LONG_WINDOW = 20

#: 주체 이름. LS API 원문 그대로다 — 여기서 영문으로 바꾸면 수집기와
#: 이 파일이 따로 놀고, 어느 쪽이 옳은지 사람이 매번 확인해야 한다.
FOREIGN = "외인계"
INSTITUTION = "기관"
RETAIL = "개인"

#: 축별 가중치. 개인은 역지표라 음수다.
WEIGHTS = {
    "foreign_5": 0.15,
    "foreign_20": 0.30,
    "institution_20": 0.20,
    "retail_20": -0.20,
    "foreign_persistence": 0.15,
}

#: 마지막 세션의 관측 종목 수가 그 창의 **중앙값**보다 이 비율 아래로 떨어지면
#: 피처를 만들지 않는다. 수급이 일부 종목에만 들어온 날 횡단면 z 를 재면 그
#: z 는 시장이 아니라 표본을 재는 것이 된다.
#:
#: 기준이 가격 유니버스가 아니라 창의 중앙값인 이유: ``flows`` 는 국장 전
#: 종목을 담지 않는다. 2,800종목 중 991종목 — 투자자별 순매수가 공표되는
#: 종목만 들어온다. 유니버스 대비로 재면 **정상인 날도 전부 미달**이라
#: Analyst 가 영원히 침묵하고, 그건 "수급이 안 먹혔다" 가 아니라 "잰 적이
#: 없다" 다. 잡아야 할 것은 낮은 커버리지가 아니라 **커버리지의 붕괴**다.
MIN_COVERAGE = 0.5


class FlowKrAnalyst(Analyst):
    name = "flow_kr"
    version = "flow_kr-v0.1.0"

    def features(self, as_of: datetime) -> pd.DataFrame:
        prices = self.price_panel(as_of, lookback=LOOKBACK_DAYS)
        if prices.empty:
            return pd.DataFrame()

        # 분모. 종가×거래량 의 20일 평균이 그 종목의 '평소 규모'다.
        close = self.wide(prices, "close")
        volume = self.wide(prices, "volume")
        turnover = (close * volume).tail(LONG_WINDOW).mean()
        turnover = turnover.replace(0.0, np.nan).dropna()
        if turnover.empty:
            return pd.DataFrame()

        flows = self._flow_panel(as_of, universe=set(turnover.index))
        if flows is None:
            return pd.DataFrame()

        # **수급이 관측된 종목만** 의견 대상이다. 관측이 없는 종목까지 index 에
        # 두면 z 가 전부 0 으로 채워져 1,800종목이 한 덩어리 동점이 되고,
        # 순위상관인 IC 는 그 동점 덩어리에 눌려 0 쪽으로 끌려간다. 의견이
        # 없는 것과 중립 의견은 다르다 (flow_us 모듈 docstring 과 같은 이유).
        covered = pd.Index(sorted(set(flows["entity_id"]) & set(turnover.index)))
        turnover = turnover.loc[covered]
        raw = pd.DataFrame(index=covered)
        raw["foreign_5"] = self._cumulative(flows, FOREIGN, SHORT_WINDOW) / turnover
        raw["foreign_20"] = self._cumulative(flows, FOREIGN, LONG_WINDOW) / turnover
        raw["institution_20"] = self._cumulative(flows, INSTITUTION, LONG_WINDOW) / turnover
        raw["retail_20"] = self._cumulative(flows, RETAIL, LONG_WINDOW) / turnover
        raw["foreign_persistence"] = self._persistence(flows, FOREIGN, LONG_WINDOW)

        raw = raw.replace([np.inf, -np.inf], np.nan).dropna(how="all")
        if raw.empty:
            return pd.DataFrame()
        # 결측은 횡단면 순위 중앙(0). 앞뒤로 채우면 미래를 본다.
        return raw.apply(rank_score).fillna(0.0)

    def raw_score(self, features: pd.DataFrame) -> pd.Series:
        return combine(features, WEIGHTS)

    # -- 관측 -------------------------------------------------------------------

    def _flow_panel(self, as_of: datetime, *, universe: set[str]) -> pd.DataFrame | None:
        """(session, entity_id, investor) → net_value. 커버리지 미달이면 None.

        같은 세션·주체에 잠정치와 확정치가 따로 들어오므로(``is_final``)
        확정치를 우선한다. 마지막 행을 그냥 집으면 도착 순서에 따라 잠정치가
        이길 수 있다.
        """
        # 컬럼을 좁힌다. flows 는 하루 3.6MB × 45일이고, 무게의 대부분이
        # 안 쓰는 문자열 컬럼이다.
        frame = self.store.get(
            FLOWS,
            as_of=as_of,
            lookback=LOOKBACK_DAYS,
            columns=["market", "net_value", "is_final", "revision"],
        )
        if frame.empty:
            return None

        frame = frame[
            (frame["market"] == str(self.market)) & frame["entity_id"].isin(universe)
        ]
        if frame.empty:
            return None

        frame = frame.copy()
        frame["session"] = frame["valid_from"].dt.date
        if not self._coverage_intact(frame):
            return None

        # 확정치가 뒤에 오도록 정렬한 뒤 마지막을 남긴다.
        frame = frame.sort_values(["session", "is_final", "revision"])
        return frame.groupby(["session", "entity_id", "investor"], as_index=False).last()

    @staticmethod
    def _coverage_intact(frame: pd.DataFrame) -> bool:
        """마지막 세션의 커버리지가 창의 중앙값 대비 정상인가.

        중앙값을 쓰는 이유는 이상치에 끌려가지 않기 위해서다 — 창 안에 이미
        붕괴한 날이 하나 있어도 기준선이 흔들리면 안 된다.
        """
        per_session = frame.groupby("session")["entity_id"].nunique()
        if len(per_session) < 2:
            return False
        latest = per_session.iloc[-1]
        return bool(latest >= MIN_COVERAGE * per_session.median())

    @staticmethod
    def _cumulative(flows: pd.DataFrame, investor: str, window: int) -> pd.Series:
        """최근 ``window`` 세션의 순매수 합. 행이 없는 날은 0원과 같다."""
        subset = flows[flows["investor"] == investor]
        if subset.empty:
            return pd.Series(dtype=float)
        recent = sorted(subset["session"].unique())[-window:]
        subset = subset[subset["session"].isin(recent)]
        return subset.groupby("entity_id")["net_value"].sum()

    @staticmethod
    def _persistence(flows: pd.DataFrame, investor: str, window: int) -> pd.Series:
        """순매수 양수인 날의 비율 − 0.5. 관측이 있는 날만 분모로 센다."""
        subset = flows[flows["investor"] == investor]
        if subset.empty:
            return pd.Series(dtype=float)
        recent = sorted(subset["session"].unique())[-window:]
        subset = subset[subset["session"].isin(recent)]
        grouped = subset.groupby("entity_id")["net_value"]
        return (grouped.apply(lambda values: float((values > 0).mean())) - 0.5).astype(float)
