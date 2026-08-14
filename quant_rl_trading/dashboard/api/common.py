"""API 규약 — 한 번만 정의하고 모든 엔드포인트가 이걸 통과한다.

**모든 엔드포인트는 ``as_of`` 를 받는다. 예외 없음** (불변식 9). 하나라도 빠지면
타임머신이 깨지고, 그 사실은 화면이 아니라 몇 달 뒤 이상한 백테스트 결과로
드러난다. 규약을 화면마다 다시 쓰지 않게 여기 몰아 둔다 — 이 화면이 앞으로
만들 8개 화면의 원본이다.

``as_of`` 가 없으면 라이브다. 있으면 그 시점으로 창고를 되감는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from flask import current_app, g, has_request_context, jsonify, request
from werkzeug.exceptions import BadRequest

from quant_rl_trading.replay.clock import Clock
from quant_rl_trading.store import Store
from quant_rl_trading.store.memo import MemoStore

#: 임계치 이름 → config 키. 화면도 API 도 숫자를 직접 들지 않는다 (불변식 10).
THRESHOLD_KEYS = {
    "coverage_warn": "data_quality.coverage_warn",
    "missing_warn": "data_quality.missing_warn",
    "latency_p90_warn_ms": "data_quality.latency_p90_warn_ms",
    "default_lookback_days": "data_quality.default_lookback_days",
    "max_lookback_days": "data_quality.max_lookback_days",
    "failure_rows": "data_quality.failure_rows",
}


@dataclass(frozen=True)
class RequestScope:
    """한 요청이 보는 시점과 창. 핸들러는 이것만 있으면 된다."""

    as_of: datetime
    lookback: int
    thresholds: dict[str, Any]
    live: bool


def store() -> Store:
    """요청 하나가 쓰고 버리는 읽기 캐시를 씌워 돌려준다.

    한 화면이 서비스 함수 여럿을 부르고, 그것들이 **같은 as_of·같은 창**으로
    같은 테이블을 각자 조회한다. 실측 — /api/trading 한 번에 창고 조회 61회,
    그중 서로 다른 질의는 39개였다. 스물두 번은 방금 읽은 것을 다시 읽는다.

    캐시를 Store 자체에 붙이지 않는 이유는 memo.py 에 적힌 것과 같다: 대시보드
    프로세스는 며칠씩 떠 있고, 거기에 캐시가 붙으면 **화면이 낡은 데이터를
    계속 보여준다**. 요청 경계에서 버리면 그 위험이 없다 — 다음 요청은 다시
    창고를 읽는다.
    """
    inner: Store = current_app.config["QUANT_RL_STORE"]
    if not has_request_context():
        return inner
    cached = g.get("quant_rl_store")
    if cached is None:
        cached = MemoStore(inner)
        g.quant_rl_store = cached
    return cached  # type: ignore[no-any-return]


def clock() -> Clock:
    return current_app.config["QUANT_RL_CLOCK"]  # type: ignore[no-any-return]


def _repair_plus(raw: str) -> str:
    """쿼리스트링에서 ``+`` 가 공백으로 풀린 것을 되돌린다.

    ``?as_of=2023-06-15T16:01:00+09:00`` 을 인코딩 없이 보내면 서버는 ``+`` 를
    공백으로 읽는다. 브라우저는 인코딩하지만 curl 은 하지 않는다. ISO8601 에
    공백 오프셋은 없으므로 되돌려도 다른 뜻이 될 여지가 없다 — 여기서 관대한
    쪽이 낫다. 대신 **타임존 부재는 여전히 거부한다.** 그건 관대해질 문제가
    아니라 하루가 밀리는 문제다.
    """
    return re.sub(r" (\d{2}:\d{2})$", r"+\1", raw.strip())


def parse_as_of(raw: str | None, *, now: datetime) -> tuple[datetime, bool]:
    """``as_of`` 를 시각으로. 타임존 없는 값은 거부한다.

    국장·미장을 한 시스템에서 돌리는 이상 naive 시각은 어느 쪽 자정인지 알 수
    없다. 추측하면 하루가 밀리고, 밀린 하루는 미래를 보는 하루가 된다.
    """
    if not raw:
        return now, True
    try:
        parsed = datetime.fromisoformat(_repair_plus(raw))
    except ValueError:
        raise BadRequest(f"as_of 를 읽을 수 없다: {raw!r}. ISO8601 이어야 한다") from None
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        raise BadRequest(f"as_of 에 타임존이 없다: {raw!r}. 오프셋을 명시할 것")
    return parsed, False


def parse_lookback(raw: str | None, *, thresholds: dict[str, Any]) -> int:
    default = int(thresholds["default_lookback_days"])
    ceiling = int(thresholds["max_lookback_days"])
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise BadRequest(f"lookback 은 정수여야 한다: {raw!r}") from None
    if value < 1:
        raise BadRequest("lookback 은 1 이상이어야 한다")
    if value > ceiling:
        # 화면 하나가 창고를 통째로 메모리에 올리는 것을 막는다.
        raise BadRequest(f"lookback 상한은 {ceiling}일이다 (요청 {value}일)")
    return value


def read_thresholds(as_of: datetime) -> dict[str, Any]:
    current = store()
    return {name: current.config(key, as_of=as_of) for name, key in THRESHOLD_KEYS.items()}


def scope() -> RequestScope:
    """요청 파라미터 → RequestScope.

    임계치를 as_of 시점 기준으로 읽는다. 오늘 임계치를 바꿨다고 작년 화면이
    소급해 바뀌면 재현이 불가능해진다.
    """
    as_of, live = parse_as_of(request.args.get("as_of"), now=clock().now())
    thresholds = read_thresholds(as_of)
    return RequestScope(
        as_of=as_of,
        lookback=parse_lookback(request.args.get("lookback"), thresholds=thresholds),
        thresholds=thresholds,
        live=live,
    )


def envelope(current: RequestScope, data: Any) -> Any:
    """응답 봉투. ``as_of`` 를 되돌려주는 것이 규약이다 (dashboard.md §2).

    화면이 "이 숫자는 언제 기준인가" 를 항상 표시할 수 있어야 한다. 언제
    기준인지 모르는 숫자는 쓸모없다.
    """
    return jsonify(
        {
            "as_of": current.as_of.isoformat(),
            "live": current.live,
            "lookback_days": current.lookback,
            "thresholds": current.thresholds,
            "data": data,
        }
    )
