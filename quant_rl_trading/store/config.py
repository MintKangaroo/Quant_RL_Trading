"""임계치 — 이중시간으로 보관한다.

임계치를 평범한 설정 파일에 두면, 오늘 12를 15로 바꾼 순간 작년 백테스트
결과까지 소급해 바뀐다. as_of 로 조회 가능한 테이블에 두면 "그때 그 임계치로
돌린 결과" 가 재현된다. 불변식 10.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from quant_rl_trading.store.errors import ConfigNotFound
from quant_rl_trading.store.tables import CONFIG_TABLE

#: 체크인된 기본값의 관측시각. 어떤 as_of 로 조회해도 보이도록 충분히 과거다.
DEFAULTS_EPOCH = datetime(2000, 1, 1, tzinfo=UTC)

DEFAULTS_SOURCE = "config-defaults"

#: 설정 판번호. 값이 바뀔 때마다 올린다 — "이 성과가 어느 설정에서 나왔나" 를
#: 나중에 추적하려면 성과와 함께 기록될 무언가가 있어야 한다.
VERSION_KEY = "config_version"


def flatten(path: Path) -> dict[str, str]:
    """YAML 을 ``section.key -> value_json`` 으로 편다.

    저장은 평평하게 한다. 섹션째로 한 행에 넣으면 값 하나를 바꿔도 섹션 전체가
    새 revision 이 되고, 무엇이 바뀌었는지 이력에서 읽어낼 수 없다.
    읽을 때만 ``section()`` 으로 다시 묶는다.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    flat: dict[str, str] = {}

    def walk(prefix: str, node: dict[str, Any]) -> None:
        for key, value in node.items():
            name = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                walk(name, value)
            else:
                # 리스트(exclude_flags 등)는 통째로 한 값이다.
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
        if _deployment_local(value_json, previous):
            # **창고가 정본이다.** yaml 의 `""` 로 덮으면 실계좌 지문이 지워진다
            # (`EMPTY` 주석 참고). 시딩이 이 키를 만지지 않는다.
            continue
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


#: yaml 의 빈 문자열은 **"여기서 정하지 않는다"** 는 뜻이다. 값의 출처가
#: 배포마다 다르고 저장소에 올리면 안 되는 것들이 여기 해당한다 — 지금은
#: ``execution.live_account_fingerprint`` 하나다(sha256(appkey)[:12]).
#:
#: **이걸 안 가르면 시딩이 실계좌 지문을 지운다.** yaml 이 ``""`` 이고 창고에
#: 값이 있으면 `changed_names` 가 "바뀌었다" 고 보고, ``effective_at`` 을 줘서
#: 밀면 창고 값이 ``""`` 로 덮인다. 그 지문은 "모의인 줄 알았다" 를 막는
#: 유일한 장치라(`tools/verify_live_order.py`), 지워지면 계좌 확인이 무력해진다.
#:
#: 2026-08-15 에 실제로 한 번 덮었고(복구했다), 같은 날 다른 작업이 또 밟을
#: 뻔했다. 사람이 매번 조심하는 것으로는 못 막는다.
EMPTY = '""'


def _deployment_local(value_json: str, current: tuple[str, int] | None) -> bool:
    """yaml 이 비었는데 창고에 값이 있으면 **창고가 정본이다.**"""
    return value_json == EMPTY and current is not None and current[0] != EMPTY


def changed_names(path: Path, current: dict[str, tuple[str, int]]) -> set[str]:
    """파일과 창고의 값이 어긋난 설정 이름. **배포 지역값은 세지 않는다.**"""
    return {
        name
        for name, value_json in flatten(path).items()
        if name in current
        and current[name][0] != value_json
        and not _deployment_local(value_json, current.get(name))
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


def read_section(frame: Any, name: str, as_of: datetime) -> dict[str, Any]:
    """섹션 하나를 dict 로. ``config("reward")`` 가 이걸 쓴다.

    ``reward.w_free`` 를 하나씩 읽으면 호출부가 키 이름을 알아야 하고, 키가
    늘 때마다 호출부를 고쳐야 한다. 보상 함수처럼 값 열 개를 한꺼번에 쓰는
    곳은 섹션째 받는 편이 낫다.
    """
    prefix = f"{name}."
    if frame.empty:
        raise ConfigNotFound(f"{as_of.isoformat()} 시점에 발효된 설정 섹션 {name!r} 이 없다")

    effective = frame[frame["valid_from"] <= as_of].sort_values("valid_from")
    if effective.empty:
        raise ConfigNotFound(f"{as_of.isoformat()} 시점에 발효된 설정 섹션 {name!r} 이 없다")

    out: dict[str, Any] = {}
    for row in effective.to_dict(orient="records"):
        entity = str(row["entity_id"])
        if entity.startswith(prefix):
            # 정렬이 오래된 것부터라, 뒤에 오는 최신 발효값이 앞을 덮는다.
            out[entity[len(prefix) :]] = json.loads(row["value_json"])
    if not out:
        raise ConfigNotFound(f"{as_of.isoformat()} 시점에 발효된 설정 섹션 {name!r} 이 없다")
    return out


def resolve(frame: Any, name: str, as_of: datetime) -> Any:
    """``config`` 표 **전체**에서 이름 하나를 푼다. 값 하나거나 섹션이다.

    ``Store.config`` 와 ``MemoStore.config`` 가 같은 규칙을 쓰게 한 벌만 둔다.
    둘로 두면 캐시를 씌운 쪽만 다르게 풀리는 사고가 난다.

    표를 이름으로 안 좁히고 통째로 받는 이유는 ``Store.config`` 의 주석에
    적었다 — 조회 고정비가 이름 수만큼 곱해지는 것을 막고, 인자가 같은
    조회 하나로 통일해 요청 캐시가 걸리게 하기 위함이다.
    """
    exact = frame[frame["entity_id"] == name] if not frame.empty else frame
    if "." in name or not exact.empty:
        # 점이 있거나, 그 이름의 값이 실제로 있으면 값 하나다.
        # ``config_version`` 처럼 섹션에 속하지 않는 최상위 값이 여기 걸린다.
        return read_value(exact, name, as_of)
    return read_section(frame, name, as_of)


__all__ = [
    "CONFIG_TABLE",
    "DEFAULTS_EPOCH",
    "VERSION_KEY",
    "defaults_rows",
    "defaults_run_id",
    "read_section",
    "read_value",
    "resolve",
]
