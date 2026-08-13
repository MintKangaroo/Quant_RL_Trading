"""Signal — Analyst 가 내는 점수.

동결(frozen) 이다. 낸 점수는 바뀌지 않는다.

**score 의 정의는 하나로 통일한다** (agents.md §1)::

    score = tanh(예측 초과수익의 횡단면 z-score / 2)

"horizon_days 뒤, **같은 시장 종목들 대비** 얼마나 더 오를 것인가" 다. 정의가
Analyst 마다 다르면 Selector 가 점수를 합칠 수 없다. 절대수익 예측과 상대수익
예측을 섞으면 시장이 통째로 오르는 날 모든 Analyst 가 만장일치로 매수를
외치고, 그건 아무 정보도 아니다.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: 타깃 기간은 5일로 통일한다. 느린 신호도 5일 예측에 기여하되 IC 가 낮게
#: 나오는 것이 정상이다. 5일에서 못 넘으면 기준을 낮추지 말고 horizon 20일
#: 버전을 **별도 모델**로 추가한다 (agents.md §1).
DEFAULT_HORIZON_DAYS = 5


def score_from_z(z: float) -> float:
    """횡단면 z-score → score. 이 함수 말고 다른 경로로 score 를 만들지 않는다.

    ``tanh`` 로 누르는 이유는 꼬리 때문이다. z 가 5 인 종목과 15 인 종목은
    둘 다 "매우 좋다" 일 뿐이고, 그 차이를 선형으로 믿으면 Selector 가
    이상치 하나에 포트폴리오를 몰아준다.
    """
    return math.tanh(z / 2.0)


class Evidence(BaseModel):
    """왜 이 점수가 나왔는지. Decision Trace 화면이 이걸 읽는다."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    value: float
    note: str = ""

    def canonical(self) -> dict[str, Any]:
        return {"key": self.key, "value": round(self.value, 6), "note": self.note}


class Signal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    analyst: str
    #: 모델이 바뀌면 버전이 바뀐다. 버전 없이 IC 를 재면 어느 모델의 성적인지
    #: 알 수 없고, 나쁜 모델의 성적이 좋은 모델에 섞인다.
    analyst_version: str
    entity_id: str
    as_of: datetime
    score: float = Field(ge=-1.0, le=1.0)
    #: **에이전트가 스스로 매기지 않는다.** 최근 60일 롤링 IC 로 계산한다
    #: (agents.md §1). 스스로 매기게 하면 과신한다.
    confidence: float = Field(ge=0.0, le=1.0)
    horizon_days: int = Field(default=DEFAULT_HORIZON_DAYS, gt=0)
    #: 같은 피처에서 같은 점수가 나왔는지 확인하는 지문. agent_cache 의 키다.
    features_hash: str = ""
    evidence: tuple[Evidence, ...] = ()
    latency_ms: float = 0.0

    @field_validator("as_of")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError(f"as_of 에 타임존이 없다: {value!r}")
        return value

    def canonical(self) -> dict[str, Any]:
        """직렬화 시 항상 같은 모양. 결정론 비교의 단위다."""
        return {
            "analyst": self.analyst,
            "analyst_version": self.analyst_version,
            "entity_id": self.entity_id,
            "as_of": self.as_of.isoformat(),
            # 부동소수 표현 차이가 바이트 비교를 깨지 않게 고정 자릿수로 묶는다.
            "score": round(self.score, 6),
            "confidence": round(self.confidence, 6),
            "horizon_days": self.horizon_days,
            "features_hash": self.features_hash,
            "evidence": [item.canonical() for item in self.evidence],
        }

    def row(self, *, observed_at: datetime, source: str) -> dict[str, Any]:
        """store 적재용 행.

        ``valid_from`` 은 신호가 유효해진 시점(as_of)이고 ``observed_at`` 은
        우리가 그것을 계산해 알게 된 시점이다. 리플레이에서 Signal 이 다시
        보이려면 둘이 구분돼 있어야 한다.
        """
        import json

        return {
            "entity_id": self.entity_id,
            "valid_from": self.as_of,
            "observed_at": observed_at,
            "source": source,
            "analyst": self.analyst,
            "analyst_version": self.analyst_version,
            "score": self.score,
            "confidence": self.confidence,
            "horizon_days": self.horizon_days,
            "features_hash": self.features_hash,
            "evidence_json": json.dumps(
                [item.canonical() for item in self.evidence],
                ensure_ascii=False,
                sort_keys=True,
            ),
            "latency_ms": self.latency_ms,
        }
