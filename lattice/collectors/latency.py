"""지연 실측.

관측 즉시 사용한다고 가정하지 않는다. 파이프라인 각 단계에 타임스탬프를
찍어 실제 소요시간을 기록하고, 백테스트 지연은 그 **실측 분포의 p90** 을 쓴다.

가정한 지연과 실제 지연의 차이가 백테스트를 거짓말로 만드는 대표적 원인이다
(data-contract §5). 목표는 1분 이내지만 그것은 목표이지 가정이 아니다.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from lattice.replay.clock import Clock
from lattice.store import Store

LATENCY_TABLE = "ingest_latency"


@dataclass
class LatencyRecorder:
    """단계별 소요시간을 모아 store 에 적재한다."""

    store: Store
    clock: Clock
    source: str
    ingest_run_id: str
    _rows: list[dict[str, Any]] = field(default_factory=list, repr=False)

    @contextmanager
    def stage(self, name: str, detail: str = "") -> Iterator[None]:
        """한 단계를 감싼다. 예외가 나도 실패로 기록하고 다시 던진다.

        실패한 호출의 지연을 버리면 분포가 낙관적으로 왜곡된다 — 느려서
        타임아웃 난 호출이야말로 지연 예산이 알아야 할 사건이다.
        """
        started = self.clock.now()
        ok = True
        try:
            yield
        except Exception:
            ok = False
            raise
        finally:
            finished = self.clock.now()
            self._rows.append(
                {
                    "entity_id": self.source,
                    "valid_from": started,
                    "observed_at": finished,
                    "source": self.source,
                    "stage": name,
                    "elapsed_ms": (finished - started).total_seconds() * 1000.0,
                    "ok": ok,
                    "detail": detail,
                }
            )

    def flush(self) -> int:
        if not self._rows:
            return 0
        rows, self._rows = self._rows, []
        return self.store.append(LATENCY_TABLE, rows, ingest_run_id=self._attempt_id())

    def _attempt_id(self) -> str:
        """시도마다 다른 run id.

        수집이 실패해 재시도하면 같은 ingest_run_id 로 두 번 지연을 적재하게 되고,
        창고는 중복으로 거절한다. 그런데 **재시도의 지연이야말로 버려선 안 되는
        표본**이다 — 느려서 실패한 호출을 빼면 p90 이 낙관적으로 왜곡되고,
        백테스트가 쓰는 지연 가정이 실제보다 짧아진다.

        그래서 실패한 시도를 덮어쓰지 않고 옆에 쌓는다 (불변식 4).
        """
        base = f"latency-{self.ingest_run_id}"
        if not self.store.ingest_run_recorded(LATENCY_TABLE, base):
            return base
        attempt = 2
        while self.store.ingest_run_recorded(LATENCY_TABLE, f"{base}-{attempt}"):
            attempt += 1
        return f"{base}-{attempt}"
