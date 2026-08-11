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

from lattice.collectors.market_hours import Market
from lattice.replay.clock import Clock
from lattice.schemas.signal import Evidence, Signal, score_from_z
from lattice.store import Store

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


def combine(frame: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    """피처 z 들을 가중 합해 하나의 z 로.

    가중치를 손으로 정하는 것은 M2 까지다. M3 의 Selector 가 진화 알고리즘으로
    Analyst 간 가중치를 찾고, Analyst 내부 가중치는 모델이 학습한다.
    """
    total = sum(abs(value) for value in weights.values()) or 1.0
    combined = sum(frame[name] * weight for name, weight in weights.items())
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

    def price_panel(self, as_of: datetime, *, lookback: int) -> pd.DataFrame:
        """종목×세션 종가 패널. 게이트 경유 (불변식 1).

        거래 가능한 종목만 남긴다 — 데이터 유니버스와 매매 유니버스는 다르다.
        """
        prices = self.store.get(PRICES, as_of=as_of, lookback=lookback)
        if prices.empty:
            return prices
        prices = prices[prices["market"] == str(self.market)].copy()
        prices["session"] = prices["valid_from"].dt.date

        tradable = self.tradable_entities(as_of, lookback=lookback)
        if tradable is not None:
            prices = prices[prices["entity_id"].isin(tradable)]
        return prices

    def tradable_entities(self, as_of: datetime, *, lookback: int) -> set[str] | None:
        """as_of 시점에 상장·거래 가능한 종목.

        **오늘 명단이 아니라 그 시점 명단이다.** 오늘 명단으로 과거를 필터하면
        상장폐지 종목이 통째로 빠지고, 애써 백필한 생존편향 제거가 무의미해진다.
        """
        universe = self.store.get(UNIVERSE, as_of=as_of, lookback=lookback)
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
