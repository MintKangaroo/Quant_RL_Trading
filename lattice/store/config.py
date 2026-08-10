"""임계치 — 이중시간으로 보관한다.

임계치를 평범한 설정 파일에 두면, 오늘 12를 15로 바꾼 순간 작년 백테스트
결과까지 소급해 바뀐다. as_of 로 조회 가능한 테이블에 두면 "그때 그 임계치로
돌린 결과" 가 재현된다. 불변식 10.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lattice.store.errors import ConfigNotFound
from lattice.store.tables import CONFIG_TABLE

#: 체크인된 기본값의 관측시각. 어떤 as_of 로 조회해도 보이도록 충분히 과거다.
DEFAULTS_EPOCH = datetime(2000, 1, 1, tzinfo=UTC)

DEFAULTS_SOURCE = "config-defaults"


def defaults_rows(path: Path) -> list[dict[str, Any]]:
    """TOML 기본값을 config 테이블 행으로 편다.

    ``[section] key = value`` 는 ``section.key`` 라는 이름이 된다.
    """
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []

    def walk(prefix: str, node: dict[str, Any]) -> None:
        for key, value in node.items():
            name = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                walk(name, value)
            else:
                rows.append(
                    {
                        "entity_id": name,
                        "valid_from": DEFAULTS_EPOCH,
                        "observed_at": DEFAULTS_EPOCH,
                        "source": DEFAULTS_SOURCE,
                        "value_json": json.dumps(value, ensure_ascii=False, sort_keys=True),
                    }
                )

    walk("", raw)
    return sorted(rows, key=lambda row: str(row["entity_id"]))


def defaults_run_id(path: Path) -> str:
    """파일 내용으로 결정되는 run id. 같은 파일을 두 번 시딩하면 거부된다."""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    return f"{DEFAULTS_SOURCE}-{digest}"


def read_value(frame: Any, name: str, as_of: datetime) -> Any:
    """as_of 시점에 **발효돼 있던** 값.

    게이트가 걸러주는 것은 ``observed_at`` 이다. 설정에는 그것만으로 부족하다 —
    "다음 달부터 임계치를 올린다" 를 오늘 기록해 두면 관측은 오늘이지만
    발효는 다음 달이다. 그래서 ``valid_from <= as_of`` 를 한 번 더 건다.
    """
    if not frame.empty:
        effective = frame[frame["valid_from"] <= as_of].sort_values("valid_from")
        if not effective.empty:
            return json.loads(effective.iloc[-1]["value_json"])
    raise ConfigNotFound(f"{as_of.isoformat()} 시점에 발효된 설정 {name!r} 이 없다")


__all__ = [
    "CONFIG_TABLE",
    "DEFAULTS_EPOCH",
    "defaults_rows",
    "defaults_run_id",
    "read_value",
]
