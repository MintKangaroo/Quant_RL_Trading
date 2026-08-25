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

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from quant_rl_trading.store import Store
from quant_rl_trading.store.prices import PRICES, adjust, drop_dead_sessions

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
    #: 겹치는 타깃을 보정한 t 값(Newey-West, lag = horizon-1). **크기와 함께
    #: 유의성을 남긴다** — 예전에는 ic_std 를 계산해 로그에 찍고 버렸다.
    ic_t: float
    sample_days: int
    sample_rows: int
    threshold: float
    min_sample_days: int
    #: t 하한 (Newey-West). 0 이면 t 게이트를 끈다 — 옛 행 재현용.
    t_min: float = 0.0
    fold_ics: tuple[float, ...] = ()

    @property
    def passed(self) -> bool:
        """합격 판정. 표본이 모자라면 IC 가 높아도 통과가 아니다.

        표본 200일 미만에서 나온 IC 0.05 는 우연과 구분되지 않는다. 여기서
        관대해지면 검증되지 않은 Analyst 가 실제 자본을 움직이게 된다.
        """
        if not (self.ic >= self.threshold and self.sample_days >= self.min_sample_days):
            return False
        if self.t_min <= 0.0:
            return True
        # **크기와 유의성을 둘 다 요구한다** (2026-08-25). 크기 게이트만으로는
        # 변동성이 큰 잡음이 요행으로 넘는다 — chart 급 std(0.13)면 IC 0.03
        # 이어도 t≈1.8 이라 이 게이트가 정확히 잡는다. 실측 근거: 통과 3종의
        # t 는 4.5~7.7, 미통과 최고는 2.04 — 현재 판정은 하나도 안 바뀐다.
        # nan(표본<3)은 유의성을 잴 수 없으므로 통과가 아니다.
        return bool(np.isfinite(self.ic_t) and self.ic_t >= self.t_min)

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
            # **유의성을 같이 남긴다.** 크기만 적으면 나중에 성적표를 봐도
            # 그 IC 가 우연인지 알 수 없다 — 2026-08-23 재점검에서 실제로
            # t 를 내려다 저장값이 없어 못 냈다.
            "ic_std": self.ic_std,
            "ic_t": self.ic_t,
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



