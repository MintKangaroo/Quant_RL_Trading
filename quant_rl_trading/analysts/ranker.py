"""ranker Analyst — 기초 Analyst 점수 6개를 **순위 목적**으로 다시 묶는다.

## 왜 이게 필요했나 (2026-09-03, 시행 L — docs/protocols/rank-objective-ranker-2026-09.md)

점수를 데이터로 묶는 시도는 세 번 졌다(랭커 B·pooled 4변형·매매 전반 RL). 진단
D1~D7 이 가른 원인은 표본도 정보도 아니고 **목적함수**였다 — 수익률 z 를 MSE 로
맞추면 꼬리(상한가·급락)를 맞추느라 순위를 버린다. 타깃과 피처를 **세션 안 순위
정규분위(rank-gauss)** 로 바꾸자 같은 GBM 이 국장 ΔIC +0.028(NW t 2.88)·미장
+0.031(t 4.40) 으로 fundamental 을 이겼고, 최악 20세션 블록도 +0.003 이었다.

## 이 Analyst 가 지키는 것

- **읽는 것은 `signals` 표뿐이다.** 시세·재무를 직접 읽지 않는다 — 기초 Analyst 가
  그 세션에 낸 점수가 입력이고, 그래서 `session/signals.SCORERS` 에서 **맨 마지막**에
  돈다. 앞선 것이 안 냈으면 그 열은 0(순위 중앙)이다.
- **모델은 학습 종료일 뒤에만 쓴다.** `tools/train_ranker.py` 가 남긴 산출물마다
  `usable_from` 이 붙고, as_of 가 그보다 앞서면 신호를 **안 낸다**. 학습창 안을
  채점하면 IC 는 외운 값이고, 그 IC 로 받은 가중치는 거짓이다. 산출물이 없어도
  같다 — 지어내지 않는다.
- **홀드아웃(2026-07~)을 학습에 안 쓴다.** 마지막 모델은 2026-06-30 까지로 굳는다.
  다시 학습하는 것은 홀드아웃을 여는 등록과 함께다.
- **fundamental 을 대체하는 알파다.** 규칙으로 빼지 않는다 — `analyst_weights` 의
  한계기여(LOO ΔIC) 규칙이 겹치는 쪽의 가중을 0 으로 내린다(`ic.marginal_shares`).
  risk 는 제약이라 그대로다(`selector/constraints.py`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

from quant_rl_trading.analysts.base import Analyst, rank_score
from quant_rl_trading.collectors.market_hours import Market

SIGNALS = "signals"
VERSION = "ranker-v0.1.0"

#: 입력 Analyst → 피처 열. 두 시장의 수급이 한 열(`flow`)로 겹치는 이유는 모델을
#: 국장+미장 합쳐 학습하기 때문이다 — 시행 L 의 GBM-POOL. `volume` 은 뺐다(시행 L
#: 의 입력이 아니었고, 등록 안 한 열을 넣으면 그 결과는 시행 L 의 것이 아니다).
BASE_ANALYSTS: dict[str, str] = {
    "chart": "chart",
    "event": "event",
    "flow_kr": "flow",
    "flow_us": "flow",
    "fundamental": "fundamental",
    "regime": "regime",
    "risk": "risk",
}
SCORE_FEATURES: tuple[str, ...] = ("chart", "event", "flow", "fundamental", "regime", "risk")
FEATURES: tuple[str, ...] = SCORE_FEATURES + ("is_us",)

#: 오늘 세션의 신호만 본다. 달력일 1 이면 valid_from 하한이 어제 자정이라 오늘 세션 하나가
#: 들어오고, 주말·휴장 뒤에도 "직전 세션" 이 아니라 **as_of 세션** 만 남긴다(아래 필터).
LOOKBACK_DAYS = 2

#: GBM 고정 설정 — 시행 L 사전등록 그대로. 여기서 바꾸면 채택 근거가 사라진다.
GBM_PARAMS: dict[str, object] = {
    "objective": "regression", "num_leaves": 7, "min_data_in_leaf": 2000,
    "learning_rate": 0.03, "bagging_fraction": 0.8, "bagging_freq": 1,
    "feature_fraction": 1.0, "lambda_l2": 1.0, "verbose": -1, "seed": 0,
}
GBM_ROUNDS = 300


def rank_gauss(frame: pd.DataFrame, columns: list[str] | tuple[str, ...], *, by: list[str] | None = None) -> pd.DataFrame:
    """열마다 (그룹 안) 순위 → 정규분위. 결측은 0 = 순위 중앙.

    `(rank − 0.5) / n` 을 Φ⁻¹ 에 넣는다. 타깃을 이렇게 바꾸면 최소제곱이 곧 일별
    Spearman IC 최대화에 가까워진다 — 시행 L 이 이긴 이유 전부다. 학습(tools/train_ranker)
    과 실전(features) 이 **같은 함수** 를 쓴다.
    """
    out = frame.copy()
    cols = list(columns)
    grouped = out.groupby(by, sort=False)[cols] if by else None
    ranks = grouped.rank(method="average") if grouped is not None else out[cols].rank(method="average")
    counts = grouped.transform("count") if grouped is not None else out[cols].count()
    quantiles = (ranks - 0.5) / counts
    out[cols] = norm.ppf(quantiles.clip(1e-6, 1 - 1e-6)).astype(float)
    out[cols] = out[cols].where(quantiles.notna(), 0.0)
    return out


# --------------------------------------------------------------------------- 모델 산출물


@dataclass(frozen=True)
class RankerModel:
    """`tools/train_ranker.py` 가 남긴 것. 부스터 파일 + 사이드카(JSON)."""

    path: Path
    version: str
    trained_through: date
    usable_from: date
    features: tuple[str, ...]
    rows: int

    @classmethod
    def load(cls, sidecar: Path) -> RankerModel:
        meta = json.loads(sidecar.read_text(encoding="utf-8"))
        # `with_suffix` 를 안 쓴다 — 버전 문자열의 점(v0.1.0)을 확장자로 오해한다.
        return cls(
            path=sidecar.with_name(sidecar.name[: -len(".json")] + ".txt"),
            version=str(meta["version"]),
            trained_through=date.fromisoformat(meta["trained_through"]),
            usable_from=date.fromisoformat(meta["usable_from"]),
            features=tuple(meta["features"]),
            rows=int(meta.get("rows", 0)),
        )

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        import lightgbm as lgb

        booster = lgb.Booster(model_file=str(self.path))
        return np.asarray(booster.predict(frame.loc[:, list(self.features)].to_numpy(np.float32)), dtype=float)


def model_dir(root: Path) -> Path:
    return Path(root) / "models" / "ranker"


def usable_model(root: Path, *, as_of: datetime, version: str = VERSION) -> RankerModel | None:
    """as_of 에 써도 되는 가장 최근 모델. 없으면 None — 지어내지 않는다.

    `usable_from ≤ as_of 날짜` 인 것 중 학습 종료일이 가장 늦은 것. 버전이 다르면
    피처 정의가 다른 것이므로 안 본다.
    """
    folder = model_dir(root)
    if not folder.is_dir():
        return None
    day = as_of.date()
    candidates = []
    for sidecar in sorted(folder.glob(f"{version}-*.json")):
        try:
            model = RankerModel.load(sidecar)
        except (KeyError, ValueError, json.JSONDecodeError):
            continue
        if model.version == version and model.usable_from <= day and model.path.exists():
            candidates.append(model)
    if not candidates:
        return None
    return max(candidates, key=lambda m: m.trained_through)


# --------------------------------------------------------------------------- Analyst


class RankerAnalyst(Analyst):
    name = "ranker"
    version = VERSION

    def __init__(self, store, clock, *, market: Market = Market.KR, models_root: Path | None = None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(store, clock, market=market)
        # 모델은 창고 옆 `models/ranker/` 에 산다. 백테스트용 창고(data/_backtest…)에는
        # 없으므로 기본 창고 쪽으로 물러선다 — 같은 모델로 과거를 돌려야 같은 코드다.
        self.models_root = models_root
        self._model: RankerModel | None = None

    def _resolve_model(self, as_of: datetime) -> RankerModel | None:
        roots = [self.models_root] if self.models_root is not None else [Path(self.store.root)]
        if self.models_root is None:
            from quant_rl_trading.store import _default_root

            default = _default_root()
            if default != Path(self.store.root):
                roots.append(default)
        for root in roots:
            model = usable_model(root, as_of=as_of)
            if model is not None:
                return model
        return None

    def features(self, as_of: datetime) -> pd.DataFrame:
        """오늘 세션의 기초 Analyst 점수 → rank-gauss 피처. 모델이 없으면 빈 프레임."""
        self._model = self._resolve_model(as_of)
        if self._model is None:
            return pd.DataFrame()
        frame = self.store.get(
            SIGNALS,
            as_of=as_of,
            lookback=LOOKBACK_DAYS,
            columns=["entity_id", "valid_from", "observed_at", "analyst", "score"],
        )
        if frame.empty:
            return pd.DataFrame()
        prefix = f"{self.market}:"
        frame = frame[
            frame["entity_id"].astype(str).str.startswith(prefix)
            & frame["analyst"].astype(str).isin(BASE_ANALYSTS)
        ]
        if frame.empty:
            return pd.DataFrame()
        # **as_of 세션 하나만.** 어제 점수로 오늘을 매기면 하루 늦은 신호에 오늘 날짜가 붙는다.
        latest_session = frame["valid_from"].max()
        if latest_session.date() != as_of.date():
            return pd.DataFrame()
        frame = frame[frame["valid_from"] == latest_session]
        frame = frame.sort_values("observed_at").groupby(["entity_id", "analyst"], as_index=False).tail(1)
        frame["feature"] = frame["analyst"].map(BASE_ANALYSTS)
        wide = frame.pivot_table(index="entity_id", columns="feature", values="score", aggfunc="last")
        for column in SCORE_FEATURES:
            if column not in wide.columns:
                wide[column] = np.nan
        wide = rank_gauss(wide, SCORE_FEATURES)
        wide["is_us"] = 1.0 if self.market == Market.US else 0.0
        return wide.loc[:, list(FEATURES)].astype(float)

    def evidence_for(self, features: pd.DataFrame, entity_id: str):  # type: ignore[override]
        # `is_us` 는 시장 표시지 근거가 아니다 — 화면에 "is_us 1.0" 이 뜨면 아무것도 말해주지 않는다.
        return super().evidence_for(features.loc[:, list(SCORE_FEATURES)], entity_id)

    def raw_score(self, features: pd.DataFrame) -> pd.Series:
        """모델 예측 → 횡단면 순위 z. 예측값의 절대 크기는 다른 Analyst 와 단위가 다르다."""
        if self._model is None or features.empty:
            return pd.Series(0.0, index=features.index)
        predicted = pd.Series(self._model.predict(features), index=features.index)
        return rank_score(predicted)


__all__ = [
    "BASE_ANALYSTS", "FEATURES", "GBM_PARAMS", "GBM_ROUNDS", "RankerAnalyst", "RankerModel",
    "SCORE_FEATURES", "VERSION", "model_dir", "rank_gauss", "usable_model",
]
