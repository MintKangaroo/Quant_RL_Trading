"""be2 Analyst — 마지막 모델 회차의 채택 후보 BE2 를 **얼린 모델로** 매일 채점한다(관찰 모드, shadow 전용).

## BE2 가 무엇인가 (docs/protocols/final-model-round-2026-10.md, 해시 34abffde1d5e6bed)

BE1(세트 트랜스포머 — 종목마다 60세션 × FA 76열, 같은 날 종목끼리 주의) 과 C1(GBM · 같은 FA · 시행 L 규격)의
**세션 안 순위 평균**. 판정(2026-09-29) 대 C1 +2.1%p · 대 C0 +2.2%p 로 유일한 채택 후보였다. 판정 때는 예측만
남겼으므로 `tools/freeze_be2.py` 가 판정 창 끝(2026-06-30)까지로 한 번 학습해 `models/be2/` 에 얼린다.

## 지키는 것

- **얼린 모델만 쓴다.** 사이드카의 `usable_from`(2026-07-01) 전 as_of 에는 점수를 안 낸다 — 학습창 안을 채점하면
  외운 값이다. 모델이 없거나 파일 지문이 사이드카와 다르면 역시 안 낸다(지어내지 않는다).
- **입력은 창고 `fa_features` 뿐이다.** 매일 경로(`tools/score_be2.py`)가 `analysts/fa_features.build_session` 으로
  그 세션 행을 먼저 적고, 이 Analyst 는 as_of 에 관측된 60세션 창을 읽기만 한다. 창의 국장 세션이 하나라도 비면
  점수를 안 낸다 — 0 으로 채운 창은 "순위 중앙" 으로 보여 아무 경고도 안 낸다(kit `require_full_window` 와 같은 이유).
- **시간 축은 국장 ∪ 미장 세션**이다. 판정 때 큐브 축이 그랬다 — 국장 휴장일(미장만 연 날)은 국장 종목에 0 벡터로
  한 칸을 차지한다. 축을 국장만으로 잡으면 모델이 배운 것과 다른 창을 먹는다.
- **관찰 모드다.** 운영 장부의 가중치(`analyst_weights`)에는 이 이름이 없다 — 주간 IC 측정 목록(`tools/measure_ic.ANALYSTS`)
  에도, 일일 실행기의 `session/signals.SCORERS` 에도 없다. 이 점수로 고르는 것은 BE2 shadow 장부
  (`data/_be2_shadow`, `selector.weights_override`)뿐이다.
- 국장만. 미장 FA 의 점수 패널은 시행 AT 의 거래대금 상위 1,000 명단(`trial_us_kit.us_panel`)이었는데 실전 명단과 다르다.

순위 평균은 판정과 같다(세션·시장 안 백분위의 평균). 판정은 시드마다 BE2 를 매겨 **지표를** 평균했고, 실전 장부는
하나라 **점수를** 시드 평균한다 — 앙상블이라 시드 잡음이 줄고, 판정 숫자와 같은 대상은 아니다(docs/design/be2-shadow.md).
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from quant_rl_trading.analysts import fa_features
from quant_rl_trading.analysts.base import rank_score
from quant_rl_trading.analysts.ranker import RankerAnalyst
from quant_rl_trading.collectors.market_hours import Market, trading_days
from quant_rl_trading.schemas.fa import FA_FEATURES, SCORE_COLUMNS

logger = logging.getLogger(__name__)

VERSION = "be2-v1.0.0"
#: 등록 문서 sha256 앞 16자 — 사이드카가 다른 해시를 들고 오면 다른 판정의 모델이다.
PROTOCOL_HASH = "34abffde1d5e6bed"

# ── 판정 때 고정한 구조 (tools/trial_final_transformer.py 와 같은 값 — 사이드카가 다르면 안 쓴다) ─────────
WINDOW = 60
D_MODEL, HEADS, FFN, DROPOUT = 32, 4, 64, 0.2
TIME_LAYERS, SET_LAYERS = 2, 2
TIME_OFFSETS: tuple[int, ...] = tuple(range(0, 10)) + tuple(range(14, WINDOW, 5))
N_STEPS = len(TIME_OFFSETS)
MIN_SET = 50
ARCH: dict[str, Any] = {
    "window": WINDOW, "d_model": D_MODEL, "heads": HEADS, "ffn": FFN, "dropout": DROPOUT,
    "time_layers": TIME_LAYERS, "set_layers": SET_LAYERS, "time_offsets": list(TIME_OFFSETS), "min_set": MIN_SET,
}
#: 창 60칸(국장 ∪ 미장)을 덮는 달력일 — 연휴·양 시장 휴장을 넉넉히.
WINDOW_LOOKBACK_DAYS = 130


def build_set_ranker(n_features: int):  # type: ignore[no-untyped-def]
    """판정 도구의 `make_model` 과 **같은 구조·같은 파라미터 이름**(state_dict 가 그대로 들어간다).

    시드는 여기서 안 건다 — 얼린 가중치를 싣기만 한다. 같은 가중치면 같은 출력인지는 테스트가 두 구현을 맞춰 본다.
    """
    import torch
    from torch import nn

    class SetRanker(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.inp = nn.Linear(n_features, D_MODEL)
            self.pos = nn.Parameter(torch.zeros(1, N_STEPS, D_MODEL))
            self.drop = nn.Dropout(DROPOUT)
            time_layer = nn.TransformerEncoderLayer(D_MODEL, HEADS, FFN, DROPOUT, batch_first=True)
            self.time_encoder = nn.TransformerEncoder(time_layer, TIME_LAYERS)
            set_layer = nn.TransformerEncoderLayer(D_MODEL, HEADS, FFN, DROPOUT, batch_first=True)
            self.set_encoder = nn.TransformerEncoder(set_layer, SET_LAYERS)
            self.norm = nn.LayerNorm(D_MODEL)
            self.out = nn.Linear(D_MODEL, 1)

        def forward(self, x):  # type: ignore[no-untyped-def]
            h = self.time_encoder(self.drop(self.inp(x)) + self.pos)
            z = self.norm(h.mean(dim=1))
            z = self.set_encoder(z.unsqueeze(0)).squeeze(0)
            return self.out(z).squeeze(-1)

    return SetRanker()


# --------------------------------------------------------------------------- 얼린 모델


def model_dir(root: Path) -> Path:
    return Path(root) / "models" / "be2"


def file_digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@dataclass(frozen=True)
class Be2Model:
    """`tools/freeze_be2.py` 가 남긴 것 — 사이드카(JSON) + 시드마다 트랜스포머(.pt)·GBM(.txt)."""

    sidecar: Path
    version: str
    trained_through: date
    usable_from: date
    features: tuple[str, ...]
    seeds: tuple[int, ...]
    transformer: dict[int, Path]
    gbm: dict[int, Path]
    digests: dict[str, str] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, sidecar: Path) -> Be2Model:
        meta = json.loads(Path(sidecar).read_text(encoding="utf-8"))
        folder = Path(sidecar).parent
        seeds = tuple(int(s) for s in meta["seeds"])
        return cls(
            sidecar=Path(sidecar),
            version=str(meta["version"]),
            trained_through=date.fromisoformat(meta["trained_through"]),
            usable_from=date.fromisoformat(meta["usable_from"]),
            features=tuple(meta["features"]),
            seeds=seeds,
            transformer={s: folder / meta["files"]["transformer"][str(s)] for s in seeds},
            gbm={s: folder / meta["files"]["gbm"][str(s)] for s in seeds},
            digests=dict(meta.get("sha256", {})),
            meta=meta,
        )

    def problems(self) -> list[str]:
        """쓰면 안 되는 이유들. 빈 목록이어야 쓴다 — 구조·열·해시·파일 지문이 판정 때와 같은지."""
        out: list[str] = []
        if self.version != VERSION:
            out.append(f"버전 {self.version} ≠ {VERSION}")
        if tuple(self.features) != tuple(FA_FEATURES):
            out.append("피처 목록이 schemas/fa.FA_FEATURES 와 다르다")
        if self.meta.get("protocol_hash") != PROTOCOL_HASH:
            out.append(f"등록 해시 {self.meta.get('protocol_hash')} ≠ {PROTOCOL_HASH}")
        if self.meta.get("arch") != ARCH:
            out.append("트랜스포머 구조 상수가 사이드카와 다르다")
        if not self.seeds:
            out.append("시드가 없다")
        for path in [*self.transformer.values(), *self.gbm.values()]:
            if not path.exists():
                out.append(f"파일 없음: {path.name}")
            elif self.digests.get(path.name) and file_digest(path) != self.digests[path.name]:
                out.append(f"지문 불일치: {path.name}")
        return out

    # -- 예측 ------------------------------------------------------------------

    def predict_transformer(self, seed: int, x: np.ndarray) -> np.ndarray:
        """x: (종목, N_STEPS, 피처) float32 → (종목,). 추론 모드(dropout 끔)."""
        import torch

        model = build_set_ranker(len(self.features))
        state = torch.load(self.transformer[seed], map_location="cpu", weights_only=True)
        model.load_state_dict(state)
        model.eval()
        with torch.inference_mode():
            return np.asarray(model(torch.from_numpy(x)).numpy(), dtype=float)

    def predict_gbm(self, seed: int, x: np.ndarray) -> np.ndarray:
        import lightgbm as lgb

        booster = lgb.Booster(model_file=str(self.gbm[seed]))
        return np.asarray(booster.predict(x), dtype=float)


def usable_be2_model(root: Path, *, as_of: datetime, version: str = VERSION) -> Be2Model | None:
    """as_of 에 써도 되는 가장 최근 얼린 모델. `usable_from ≤ as_of 날짜` · 문제 없음. 없으면 None."""
    folder = model_dir(root)
    if not folder.is_dir():
        return None
    day = as_of.date()
    candidates = []
    for sidecar in sorted(folder.glob(f"{version}-*.json")):
        try:
            model = Be2Model.load(sidecar)
        except (KeyError, ValueError, json.JSONDecodeError):
            continue
        if model.usable_from > day:
            continue
        issues = model.problems()
        if issues:
            logger.warning("be2 모델 %s 를 안 쓴다: %s", sidecar.name, "; ".join(issues))
            continue
        candidates.append(model)
    if not candidates:
        return None
    return max(candidates, key=lambda m: m.trained_through)


# --------------------------------------------------------------------------- 창·예측 (판정 도구와 같은 규칙)


def time_axis(session: date, *, lookback_days: int = WINDOW_LOOKBACK_DAYS) -> list[date]:
    """판정 큐브의 시간 축 — 국장 ∪ 미장 세션 날짜, ``session`` 까지. 끝이 그 세션이다."""
    start = date.fromordinal(session.toordinal() - lookback_days)
    days = set(trading_days(Market.KR, start, session)) | set(trading_days(Market.US, start, session))
    return sorted(d for d in days if d <= session)


def window_batch(history: pd.DataFrame, axis: list[date], entities: list[str]) -> np.ndarray:
    """(종목, N_STEPS, 피처) — 판정 도구의 `build_cube`(float16) → `window_batch` 와 같은 값.

    칸 k 는 축에서 ``TIME_OFFSETS[k]`` 칸 전이다. 그날 행이 없는 종목은 0(rank-gauss 0, 표지 0) — 판정 큐브가
    그랬다(`build_cube` 는 0 으로 시작해 있는 칸만 채운다). 반정밀도로 한 번 내렸다 올린다 — 판정 큐브가 float16 이었다.
    """
    columns = list(FA_FEATURES)
    col_of = {e: i for i, e in enumerate(entities)}
    by_day = {d: part for d, part in history.groupby("session")}
    out = np.zeros((len(entities), N_STEPS, len(columns)), dtype=np.float32)
    last = len(axis) - 1
    for k, back in enumerate(TIME_OFFSETS):
        src = last - back
        if src < 0:
            continue
        part = by_day.get(axis[src])
        if part is None or part.empty:
            continue
        idx = part["entity_id"].map(col_of)
        keep = idx.notna().to_numpy()
        values = np.nan_to_num(part.loc[keep, columns].to_numpy(np.float32), nan=0.0, posinf=0.0, neginf=0.0)
        out[idx[keep].to_numpy(np.int64), k, :] = values.astype(np.float16).astype(np.float32)
    return out


def percentile(values: np.ndarray) -> np.ndarray:
    """세션 안 백분위 — `pd.Series.rank(pct=True)`(판정 `rank_average` 와 같은 함수)."""
    return np.asarray(pd.Series(values).rank(pct=True).to_numpy(float), dtype=float)


# --------------------------------------------------------------------------- Analyst


class Be2Analyst(RankerAnalyst):
    """얼린 BE2 로 그 세션 FA 를 채점한다. 평활(EMA)은 ranker 와 같은 규칙·같은 키(`ranker.smoothing_span`)."""

    name = "be2"
    version = VERSION

    def __init__(self, store, clock, *, market: Market = Market.KR, models_root: Path | None = None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(store, clock, market=market, models_root=models_root)
        self._be2: Be2Model | None = None
        self._pred: pd.Series = pd.Series(dtype=float)
        #: 점수를 안 낸 이유 — 도구가 로그·rc 에 옮긴다. 빈 문자열이면 냈다.
        self.skip_reason = ""

    def _resolve_be2(self, as_of: datetime) -> Be2Model | None:
        roots = [self.models_root] if self.models_root is not None else [Path(self.store.root)]
        if self.models_root is None:
            from quant_rl_trading.store import _default_root

            default = _default_root()
            if default != Path(self.store.root):
                roots.append(default)
        for root in roots:
            model = usable_be2_model(root, as_of=as_of)
            if model is not None:
                return model
        return None

    def features(self, as_of: datetime) -> pd.DataFrame:
        """그 세션 FA 행(종목 인덱스). 점수 계산도 여기서 끝내 둔다 — 창이 필요해서 `raw_score` 로 못 미룬다."""
        self._as_of = as_of
        self._pred = pd.Series(dtype=float)
        self.skip_reason = ""
        if self.market != Market.KR:
            self.skip_reason = "국장만 — 미장 FA 명단이 실전과 다르다"
            return pd.DataFrame()
        self._be2 = self._resolve_be2(as_of)
        if self._be2 is None:
            self.skip_reason = "쓸 수 있는 얼린 모델이 없다(usable_from 전이거나 파일·지문 문제)"
            return pd.DataFrame()
        session = fa_features.session_of(str(self.market), as_of)
        history = fa_features.read_window(self.store, as_of=as_of, market=str(self.market),
                                          lookback_days=WINDOW_LOOKBACK_DAYS)
        today = history[history["session"] == session] if not history.empty else history
        if today.empty:
            self.skip_reason = f"{session} FA 행이 창고에 없다 — score_be2 가 먼저 적는다"
            return pd.DataFrame()
        axis = time_axis(session)
        window = axis[-WINDOW:]
        have = set(history["session"])
        missing = [d for d in window if d in set(trading_days(Market.KR, window[0], session)) and d not in have]
        if missing:
            self.skip_reason = (f"60세션 창의 국장 세션 {len(missing)}개에 FA 행이 없다({missing[0]}~) — "
                                "0 으로 채운 창으로 채점하지 않는다")
            return pd.DataFrame()
        entities = sorted(today["entity_id"].astype(str))
        if len(entities) < MIN_SET:
            self.skip_reason = f"종목 {len(entities)}개 < {MIN_SET}"
            return pd.DataFrame()
        x = window_batch(history[history["session"].isin(set(window))], window, entities)
        flat = today.set_index("entity_id").loc[entities, list(FA_FEATURES)].to_numpy(np.float32)
        per_seed = []
        for seed in self._be2.seeds:
            be1 = percentile(self._be2.predict_transformer(seed, x))
            c1 = percentile(self._be2.predict_gbm(seed, flat))
            per_seed.append((be1 + c1) / 2.0)
        self._pred = pd.Series(np.mean(per_seed, axis=0), index=entities)
        frame = today.set_index("entity_id").loc[entities, list(FA_FEATURES)].astype(float)
        frame.index.name = None
        return frame

    def evidence_for(self, features: pd.DataFrame, entity_id: str):  # type: ignore[no-untyped-def]
        # 76열 중 절댓값 큰 셋은 결측 표지(1.0)가 차지한다 — 근거로는 점수 여섯만 보인다(ranker 와 같다).
        return super(RankerAnalyst, self).evidence_for(features.loc[:, list(SCORE_COLUMNS)], entity_id)

    def raw_score(self, features: pd.DataFrame) -> pd.Series:
        """시드 평균 순위 평균 → 횡단면 순위 z → 직전 세션 자기 점수와 EMA(ranker 와 같은 규칙)."""
        if self._be2 is None or features.empty or self._pred.empty:
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
    "ARCH", "Be2Analyst", "Be2Model", "MIN_SET", "N_STEPS", "PROTOCOL_HASH", "TIME_OFFSETS", "VERSION",
    "WINDOW", "build_set_ranker", "file_digest", "model_dir", "percentile", "time_axis", "usable_be2_model",
    "window_batch",
]
