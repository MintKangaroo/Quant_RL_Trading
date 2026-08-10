"""에이전트 출력 캐시.

    (agent, agent_version, entity_id, as_of, features_hash) → output

영구 저장. 리플레이 때 LLM 을 다시 부르지 않는다. 이것이 리플레이 결정론과
LLM 비용 문제를 동시에 해결한다.

``observed_at`` 은 계산한 벽시계 시각이 아니라 **as_of** 다. 출력이 as_of
이전 데이터만의 함수이므로 그 시점에 알 수 있었던 것이 맞다. 벽시계를 찍으면
오늘 채운 캐시가 과거 리플레이에서 영영 보이지 않는다 — 캐시가 조용히 무력화되고,
아무도 눈치채지 못한 채 LLM 을 매번 다시 부르게 된다.

``features_hash`` 가 키에 들어가는 이유: 같은 as_of 라도 입력 피처가 다르면
다른 출력이다. 피처가 바뀐 줄 모르고 옛 출력을 재사용하면 리플레이는
재현이 아니라 날조가 된다.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from lattice.replay.clock import Clock, require_aware
from lattice.replay.events import canonical_json
from lattice.store import Store

CACHE_TABLE = "agent_cache"


def features_hash(features: Any) -> str:
    """입력 피처의 지문. 같은 입력이면 언제나 같다."""
    return hashlib.sha256(canonical_json(features).encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class CacheKey:
    agent: str
    agent_version: str
    entity_id: str
    as_of: datetime
    features_hash: str


@dataclass
class AgentCache:
    store: Store
    #: 실제 계산 시각(computed_at)을 남기기 위한 벽시계.
    #: 캐시의 observed_at 은 여기서 오지 않는다.
    clock: Clock

    def get(self, key: CacheKey) -> Any | None:
        require_aware("as_of", key.as_of)
        frame = self.store.get(CACHE_TABLE, as_of=key.as_of, entity=key.entity_id)
        if frame.empty:
            return None
        hit = frame[
            (frame["agent"] == key.agent)
            & (frame["agent_version"] == key.agent_version)
            & (frame["features_hash"] == key.features_hash)
            & (frame["valid_from"] == key.as_of)
        ]
        if hit.empty:
            return None
        return json.loads(hit.iloc[-1]["output"])

    def put(self, key: CacheKey, output: Any, *, ingest_run_id: str) -> int:
        require_aware("as_of", key.as_of)
        return self.store.append(
            CACHE_TABLE,
            [
                {
                    "entity_id": key.entity_id,
                    "valid_from": key.as_of,
                    "observed_at": key.as_of,
                    "source": f"{key.agent}@{key.agent_version}",
                    "agent": key.agent,
                    "agent_version": key.agent_version,
                    "features_hash": key.features_hash,
                    "output": canonical_json(output),
                    "computed_at": self.clock.now(),
                }
            ],
            ingest_run_id=ingest_run_id,
        )
