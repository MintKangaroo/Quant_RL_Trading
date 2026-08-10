"""이벤트 로그 — append-only.

관측 → Signal → Selector 결정 → Allocator 액션 → 주문 → 체결까지 전 단계를
남긴다. 리플레이는 이 로그를 재생하고, Auditor 는 이 로그로 귀속 분석을 한다.

시간 매핑 (store 의 이중시간을 그대로 쓴다):

    ts_sim  ≡ valid_from    시뮬레이션 시각. 리플레이에서 재현되는 시간
    ts_wall ≡ observed_at   실제로 계산한 시각. 리플레이마다 달라진다

``payload_hash`` 는 payload 만의 함수라 두 번 돌려도 같다. 결정론 비교는
ts_wall 이 아니라 이 해시로 한다.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from lattice.replay.clock import Clock, LiveClock
from lattice.store import Store

EVENTS_TABLE = "events"


def canonical_json(payload: Any) -> str:
    """정렬된 JSON. 같은 내용이면 언제나 같은 바이트."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_hash(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()[:16]


@dataclass
class EventLog:
    """한 run 의 이벤트를 모아 store 로 흘려보낸다.

    이벤트마다 파일을 하나씩 만들면 파티션이 파편화된다. 버퍼에 쌓고
    ``flush`` 에서 한 배치로 적재한다. 적재 자체는 store 가 append-only 를
    보장하므로 여기서 다시 보장하지 않는다.
    """

    store: Store
    #: 시뮬레이션 시계. 리플레이에서는 과거에 멈춰 있다.
    clock: Clock
    run_id: str
    #: 벽시계. 라이브에서는 clock 과 같은 것을 넣으면 두 시각이 일치한다.
    #: 리플레이에서는 "언제 이 계산을 실제로 돌렸나" 가 여기 남는다.
    wall_clock: Clock = field(default_factory=LiveClock)
    _buffer: list[dict[str, Any]] = field(default_factory=list, repr=False)
    _seq: int = 0
    _flushes: int = 0

    def record(self, stage: str, actor: str, payload: Any) -> int:
        """이벤트 하나를 적는다. 돌려주는 값은 그 이벤트의 seq."""
        seq = self._seq
        self._seq += 1
        self._buffer.append(
            {
                "entity_id": self.run_id,
                "valid_from": self.clock.now(),      # ts_sim
                "observed_at": self.wall_clock.now(),  # ts_wall
                "source": "replay",
                "seq": seq,
                "stage": stage,
                "actor": actor,
                "payload_hash": payload_hash(payload),
                "payload": canonical_json(payload),
            }
        )
        return seq

    def flush(self) -> int:
        if not self._buffer:
            return 0
        batch, self._buffer = self._buffer, []
        written = self.store.append(
            EVENTS_TABLE,
            batch,
            ingest_run_id=f"events-{self.run_id}-{self._flushes:04d}",
        )
        self._flushes += 1
        return written

    @property
    def pending(self) -> int:
        return len(self._buffer)
