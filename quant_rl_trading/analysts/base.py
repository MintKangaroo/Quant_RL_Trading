"""Analyst 공통 뼈대.

세 가지를 강제한다.

1. **직접 수집하지 않는다.** 입력은 ``store`` 와 ``as_of`` 뿐이다 (CLAUDE.md 금지
   사항). Analyst 가 스스로 API 를 때리면 그 데이터는 게이트를 안 거치고,
   게이트를 안 거친 데이터는 반드시 언젠가 미래를 본다.
2. **모든 피처는 같은 시장 내 횡단면 z-score.** 국장과 미장을 섞지 않는다.
   절대값으로 학습하면 시장이 통째로 오른 날 전원이 만장일치로 매수를 외친다.
3. **score 는 ``score_from_z`` 로만 만든다.** 정의가 Analyst 마다 다르면
   Selector 가 점수를 합칠 수 없다.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from datetime import datetime

import numpy as np
import pandas as pd

from quant_rl_trading.collectors.market_hours import Market
from quant_rl_trading.replay.clock import Clock
from quant_rl_trading.schemas.signal import Evidence, Signal, score_from_z
from quant_rl_trading.store import Store

PRICES = "prices"
UNIVERSE = "universe"


def features_hash(frame: pd.DataFrame) -> str:
    """피처 프레임의 지문. 같은 입력이면 같은 값이어야 한다 (agent_cache 키)."""
    payload = pd.util.hash_pandas_object(frame, index=True).values.tobytes()
    return hashlib.sha256(payload).hexdigest()[:16]


def zscore(series: pd.Series) -> pd.Series:
    """횡단면 z. 표준편차가 0이면 전원 동일하다는 뜻이므로 0으로 둔다."""
    spread = float(series.std())
    if not np.isfinite(spread) or spread == 0.0:
        return pd.Series(0.0, index=series.index)
    return ((series - float(series.mean())) / spread).clip(-5.0, 5.0)


def rank_score(series: pd.Series) -> pd.Series:
    """횡단면 **순위** 정규화. z 대신 이걸 쓴다.

    z-score 의 분모는 이상치에 부풀려진다. 주가 모멘텀처럼 꼬리가 두꺼운
    분포에서는 대부분의 종목이 0 근처로 눌리고 극단값만 클립에 걸려,
    **피처마다 실제 분산이 달라진다.** 실측(2026-08-12, 2,787종목):

        momentum_20  std 0.523      reversal_5   std 0.403
        range_position std 0.987    volume_surge std 0.864

    분산이 다른 z 를 가중합하면 **선언한 가중치가 실효 가중치가 아니다** —
    momentum_20 은 30%로 선언했지만 실효 22%, reversal_5 는 20% 선언에 11.5%
    였다. 순위로 바꾸면 모든 피처가 같은 분산을 갖고, 가중치가 뜻대로 먹는다.

    그리고 **IC 를 순위상관(Spearman)으로 재기 때문에** 피처도 순위 공간에
    두는 것이 평가 지표와 일치한다.

    출력은 대략 [-1.73, 1.73] 범위의 균등분포다(표준화된 균등). 정규분위수로
    바꾸지 않는 이유는, 그러면 다시 꼬리에 큰 값이 생겨 가중합이 소수 종목에
    끌려가기 때문이다.
    """
    values = series.dropna()
    if values.empty or values.nunique() < 2:
        return pd.Series(0.0, index=series.index)
    # 동점은 평균 순위. 안 그러면 같은 값이 다른 점수를 받는다.
    ranked = values.rank(method="average", pct=True)
    # [0,1] → 평균 0, 표준편차 1 로. 균등분포의 std 는 1/sqrt(12) 이다.
    normalized = (ranked - 0.5) * (12.0**0.5)
    return normalized.reindex(series.index)


def combine(frame: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    """피처들을 가중 합해 하나의 점수로.

    가중치를 손으로 정하는 것은 M2 까지다. M3 의 Selector 가 진화 알고리즘으로
    Analyst 간 가중치를 찾고, Analyst 내부 가중치는 모델이 학습한다.

    **분산이 0인 피처는 빼고 정규화한다.** 전 종목이 같은 값인 열은 점수에
    아무것도 더하지 않으면서 분모의 자기 몫은 그대로 가져간다 — 즉 **나머지
    피처의 영향력을 조용히 깎는다.** event 의 ``no_halt`` 가 정확히 그랬다:
    실측에서 한 번도 발화하지 않는 상수였는데 가중치의 45% 를 들고 있었고,
    그동안 event 점수는 사실상 나머지 한 피처 혼자 낸 것이었다. 측정에서는
    "IC 가 좀 낮네" 로만 보여서 몇 달을 못 알아봤다.

    합성 데이터로는 안 잡힌다. 테스트에서는 그 피처가 값을 갖도록 만들기
    때문이다. 그래서 코드가 스스로 막게 한다.
    """
    live = {
        name: weight
        for name, weight in weights.items()
        if name in frame.columns and float(frame[name].std() or 0.0) > 0.0
    }
    if not live:
        return pd.Series(0.0, index=frame.index)
    total = sum(abs(value) for value in live.values()) or 1.0
    combined = sum(frame[name] * weight for name, weight in live.items())
    return pd.Series(combined / total, index=frame.index)


class Analyst(ABC):
    """점수를 내는 것. 수집하지 않고, 주문하지 않는다."""

    name: str
    version: str

    def __init__(self, store: Store, clock: Clock, *, market: Market = Market.KR) -> None:
        self.store = store
        self.clock = clock
        self.market = market

    # -- 하위 클래스가 채우는 것 ---------------------------------------------

    @abstractmethod
    def features(self, as_of: datetime) -> pd.DataFrame:
        """``entity_id`` 를 인덱스로 하는 피처 프레임. 전부 store 경유."""

    @abstractmethod
    def raw_score(self, features: pd.DataFrame) -> pd.Series:
        """피처 → 원점수. 횡단면 z 로 돌려준다 (tanh 은 base 가 씌운다)."""

    def evidence_for(self, features: pd.DataFrame, entity_id: str) -> tuple[Evidence, ...]:
        """왜 이 점수인지. Decision Trace 화면이 읽는다."""
        row = features.loc[entity_id]
        top = row.abs().sort_values(ascending=False).head(3)
        return tuple(
            Evidence(key=str(key), value=float(row[key])) for key in top.index
        )

    # -- 공통 실행 -------------------------------------------------------------

    def run(self, as_of: datetime, *, confidence: float = 0.0) -> list[Signal]:
        """as_of 시점의 신호. 빈 관측이면 빈 목록을 낸다 — 지어내지 않는다.

        ``confidence`` 를 인자로 받는 이유는 **에이전트가 스스로 매기지 않기**
        때문이다 (agents.md §1). 최근 60일 롤링 IC 로 계산한 값을 호출자가
        넣는다. 스스로 매기게 하면 과신한다.
        """
        started = self.clock.now()
        frame = self.features(as_of)
        if frame.empty:
            return []

        scores = self.raw_score(frame)
        digest = features_hash(frame)
        elapsed = (self.clock.now() - started).total_seconds() * 1000.0

        return [
            Signal(
                analyst=self.name,
                analyst_version=self.version,
                entity_id=str(entity_id),
                as_of=as_of,
                score=score_from_z(float(value)),
                confidence=confidence,
                features_hash=digest,
                evidence=self.evidence_for(frame, str(entity_id)),
                latency_ms=elapsed,
            )
            for entity_id, value in scores.sort_index().items()
            if np.isfinite(value)
        ]

    # -- 관측 -------------------------------------------------------------------

    #: 패널에 실을 관측 컬럼. 여기 없는 것을 ``wide`` 로 꺼내면 KeyError 가
    #: 난다 — 하위 클래스가 목록을 늘려서 쓴다. 기본값을 좁게 두는 이유는
    #: 메모리다. 5년 창고에서 전 컬럼을 끌면 안 쓰는 문자열이 대부분이다.
    price_columns: tuple[str, ...] = ("market", "close", "volume", "value")

    def price_panel(self, as_of: datetime, *, lookback: int) -> pd.DataFrame:
        """종목×세션 종가 패널. 게이트 경유 (불변식 1).

        거래 가능한 종목만 남긴다 — 데이터 유니버스와 매매 유니버스는 다르다.

        **종가 0 은 가격이 아니라 결측이다.** 창고에는 전 종목 종가가 0 인
        세션이 섞여 있다(수집 실패로 보인다). 그대로 두면 ``pct_change`` 가
        그 자리에서 ``±inf`` 를 내고, inf 가 하나만 있어도 그 종목의 표준편차·
        상관·베타가 통째로 NaN 이 된다. 종목 하나가 아니라 **그날을 지나는 모든
        종목**이 같이 죽으므로, 피처가 조용히 비고 Analyst 는 아무 말도 하지
        않게 된다. 0 을 여기서 NaN 으로 바꾸면 창 계산이 그 세션만 건너뛴다.
        """
        prices = self.store.get(
            PRICES,
            as_of=as_of,
            lookback=lookback,
            columns=list(self.price_columns),
            # 시장을 SQL 에서 거른다. pandas 로 거르면 미장 행을 통째로 퍼온
            # 뒤 버리게 되고, 창고에 두 시장이 같이 사는 순간 국장 질의가
            # 그것만으로 죽는다.
            market=str(self.market),
        )
        if prices.empty:
            return prices
        prices = prices.copy()
        prices["session"] = prices["valid_from"].dt.date
        if "close" in prices.columns:
            prices.loc[prices["close"] <= 0.0, "close"] = float("nan")

        tradable = self.tradable_entities(as_of, lookback=lookback)
        if tradable is not None:
            prices = prices[prices["entity_id"].isin(tradable)]
        return prices

    def tradable_entities(self, as_of: datetime, *, lookback: int) -> set[str] | None:
        """as_of 시점에 상장·거래 가능한 종목.

        **오늘 명단이 아니라 그 시점 명단이다.** 오늘 명단으로 과거를 필터하면
        상장폐지 종목이 통째로 빠지고, 애써 백필한 생존편향 제거가 무의미해진다.
        """
        universe = self.store.get(
            UNIVERSE,
            as_of=as_of,
            lookback=lookback,
            columns=["is_listed", "is_tradable"],
        )
        if universe.empty:
            return None
        latest = universe.sort_values("valid_from").groupby("entity_id").tail(1)
        alive = latest[
            latest["is_listed"].astype(bool) & latest["is_tradable"].astype(bool)
        ]
        return set(alive["entity_id"].astype(str))

    @staticmethod
    def wide(prices: pd.DataFrame, column: str = "close") -> pd.DataFrame:
        """세션×종목 행렬. 결측은 채우지 않는다 — backward-fill 은 미래를 본다."""
        return prices.pivot_table(
            index="session", columns="entity_id", values=column, aggfunc="last"
        ).sort_index()


def to_scores_frame(signals: list[Signal]) -> pd.DataFrame:
    """IC 측정기가 먹는 모양으로. (entity_id, session, score)"""
    if not signals:
        return pd.DataFrame(columns=["entity_id", "session", "score"])
    return pd.DataFrame(
        {
            "entity_id": [signal.entity_id for signal in signals],
            "session": [signal.as_of.date() for signal in signals],
            "score": [signal.score for signal in signals],
        }
    )


__all__: list[str] = [
    "Analyst",
    "Evidence",
    "combine",
    "features_hash",
    "to_scores_frame",
    "zscore",
]
