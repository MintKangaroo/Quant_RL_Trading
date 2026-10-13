"""c1fa Analyst — 얼린 C1(GBM · FA 76열, 시행 L 규격)만으로 매일 채점한다(관찰 모드, C1 shadow 전용).

## C1 이 무엇인가 (docs/protocols/c1-gbm-fa-second-2026-10.md — 시행 C1G, 금고 second 창 판정)

마지막 모델 회차(final-model-round-2026-10.md)의 **대조군 C1** — 같은 GBM 을 전 피처(FA)로 학습한 것. BE2 사이드카
(`models/be2/be2-v1.0.0-20260630.json`, sha256 앞 16자 e3c347d129cc1c33) 안의 GBM 시드 0~4 가 바로 그 모델이고,
금고 early 판정(10/13)의 C1 군도 이 파일들이었다. **다시 학습하지 않는다** — 같은 파일을 읽는다.

## 지키는 것 (be2 와 같다 — 같은 클래스·같은 함수)

- 입력·창·행 집합은 `be2.session_batch` 그대로다(금고 판정부 `tools/vault_judge.be2_predictions` 와 같은 함수).
  GBM 은 그날 평평한 행(`batch.flat`)만 먹지만, 판정이 60세션 창이 찬 세션만 채점하므로 여기서도 같은 관문을 지킨다.
- 시드 점수 = `Be2Model.predict_gbm(seed, flat)` 의 세션 안 백분위 → 시드 평균 → 순위 z → `ranker.smoothing_span` EMA.
  판정은 시드마다 포트를 만들어 **지표를** 평균하고, 장부는 하나라 **점수를** 시드 평균한다(be2-shadow.md '판정과 다른 것' 1).
- **사이드카 지문 고정**: 사이드카 sha256 앞 16자가 `SIDECAR_HASH` 와 다르면 점수를 안 낸다 — 금고 판정 모델과 다른 것을
  shadow 가 조용히 쓰지 않게. 파일별 지문은 `Be2Model.problems` 가 사이드카 기록과 대조한다.
- 관찰 모드: 일일 실행기(`session/signals.SCORERS`)·주간 IC 목록(`tools/measure_ic.ANALYSTS`)에 없다 → 운영 가중치 0.
  이 점수로 고르는 것은 `data/_c1_shadow` 의 `selector.weights_override` 뿐이다(tests/invariants/test_c1_ar_shadow_isolation.py).
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

import numpy as np

from quant_rl_trading.analysts.be2 import Be2Analyst, Be2Model, SessionBatch, percentile

NAME = "c1fa"
VERSION = "c1fa-v1.0.0"
#: 얼린 BE2 사이드카(그 안의 GBM = C1) sha256 앞 16자 — 금고 앞당김 문서 '모델 해시' 의 `be2-v1.0.0-20260630` 과 같다.
SIDECAR_HASH = "e3c347d129cc1c33"


class C1FaAnalyst(Be2Analyst):
    """얼린 C1 GBM 시드 0~4 의 세션 안 백분위 평균. 평활은 ranker·be2 와 같은 키."""

    name = NAME
    version = VERSION
    _sidecar_problem: str = ""

    def _resolve_be2(self, as_of: datetime) -> Be2Model | None:
        self._sidecar_problem = ""
        model = super()._resolve_be2(as_of)
        if model is None:
            return None
        digest = hashlib.sha256(Path(model.sidecar).read_bytes()).hexdigest()[:16]
        if digest != SIDECAR_HASH:
            self._sidecar_problem = f"사이드카 지문 {digest} ≠ 고정 {SIDECAR_HASH} — 금고 판정 모델과 다르다"
            return None
        return model

    def features(self, as_of: datetime):  # type: ignore[no-untyped-def]
        frame = super().features(as_of)
        if self._sidecar_problem:
            self.skip_reason = self._sidecar_problem
        return frame

    def _seed_scores(self, model: Be2Model, batch: SessionBatch) -> list[np.ndarray]:
        return [percentile(model.predict_gbm(seed, batch.flat)) for seed in model.seeds]


__all__ = ["NAME", "SIDECAR_HASH", "VERSION", "C1FaAnalyst"]
