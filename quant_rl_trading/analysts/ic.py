"""IC 측정 — M2 의 진짜 산출물.

Analyst 보다 이게 먼저다. 이 단계의 실패 방식은 하나뿐이고 매우 흔하다 —
**누수된 IC 를 진짜 알파로 착각하는 것.** 백테스트 IC 가 0.15 처럼 나오면
재능이 아니라 버그다.

누수는 두 곳에서 들어온다.

1. **피처가 미래를 본다.** 이건 게이트가 막는다 — 피처는 전부
   ``store.get(as_of=...)`` 를 경유하므로 as_of 이후 관측은 애초에 안 보인다.
2. **검증 분할이 미래를 본다.** 이건 게이트가 못 막는다. 타깃이 5일짜리인데
   폴드를 그냥 자르면, 학습 표본의 타깃 구간과 검증 표본의 피처 구간이 겹친다.
   모델은 정답을 조금 본 채로 시험을 친다.

2번을 막는 것이 **purged K-fold + embargo** 다. 폴드 경계에서 타깃 구간이
겹치는 학습 표본을 잘라내고(purge), 그 앞뒤로 여유를 더 둔다(embargo).

> 이 검증기가 실제로 누수를 잡는지 증명하지 못하면, 그 검증기는 쓸모없다.
> ``tests/analysts/test_ic_leakage.py`` 가 그 증명이다.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from quant_rl_trading.store import Store

PRICES = "prices"

#: 타깃 기간. agents.md §10 — 5일로 통일한다.
HORIZON_DAYS = 5

#: 폴드 사이에 비우는 거래일 수. 타깃 기간보다 넉넉해야 한다.
EMBARGO_DAYS = 5


@dataclass(frozen=True)
class ICResult:
    """한 Analyst·한 시장의 성적표."""

    analyst: str
    analyst_version: str
    market: str
    ic: float
    ic_std: float
    sample_days: int
    sample_rows: int
    threshold: float
    min_sample_days: int
    fold_ics: tuple[float, ...] = ()

    @property
    def passed(self) -> bool:
        """합격 판정. 표본이 모자라면 IC 가 높아도 통과가 아니다.

        표본 200일 미만에서 나온 IC 0.05 는 우연과 구분되지 않는다. 여기서
        관대해지면 검증되지 않은 Analyst 가 실제 자본을 움직이게 된다.
        """
        return self.ic >= self.threshold and self.sample_days >= self.min_sample_days

    @property
    def weight(self) -> float:
        """통과 못 하면 0. 관찰 모드다 — 점수는 계속 기록하되 매매에는 안 쓴다."""
        return 1.0 if self.passed else 0.0

    def row(self, *, as_of: datetime, observed_at: datetime, source: str) -> dict[str, Any]:
        return {
            "entity_id": self.analyst,
            "valid_from": as_of,
            "observed_at": observed_at,
            "source": source,
            "analyst_version": self.analyst_version,
            "weight": self.weight,
            "ic": self.ic,
            "ic_threshold": self.threshold,
            "sample_days": self.sample_days,
            "passed": self.passed,
            "market": self.market,
        }


# -----------------------------------------------------------------------------
# 타깃
# -----------------------------------------------------------------------------


#: 신호와 진입 사이의 거래일 수.
#:
#: 신호는 **마감 후** 공표 시각에 나온다 (PublicationPolicy). 그런데 타깃을
#: 그날 종가부터 재면, 이미 지나간 종가에 체결한 셈이 되어 하루치 공짜 미래를
#: 본다. IC 를 조용히 부풀리는 대표적 경로다.
#:
#: 그래서 진입은 **다음 거래일 종가**다. 보수적이지만 거짓이 아니다.
ENTRY_LAG_DAYS = 1


def forward_returns(
    prices: pd.DataFrame,
    *,
    horizon: int = HORIZON_DAYS,
    entry_lag: int = ENTRY_LAG_DAYS,
) -> pd.DataFrame:
    """종목별 수익률. 신호일 기준 ``entry_lag`` 뒤에 들어가 ``horizon`` 만큼 보유.

    거래일 축으로 민다. 달력일로 밀면 연휴에서 기간이 들쭉날쭉해지고, 그
    들쭉날쭉함이 레짐과 상관돼 IC 를 왜곡한다.
    """
    frame = prices.loc[:, ["entity_id", "session", "close"]].dropna(subset=["close"])
    frame = frame.sort_values(["entity_id", "session"])
    grouped = frame.groupby("entity_id")["close"]
    frame["entry_close"] = grouped.shift(-entry_lag)
    frame["forward_close"] = grouped.shift(-(entry_lag + horizon))
    frame["forward_return"] = frame["forward_close"] / frame["entry_close"] - 1.0
    return frame.dropna(subset=["forward_return"])


def cross_sectional_z(frame: pd.DataFrame, column: str, *, by: str = "session") -> pd.Series:
    """같은 날 종목들 사이의 z-score.

    타깃이 **횡단면 초과수익**이어야 하는 이유는, 시장이 통째로 오른 날의
    수익을 예측력으로 세지 않기 위해서다. 그걸 안 빼면 모든 Analyst 가 그냥
    베타를 학습하고 IC 가 좋아 보인다.
    """
    grouped = frame.groupby(by)[column]
    centered = frame[column] - grouped.transform("mean")
    spread = grouped.transform("std")
    return (centered / spread.replace(0.0, np.nan)).astype(float)


def build_targets(
    store: Store,
    *,
    as_of: datetime,
    lookback: int,
    horizon: int = HORIZON_DAYS,
    window: int = 32,
    market: str | None = None,
) -> pd.DataFrame:
    """라벨 생성. 창고를 게이트로만 읽는다 (불변식 1).

    ``as_of`` 는 **측정 시점**이다. 이 시점에서 뒤를 돌아보며 라벨을 만드는
    것이므로, 라벨 자체는 미래를 봐도 된다 — 그게 라벨의 정의다. 누수는
    라벨이 아니라 **피처**가 미래를 볼 때 생긴다.
    """
    from quant_rl_trading.dashboard.services import data_quality as dq

    # 라벨에 필요한 것은 종가뿐이다. 6년치를 전 컬럼으로 이어 붙이면 안 쓰는
    # 문자열·부동소수 열이 수백 MB를 차지하고, 그 봉우리에서 머신이 멈춘다.
    windows = dq.iter_windows(
        store,
        PRICES,
        as_of=as_of,
        lookback=lookback,
        window=window,
        columns=["close"],
        # 라벨도 한 시장 것만 만든다. 다른 시장 종목이 섞이면 횡단면 z 가
        # 두 시장을 한 줄에 세우게 되고, 그건 비교가 아니다.
        market=market,
    )
    frames = [
        chunk.loc[:, ["entity_id", "valid_from", "close"]]
        for chunk in windows
        if not chunk.empty
    ]
    if not frames:
        return pd.DataFrame(columns=["entity_id", "session", "target"])

    prices = pd.concat(frames, ignore_index=True)
    # 창이 서로 겹치게 잘려 있어서 중복이 생긴다 (_span 이 하루 넉넉히 잡는다).
    frames.clear()
    prices = prices.drop_duplicates(subset=["entity_id", "valid_from"], keep="last")
    prices["session"] = prices["valid_from"].dt.date

    forward = forward_returns(prices, horizon=horizon)
    forward["target"] = cross_sectional_z(forward, "forward_return")
    return forward.dropna(subset=["target"]).loc[:, ["entity_id", "session", "target"]]


# -----------------------------------------------------------------------------
# purged K-fold + embargo
# -----------------------------------------------------------------------------


def purged_folds(
    sessions: Sequence[date],
    *,
    n_splits: int = 5,
    horizon: int = HORIZON_DAYS,
    embargo: int = EMBARGO_DAYS,
) -> Iterator[tuple[list[date], list[date]]]:
    """(학습 세션, 검증 세션) 쌍을 낸다.

    검증 구간 주변에서 **타깃 구간이 겹치는 학습 세션을 잘라낸다**. 검증
    구간이 [a, b] 라면, 학습에서 빼야 하는 것은 [a - horizon - embargo,
    b + embargo] 다. 앞쪽을 horizon 만큼 더 빼는 이유는, 그 세션의 타깃이
    검증 구간까지 뻗어 있기 때문이다.

    embargo 를 0 으로 두고 실행할 수 있게 인자로 뺐다. 그건 운영용이 아니라
    **검증기가 실제로 작동하는지 확인하기 위한 것**이다 — 끄면 IC 가 올라가야
    한다. 안 올라가면 purge 가 아무 일도 안 하고 있는 것이다.
    """
    ordered = sorted(set(sessions))
    if n_splits < 2 or len(ordered) < n_splits:
        raise ValueError(f"세션 {len(ordered)}개로는 {n_splits}겹 분할을 할 수 없다")

    bounds = np.linspace(0, len(ordered), n_splits + 1).astype(int)
    for index in range(n_splits):
        start, stop = bounds[index], bounds[index + 1]
        if start >= stop:
            continue
        test = ordered[start:stop]
        low = test[0] - timedelta(days=horizon + embargo)
        high = test[-1] + timedelta(days=embargo)
        train = [day for day in ordered if day < low or day > high]
        if train and test:
            yield train, test


# -----------------------------------------------------------------------------
# IC
# -----------------------------------------------------------------------------


def daily_ic(merged: pd.DataFrame, *, score: str = "score", target: str = "target") -> pd.Series:
    """일별 횡단면 순위상관(Spearman).

    피어슨이 아니라 순위상관을 쓴다. 수익률 분포에는 꼬리가 두껍고, 하루짜리
    이상치 하나가 피어슨 IC 를 통째로 흔든다.
    """
    def one(group: pd.DataFrame) -> float:
        if len(group) < 3:
            return float("nan")
        return float(group[score].corr(group[target], method="spearman"))

    return merged.groupby("session")[[score, target]].apply(one).dropna()


def evaluate(
    scores: pd.DataFrame,
    targets: pd.DataFrame,
    *,
    analyst: str,
    analyst_version: str,
    market: str,
    threshold: float,
    min_sample_days: int,
    n_splits: int = 5,
    horizon: int = HORIZON_DAYS,
    embargo: int = EMBARGO_DAYS,
) -> ICResult:
    """OOS IC. 폴드마다 검증 구간에서만 재고 평균한다.

    ``scores`` 는 이미 만들어진 예측이다 (``entity_id``, ``session``, ``score``).
    학습은 호출자가 폴드별로 한다 — 이 함수는 채점만 한다.
    """
    merged = scores.merge(targets, on=["entity_id", "session"], how="inner")
    if merged.empty:
        return ICResult(
            analyst=analyst, analyst_version=analyst_version, market=market,
            ic=float("nan"), ic_std=float("nan"), sample_days=0, sample_rows=0,
            threshold=threshold, min_sample_days=min_sample_days,
        )

    series = daily_ic(merged)
    fold_ics: list[float] = []
    sessions = sorted(merged["session"].unique())
    if len(sessions) >= n_splits:
        for _, test in purged_folds(
            sessions, n_splits=n_splits, horizon=horizon, embargo=embargo
        ):
            fold = series[series.index.isin(test)]
            if not fold.empty:
                fold_ics.append(float(fold.mean()))

    return ICResult(
        analyst=analyst,
        analyst_version=analyst_version,
        market=market,
        ic=float(np.mean(fold_ics)) if fold_ics else float(series.mean()),
        ic_std=float(series.std()),
        sample_days=int(series.size),
        sample_rows=len(merged),
        threshold=threshold,
        min_sample_days=min_sample_days,
        fold_ics=tuple(fold_ics),
    )


#: 잴 표본이 없을 때의 confidence. **감쇠는 증거가 있을 때만 한다.**
#:
#: 예전에는 0 이었다. 그런데 합성이 ``Σ(w·score·conf) / Σ(w·conf)`` 라
#: confidence 가 0 이면 그 Analyst 는 분모에서 통째로 빠지고, 전원이 0 이면
#: **후보가 0건이 된다.** 실제로 실전 창고에서 그 일이 일어났다 — 신호는
#: 13,746행이 쌓였는데 Selector 가 아무것도 고르지 못했고, 화면에는 "오늘 살
#: 게 없다" 와 똑같이 보였다.
#:
#: IC 를 통과해 가중치를 받은 것이 이미 관문이다. 롤링 IC 는 그 위에서
#: **최근 성적으로 가감**하는 값이고, 잴 표본이 없다는 것은 감쇠할 근거가
#: 없다는 뜻이지 신뢰가 0 이라는 뜻이 아니다.
NO_EVIDENCE_CONFIDENCE = 1.0


def rolling_confidence(
    store: Store,
    *,
    analyst: str,
    as_of: datetime,
    market: str,
    window: int = 60,
) -> float:
    """최근 ``window`` 거래일 롤링 IC 를 confidence 로.

    **에이전트가 스스로 매기지 않는다** (agents.md §1). 스스로 매기면 과신한다.
    호출자가 이 값을 넣어 준다.

    이미 저장된 ``signals`` 와 그 시점 타깃을 맞춰 IC 를 다시 잰다.

    **잴 표본이 없으면 ``NO_EVIDENCE_CONFIDENCE`` 다** — 0 이 아니다. 0 으로
    두면 신호가 쌓이기 전 구간에서 시스템이 아무것도 사지 못하고, 그 침묵이
    "오늘 살 게 없다" 와 구분되지 않는다.

    잴 수 있는데 IC 가 0 이하면 그때는 0 이다. 그건 **증거가 있는 배제**다.
    음수 IC 를 뒤집어 쓰지 않는 이유는 표본에 사후로 맞추는 것이기 때문이다.
    """
    frame = store.get(
        "signals", as_of=as_of, lookback=int(window * 7 / 5) + 30, market=None
    )
    if frame.empty:
        return NO_EVIDENCE_CONFIDENCE

    scores = frame[frame["analyst"] == analyst]
    if scores.empty:
        return NO_EVIDENCE_CONFIDENCE

    scores = pd.DataFrame(
        {
            "entity_id": scores["entity_id"].astype(str),
            "session": scores["valid_from"].dt.date,
            "score": scores["score"].astype(float),
        }
    )
    sessions = sorted(scores["session"].unique())[-window:]
    if len(sessions) < 2:
        return NO_EVIDENCE_CONFIDENCE
    scores = scores[scores["session"].isin(set(sessions))]

    targets = build_targets(
        store, as_of=as_of, lookback=int(window * 7 / 5) + 90, market=market
    )
    if targets.empty:
        return NO_EVIDENCE_CONFIDENCE

    merged = scores.merge(targets, on=["entity_id", "session"], how="inner")
    if merged.empty:
        # 신호는 있는데 라벨이 아직 없다(전방 5일이 안 지났다). 못 잰 것이다.
        return NO_EVIDENCE_CONFIDENCE

    daily = daily_ic(merged)
    if daily.empty:
        return NO_EVIDENCE_CONFIDENCE
    # 여기부터는 **잰 값**이다. 0 이면 증거가 있는 배제다.
    return max(0.0, float(daily.mean()))


def thresholds(store: Store, *, as_of: datetime) -> tuple[float, int]:
    """합격선과 표본 하한. 하드코딩 금지 (불변식 10)."""
    return (
        float(store.config("analyst.ic_threshold", as_of=as_of)),
        int(store.config("analyst.ic_min_samples", as_of=as_of)),
    )


__all__ = [
    "EMBARGO_DAYS",
    "NO_EVIDENCE_CONFIDENCE",
    "HORIZON_DAYS",
    "ICResult",
    "build_targets",
    "cross_sectional_z",
    "daily_ic",
    "evaluate",
    "forward_returns",
    "purged_folds",
    "thresholds",
]
