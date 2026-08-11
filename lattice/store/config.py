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


def flatten(path: Path) -> dict[str, str]:
    """TOML 을 ``section.key -> value_json`` 으로 편다."""
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    flat: dict[str, str] = {}

    def walk(prefix: str, node: dict[str, Any]) -> None:
        for key, value in node.items():
            name = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                walk(name, value)
            else:
                flat[name] = json.dumps(value, ensure_ascii=False, sort_keys=True)

    walk("", raw)
    return dict(sorted(flat.items()))


def defaults_rows(
    path: Path,
    *,
    current: dict[str, tuple[str, int]] | None = None,
    effective_at: datetime | None = None,
) -> list[dict[str, Any]]:
    """TOML 기본값을 config 테이블 행으로 편다.

    **첫 시딩**은 ``DEFAULTS_EPOCH`` 로 들어간다. 어떤 as_of 로 조회해도 보여야
    하기 때문이다.

    **값을 바꿔 다시 시딩할 때**는 그렇게 하면 안 된다. 같은 자연키·같은
    revision·같은 observed_at 인 행이 두 개가 되고, 승자는 최신값이 아니라
    ``row_hash`` 가 작은 쪽이 된다 (reader.py 의 타이브레이커). 편집한 값이
    조용히 무시되거나, 무시되지 않으면 이번엔 **과거 as_of 조회까지 소급해
    바뀐다** — 작년 백테스트를 재현할 수 없게 된다.

    그래서 바뀐 값만 ``effective_at`` 시점의 정정본(revision+1)으로 넣는다.
    과거 조회는 옛 값을 그대로 본다. 이것이 이 테이블을 이중시간으로 둔 이유다.
    """
    flat = flatten(path)
    known = current or {}
    moment = effective_at or DEFAULTS_EPOCH
    rows: list[dict[str, Any]] = []

    for name, value_json in flat.items():
        previous = known.get(name)
        if previous is None:
            rows.append(_row(name, value_json, DEFAULTS_EPOCH, 0))
        elif previous[0] != value_json:
            rows.append(_row(name, value_json, moment, previous[1] + 1))
        # 값이 그대로면 아무것도 쓰지 않는다. 같은 사실을 두 번 적지 않는다.
    return rows


def _row(name: str, value_json: str, moment: datetime, revision: int) -> dict[str, Any]:
    return {
        "entity_id": name,
        "valid_from": moment,
        "observed_at": moment,
        "source": DEFAULTS_SOURCE,
        "revision": revision,
        "value_json": value_json,
    }


def changed_names(path: Path, current: dict[str, tuple[str, int]]) -> set[str]:
    """파일과 창고의 값이 어긋난 설정 이름."""
    return {
        name
        for name, value_json in flatten(path).items()
        if name in current and current[name][0] != value_json
    }


def current_values(frame: Any) -> dict[str, tuple[str, int]]:
    """지금 창고에 들어 있는 설정. ``이름 -> (값, revision)``."""
    if frame.empty:
        return {}
    latest = frame.sort_values(["valid_from", "revision"]).groupby("entity_id").tail(1)
    return {
        str(row["entity_id"]): (str(row["value_json"]), int(row["revision"]))
        for row in latest.to_dict(orient="records")
    }


def defaults_run_id(path: Path, *, moment: datetime | None = None) -> str:
    """파일 내용 + 발효 시점으로 결정되는 run id.

    내용만으로 정하면, 값을 A→B→A 로 되돌렸을 때 첫 A 의 run id 와 부딪혀
    되돌리기가 거부된다. 되돌리는 것은 정상적인 운영 행위다.
    """
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    if moment is not None:
        digest = f"{digest}-{moment:%Y%m%dT%H%M%S%f}"
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
