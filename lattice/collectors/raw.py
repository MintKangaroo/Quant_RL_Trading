"""원본 응답 보관소.

정규화 로직에 버그를 발견해도 원본이 있으면 다시 만들 수 있다. 없으면 그
시점의 데이터는 영구히 사라진다 — 그래서 ``data/raw/`` 는 절대 삭제하지 않는다.

curated 와 달리 raw 는 store 를 경유하지 않는다. 아직 정규화되지 않아
스키마가 없고, 이중시간 질의의 대상도 아니기 때문이다. 대신 어떤 수집
실행이 무엇을 받아왔는지 추적할 수 있게 ``ingest_run_id`` 로 이름을 짓는다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

RAW = "raw"


@dataclass
class RawArchive:
    root: Path
    _counter: dict[str, int] = field(default_factory=dict, repr=False)

    def directory(self, source: str, observed_at: datetime) -> Path:
        return self.root / RAW / source / f"date={observed_at.date().isoformat()}"

    def save(
        self,
        source: str,
        payload: Any,
        *,
        observed_at: datetime,
        ingest_run_id: str,
        label: str,
    ) -> Path:
        """원본을 그대로 남긴다. 어떤 경우에도 기존 파일을 덮지 않는다.

        순번을 메모리에만 두면 안 된다. run id 가 결정론적이라(백필은
        ``bf-prices-KR-20240110``), 아카이브 직후 죽은 세션을 **새 프로세스**가
        재시도하면 순번이 0000 부터 다시 시작해 먼저 받아 둔 원본을 덮어쓴다.
        정규화 버그를 나중에 발견해도 그 시점 데이터는 영영 복구할 수 없다.

        그래서 디스크에 있는 것을 진실로 본다. 메모리 카운터는 같은 프로세스
        안에서 stat 을 아끼는 용도로만 남긴다.
        """
        target_dir = self.directory(source, observed_at)
        target_dir.mkdir(parents=True, exist_ok=True)

        key = f"{source}/{ingest_run_id}/{label}"
        index = self._counter.get(key, 0)
        path = target_dir / f"{ingest_run_id}-{label}-{index:04d}.json"
        while path.exists():
            index += 1
            path = target_dir / f"{ingest_run_id}-{label}-{index:04d}.json"
        self._counter[key] = index + 1

        path.write_text(
            json.dumps(
                {
                    "source": source,
                    "label": label,
                    "ingest_run_id": ingest_run_id,
                    "observed_at": observed_at.isoformat(),
                    "payload": payload,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return path