def newey_west_t(series: "pd.Series", *, lag: int) -> float:
    """겹치는 타깃을 감안한 t 값. ``lag=0`` 이면 보통 t 와 같다.

    **여기가 이 식의 유일한 자리다.** 예전에는 `tools/diagnose_ic.py` 에만
    있었는데, 합격 판정이 t 를 쓰게 되면서 라이브러리로 올렸다 — 도구와
    판정이 각자 t 를 계산하면 언젠가 서로 다른 답을 낸다.

    **왜 보정하나.** 5일 앞을 보는 타깃은 연속한 날들이 겹친다. 겹친 표본을
    독립으로 세면 유효표본이 부풀고 t 가 과대평가된다 — regime 에서 -2.46 이
    보정 후 -1.93 으로 바뀌어 "유의" 가 "미확인" 이 된 적이 있다.
    """
    values = series.dropna().to_numpy(dtype=float)
    n = values.size
    if n < 3:
        return float("nan")
    mean = float(values.mean())
    dev = values - mean
    gamma0 = float((dev * dev).sum() / n)
    variance = gamma0
    for k in range(1, min(lag, n - 1) + 1):
        gamma = float((dev[k:] * dev[:-k]).sum() / n)
        variance += 2.0 * (1.0 - k / (lag + 1)) * gamma
    if variance <= 0:
        return float("nan")
    return mean / float(np.sqrt(variance / n))


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
        # ``adj_factor`` 를 같이 받는다. **라벨은 보정가로 만들어야 한다** —
        # 액면분할·무상증자가 보정되지 않으면 그 세션의 forward_return 이
        # 배율만큼(1/10 분할이면 -90%) 찍히고, 횡단면 z 가 그 종목을 최하위로
        # 밀어 라벨 자체가 거짓이 된다.
        columns=["close", "adj_factor"],
        # 라벨도 한 시장 것만 만든다. 다른 시장 종목이 섞이면 횡단면 z 가
        # 두 시장을 한 줄에 세우게 되고, 그건 비교가 아니다.
        market=market,
    )
    # **휴장일의 종가 0 을 여기서 뺀다.** ``iter_windows`` 는 테이블을 가리지
    # 않는 일반 도구라 거르지 않는다 — 거르면 0 을 세는 것이 일인
    # ``data_quality`` 가 자기 사실을 못 보게 된다. 그대로 두면
    # ``forward_returns`` 가 ``entry_close=0`` 에서 ``inf`` 를,
    # ``forward_close=0`` 에서 정확히 ``-1.0`` 을 내고, 그 세션의 횡단면 z 가
    # 통째로 NaN 이 된다 — 실측 4세션 11,491행(라벨의 5.3%)이 조용히 사라졌다.
    frames = [
        drop_dead_sessions(chunk).loc[
            :, ["entity_id", "valid_from", "close", "adj_factor"]
        ]
        for chunk in windows
        if not chunk.empty
    ]
    if not frames:
        return pd.DataFrame(columns=["entity_id", "session", "target"])

    prices = pd.concat(frames, ignore_index=True)
    # 창이 서로 겹치게 잘려 있어서 중복이 생긴다 (_span 이 하루 넉넉히 잡는다).
    frames.clear()
    prices = prices.drop_duplicates(subset=["entity_id", "valid_from"], keep="last")
    # **접기는 창을 이어 붙인 뒤에 한다.** 조각마다 접으면 누적곱이 조각
    # 안에서만 돌아, 조각 경계를 넘는 사건이 그 앞 구간에 반영되지 않는다.
    prices = adjust(prices)
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
    t_min: float = 0.0,
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
            ic=float("nan"), ic_std=float("nan"), ic_t=float("nan"),
            sample_days=0, sample_rows=0,
            threshold=threshold, min_sample_days=min_sample_days, t_min=t_min,
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
        # lag = horizon-1. 5일 타깃이면 연속 4일이 겹친다.
        ic_t=newey_west_t(series, lag=max(horizon - 1, 0)),
        sample_days=int(series.size),
        sample_rows=len(merged),
        threshold=threshold,
        min_sample_days=min_sample_days,
        t_min=t_min,
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
    # **컬럼을 좁힌다.** 이 창은 백테스트에서 하루가 갈수록 차오른다 — 자기가
    # 쌓은 신호를 자기가 다시 읽기 때문이다. 전체 컬럼으로 퍼오면 114일치가
    # 1.1M행 · 798MB 가 되고, 그 중 73% 가 여기서 쓰지도 않는 ``evidence_json``
    # 같은 문자열이다. 2026-02-20 세션이 신호 단계 0초 → 96초, RSS 1.6GB →
    # 6.2GB 로 튄 자리이며 그 뒤 OOM 으로 죽었다.
    # 남는 **행**은 좁혀도 바뀌지 않는다 (정정본 선택은 프루닝 전에 끝난다).
    frame = store.get(
        "signals",
        as_of=as_of,
        lookback=int(window * 7 / 5) + 30,
        market=None,
        columns=["entity_id", "analyst", "score"],
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



def marginal_shares(
    scored: "Mapping[str, pd.DataFrame]",
    targets: pd.DataFrame,
    *,
    horizon: int = HORIZON_DAYS,
    t_min: float = 2.0,
) -> tuple[dict[str, float], dict[str, tuple[float, float]]]:
    """통과한 알파 Analyst 들의 **한계기여 기반 가중치** (2026-08-25).

    ## 왜

    동등 가중은 겹치는 신호가 **중복 투표**하게 한다. 실측(signal-combination.md):
    event 는 단일 IC t 7.66 으로 강하지만 fundamental 위 한계기여가 ~0 이다 —
    기업행위가 재무와 상관되기 때문이다. 동등 가중이면 event 가 재무의 목소리를
    한 번 더 내고, 그만큼 다른 정보가 희석된다.

    ## 규칙 (사전 등록)

    각 Analyst 의 한계기여 ΔIC = IC(전체 동등결합) − IC(그것만 뺀 결합) 을
    일별 차이 시계열로 재고 Newey-West t 를 붙인다.

        share_i = ΔIC_i   (ΔIC_i > 0 이고 t_i ≥ t_min 일 때만; 아니면 0)
        가중치  = share / max(share)          → (0, 1]

    **전부 0 이면 동등 가중으로 물러선다** — 서로 완전한 쌍둥이 신호들은 LOO 로
    갈라지지 않는다(하나를 빼도 나머지가 대신한다). 그 경우는 오늘과 같은
    동등 가중이므로 나빠지지 않고, 물러섰다는 사실은 호출자가 크게 적는다.

    돌려주는 둘째 값은 {analyst: (ΔIC, t)} — 결정 근거를 성적표 옆에 남긴다.
    """
    names = sorted(scored)
    if len(names) <= 1:
        return {name: 1.0 for name in names}, {name: (float("nan"), float("nan")) for name in names}

    # Analyst 마다 세션별 z — 점수 스케일이 서로 달라 그대로 평균하면 큰 쪽이
    # 다 먹는다. combine 의 실전 경로와 같은 정신이다.
    z_frames = []
    for name in names:
        frame = scored[name].loc[:, ["entity_id", "session", "score"]].copy()
        frame["z"] = cross_sectional_z(frame, "score")
        frame["analyst"] = name
        z_frames.append(frame.dropna(subset=["z"]))
    stacked = pd.concat(z_frames, ignore_index=True)

    def ic_of(members: Sequence[str]) -> pd.Series:
        sub = stacked[stacked["analyst"].isin(members)]
        combined = (
            sub.groupby(["entity_id", "session"], as_index=False)["z"].mean()
            .rename(columns={"z": "score"})
        )
        merged = combined.merge(targets, on=["entity_id", "session"], how="inner")
        return daily_ic(merged)

    full = ic_of(names)
    details: dict[str, tuple[float, float]] = {}
    shares: dict[str, float] = {}
    for name in names:
        loo = ic_of([other for other in names if other != name])
        diff = (full - loo).dropna()
        delta = float(diff.mean()) if not diff.empty else float("nan")
        t_value = newey_west_t(diff, lag=max(horizon - 1, 0))
        details[name] = (delta, t_value)
        shares[name] = delta if (np.isfinite(delta) and delta > 0 and np.isfinite(t_value) and t_value >= t_min) else 0.0

    top = max(shares.values())
    if top <= 0.0:
        return {name: 1.0 for name in names}, details
    return {name: value / top for name, value in shares.items()}, details


def thresholds(store: Store, *, as_of: datetime) -> tuple[float, int, float]:
    """합격선·표본 하한·t 하한. 하드코딩 금지 (불변식 10)."""
    return (
        float(store.config("analyst.ic_threshold", as_of=as_of)),
        int(store.config("analyst.ic_min_samples", as_of=as_of)),
        float(store.config("analyst.ic_t_min", as_of=as_of)),
    )


__all__ = [
    "EMBARGO_DAYS",
    "HORIZON_DAYS",
    "NO_EVIDENCE_CONFIDENCE",
    "ICResult",
    "build_targets",
    "cross_sectional_z",
    "daily_ic",
    "evaluate",
    "forward_returns",
    "purged_folds",
    "thresholds",
]
