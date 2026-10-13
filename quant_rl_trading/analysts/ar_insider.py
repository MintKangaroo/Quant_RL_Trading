"""ar_insider Analyst — 시행 AR 의 처리 모델(점수 6 + is_us + 내부자 G4·G7)을 **얼린 그대로** 매일 채점한다(관찰 모드, AR shadow 전용).

## AR 이 무엇인가 (docs/protocols/insider-forward-2026-09.md, 해시 1ad99d859074c5b1)

6차 G4(국장 DART 임원·주요주주 지분 변동)·G7(미장 SEC Form 4)을 랭커 입력에 더한 GBM — 국장+미장 한 모델(is_us).
`tools/trial_insider_forward.py --freeze` 가 2026-06-30 까지(퍼지 5 → 학습 끝 2026-06-23)로 시드 0·1·2 를 얼렸고, 금고 early
(7/1~9/30, 10/13 판정)에서 두 시장 다 통과했다("채택 후보 → 랭커 피처 추가 → 재학습 게이트 → shadow").

## 왜 얼린 모델인가 (다시 학습하지 않는다)

등록 문서가 정한 순서는 "피처 추가 → **재학습 게이트**(modelops-ranker.md ③) → shadow" 다. 그 게이트는 **2026-11 하순 금고 개봉 뒤**
매월 `train_ranker --through <직전 월말>` 로 돈다. 지금 다시 학습하면 (가) 6/30 까지로는 얼린 파일과 같은 것을 다시 만들 뿐이고,
(나) 9/30 까지로 늘리면 h5 라벨이 10/1 이후 가격(금고 second 창)에 닿는다. 그래서 shadow 는 **금고가 판정한 바로 그 파일**을 쓰고,
재학습 게이트는 11/23 개봉 뒤 원래 일정대로 간다(docs/design/c1-ar-shadow.md).

## 피처 — 판정 패널과 같은 규칙

| 열 | 판정·학습 패널 | 여기(매일) |
|---|---|---|
| 점수 6 (chart·event·flow·fundamental·regime·risk) | `bake_scores`(창고 `signals`) → `trial_pooled.load_kr` · 세션 안 rank-gauss · 결측 0 | `fa_features._scores`(같은 표, 그 세션 as_of) · 같은 rank-gauss |
| is_us | 0(국장) | 0 |
| G4 2열·G7 3열 | `build_panel` = `ranker_sources.build(묶음, RiskAnalyst, 세션 개장 as_of)` → `attach`(왼쪽 붙임 · 패널 행 안 rank-gauss · 결측 0) | `fa_features._group`(같은 호출) → 같은 붙임 |
| 행 집합 | 점수 6 중 하나라도 있는 종목 ∩ h5 타깃이 있는 종목 | 점수 종목 ∩ **그 세션 시세가 있는 종목**(타깃은 미래라 모른다 — 곧 상장폐지될 종목이 남는 차이, be2-shadow.md 4) |

열 순서가 계약이다 — 얼린 모델은 numpy 로 학습해 열 이름을 모른다(`trial_insider_forward.freeze_ar` 의 `treat` 순서).
국장 G7 은 판정 때도 0 행(미장 자료)이라 전부 0 이 된다 — 같은 함수를 불러 같은 결과를 낸다.

## 지키는 것

- 얼린 파일의 sha256 앞 16자가 등록 문서 '모델 해시' 와 다르면 점수를 안 낸다(`MODEL_HASHES` — 테스트가 문서와 맞춘다).
- `USABLE_FROM`(2026-07-01) 전 as_of 는 채점하지 않는다 — 학습창 안을 채점하면 외운 값이다.
- 점수 6 열 중 하나라도 그 세션에 비었으면(일일 실행기 전·Analyst 사망) 점수를 안 낸다 — 빈 열은 "순위 중앙" 으로 보여 경고가 없다.
- 관찰 모드: 일일 실행기·주간 IC 목록에 없다 → 운영 가중치 0. 쓰는 곳은 `data/_ar_shadow` 의 `selector.weights_override` 뿐.
- 국장만(지금). 모델은 두 시장 한 모델이지만 미장 shadow 는 따로 정한다(설계 문서 '미장').
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from quant_rl_trading.analysts import fa_features
from quant_rl_trading.analysts.base import rank_score
from quant_rl_trading.analysts.be2 import percentile
from quant_rl_trading.analysts.ranker import RankerAnalyst, rank_gauss
from quant_rl_trading.analysts.ranker_sources import GROUPS
from quant_rl_trading.collectors.market_hours import Market
from quant_rl_trading.schemas.fa import SCORE_COLUMNS

logger = logging.getLogger(__name__)

NAME = "ar_insider"
VERSION = "ar-insider-v1.0.0"
#: 등록 문서와 그 sha256 앞 16자(금고 앞당김 문서가 고정한 `doc:AR`).
PROTOCOL = "docs/protocols/insider-forward-2026-09.md"
PROTOCOL_HASH = "1ad99d859074c5b1"
INSIDER_GROUPS: tuple[str, ...] = ("G4", "G7")
INSIDER_COLUMNS: tuple[str, ...] = tuple(c for g in INSIDER_GROUPS for c in GROUPS[g])
#: 열 순서 = `trial_insider_forward.freeze_ar` 의 treat = FEATS + is_us + G4 + G7.
FEATURES: tuple[str, ...] = (*SCORE_COLUMNS, "is_us", *INSIDER_COLUMNS)
SEEDS: tuple[int, ...] = (0, 1, 2)
#: 등록 문서 '모델 해시' 절 그대로(모델 문자열 sha256 앞 16자). 문서가 바뀌면 테스트가 깨진다.
MODEL_HASHES: dict[str, str] = {
    "AR-treat-s0": "ba7cc4fe549c2859",
    "AR-treat-s1": "93f68515735204e4",
    "AR-treat-s2": "1f244207d4663ce4",
}
TRAINED_THROUGH = date(2026, 6, 23)
USABLE_FROM = date(2026, 7, 1)


def model_dir(root: Path) -> Path:
    """얼린 AR 처리 모델 자리 — `tools/install_ar_shadow_models.py` 가 금고 판정 파일을 그대로 복사한다."""
    return Path(root) / "models" / "ar_insider"


@dataclass(frozen=True)
class ArModel:
    folder: Path

    def path(self, seed: int) -> Path:
        return self.folder / f"AR-treat-s{seed}.txt"

    def problems(self) -> list[str]:
        out = []
        for seed in SEEDS:
            path = self.path(seed)
            if not path.exists():
                out.append(f"파일 없음: {path.name}")
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
            want = MODEL_HASHES[f"AR-treat-s{seed}"]
            if digest != want:
                out.append(f"지문 불일치 {path.name}: {digest} ≠ 등록 {want}")
        return out

    def seed_predictions(self, x: np.ndarray) -> dict[int, np.ndarray]:
        import lightgbm as lgb

        out: dict[int, np.ndarray] = {}
        for seed in SEEDS:
            booster = lgb.Booster(model_file=str(self.path(seed)))
            if booster.num_feature() != x.shape[1]:
                raise ValueError(f"AR 모델 피처 {booster.num_feature()} ≠ 입력 {x.shape[1]}")
            out[seed] = np.asarray(booster.predict(x), dtype=float)
        return out


def usable_ar_model(root: Path, *, as_of: datetime) -> tuple[ArModel | None, str]:
    """(모델, 못 쓰는 이유). 이유가 빈 문자열이면 쓴다."""
    if as_of.date() < USABLE_FROM:
        return None, f"as_of {as_of.date()} < usable_from {USABLE_FROM} — 학습창 안은 채점하지 않는다"
    model = ArModel(model_dir(root))
    issues = model.problems()
    if issues:
        return None, "; ".join(issues)
    return model, ""


def session_features(store, market: str, session: date, as_of: datetime) -> pd.DataFrame | str:  # type: ignore[no-untyped-def]
    """그 세션의 AR 입력(종목 인덱스, `FEATURES` 순서). 못 만들면 **이유 문자열**.

    판정 패널(`trial_pooled.load_kr` → `rank_gauss` → `trial_ranker_sources.attach`)과 같은 규칙 — 모듈 독스트링의 표.
    """
    scores = fa_features._scores(store, market, session, as_of)
    if scores.empty:
        return f"{session} 점수 6 이 창고에 없다 — 일일 실행기 전이거나 세션이 아니다"
    empty = [c for c in SCORE_COLUMNS if scores[c].notna().sum() == 0]
    if empty:
        return f"{session} 점수 열 {empty} 이 비었다 — 일일 실행기가 아직 안 돌았거나 Analyst 가 죽었다"
    priced = fa_features._priced(store, market, session, as_of)
    panel = scores[scores["entity_id"].astype(str).isin(priced)].drop(columns=["session"]).reset_index(drop=True)
    if panel.empty:
        return f"{session} 점수 종목 중 그 세션 시세가 있는 종목이 없다"
    panel = rank_gauss(panel, SCORE_COLUMNS)
    panel["is_us"] = 1.0 if market == "US" else 0.0
    for group in INSIDER_GROUPS:
        cols = list(GROUPS[group])
        raw = fa_features._group(store, group, market, session)
        if raw is None or raw.empty:
            panel[cols] = np.nan
        else:
            panel = panel.merge(raw[["entity_id", *cols]], on="entity_id", how="left")
        panel = rank_gauss(panel, cols)
        panel[cols] = panel[cols].fillna(0.0)
    out = panel.set_index("entity_id").loc[:, list(FEATURES)].astype(np.float32)
    out.index.name = None
    return out


class ArInsiderAnalyst(RankerAnalyst):
    """얼린 AR 처리 GBM 시드 0·1·2 의 세션 안 백분위 평균 → 순위 z → `ranker.smoothing_span` EMA(ranker·be2 와 같은 규칙)."""

    name = NAME
    version = VERSION

    def __init__(self, store, clock, *, market: Market = Market.KR, models_root: Path | None = None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(store, clock, market=market, models_root=models_root)
        self._ar: ArModel | None = None
        self._pred: pd.Series = pd.Series(dtype=float)
        #: 점수를 안 낸 이유 — 도구가 로그·rc 에 옮긴다. 빈 문자열이면 냈다.
        self.skip_reason = ""

    def _resolve_ar(self, as_of: datetime) -> tuple[ArModel | None, str]:
        roots = [self.models_root] if self.models_root is not None else [Path(self.store.root)]
        if self.models_root is None:
            from quant_rl_trading.store import _default_root

            default = _default_root()
            if default != Path(self.store.root):
                roots.append(default)
        reason = ""
        for root in roots:
            model, reason = usable_ar_model(root, as_of=as_of)
            if model is not None:
                return model, ""
        return None, reason

    def features(self, as_of: datetime) -> pd.DataFrame:
        self._as_of = as_of
        self._pred = pd.Series(dtype=float)
        self.skip_reason = ""
        if self.market != Market.KR:
            self.skip_reason = "국장만 — 미장 AR shadow 는 따로 정한다"
            return pd.DataFrame()
        self._ar, reason = self._resolve_ar(as_of)
        if self._ar is None:
            self.skip_reason = f"얼린 AR 모델을 쓸 수 없다: {reason}"
            return pd.DataFrame()
        market = str(self.market)
        session = fa_features.session_of(market, as_of)
        frame = session_features(self.store, market, session, as_of)
        if isinstance(frame, str):
            self.skip_reason = frame
            return pd.DataFrame()
        per_seed = [percentile(p) for p in self._ar.seed_predictions(frame.to_numpy(np.float32)).values()]
        self._pred = pd.Series(np.mean(per_seed, axis=0), index=frame.index)
        return frame.astype(float)

    def evidence_for(self, features: pd.DataFrame, entity_id: str):  # type: ignore[no-untyped-def]
        # 근거는 점수 여섯과 내부자 열 — is_us 는 시장 표시라 뺀다.
        cols = [c for c in FEATURES if c != "is_us"]
        return super(RankerAnalyst, self).evidence_for(features.loc[:, cols], entity_id)

    def raw_score(self, features: pd.DataFrame) -> pd.Series:
        """시드 평균 백분위 → 순위 z → 직전 세션 자기 점수와 EMA(be2 `raw_score` 와 같은 규칙)."""
        if self._ar is None or features.empty or self._pred.empty:
            return pd.Series(0.0, index=features.index)
        raw = rank_score(self._pred.reindex(features.index))
        span = self._smoothing_span(self._as_of) if self._as_of else 0
        if span <= 1:
            return raw
        previous = self._previous_z(self._as_of)  # type: ignore[arg-type]
        if previous.empty:
            return raw
        alpha = 2.0 / (span + 1.0)
        prev = previous.reindex(raw.index)
        return raw.where(prev.isna(), alpha * raw + (1.0 - alpha) * prev)


__all__ = [
    "FEATURES", "INSIDER_COLUMNS", "MODEL_HASHES", "NAME", "PROTOCOL", "PROTOCOL_HASH", "SEEDS", "USABLE_FROM", "VERSION",
    "ArInsiderAnalyst", "ArModel", "model_dir", "session_features", "usable_ar_model",
]
