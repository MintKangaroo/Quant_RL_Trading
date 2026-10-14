"""'지금' 조회 짧은 캐시 — 무거운 요약 API 를 잠깐 다시 쓴다 (dashboard.md §9).

화면은 요청마다 그 순간의 시각을 as_of 로 보낸다. 그래서 as_of 를 열쇠에 그대로 넣으면 늘 빗나간다
(`system.table_freshness` 가 같은 이유로 as_of 거리를 따로 본다). 여기서는:

- 열쇠 = (이름, 인자 묶음) — 창고 경로·lookback·시장 등 답을 바꾸는 것 전부
- 맞음 = 나이 < ``ttl`` 이고 요청 as_of 가 캐시한 as_of 와 ``window`` 안

되감기(과거 as_of)는 거리가 멀어 늘 다시 계산한다. 값은 같은 함수의 결과를 그대로 돌려줄 뿐 새로 만들지 않는다.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable
from datetime import datetime
from threading import Lock
from time import monotonic
from typing import Any, TypeVar

T = TypeVar("T")

TTL_SECONDS = 60.0
WINDOW_SECONDS = 120.0
_MAX_ENTRIES = 64

_lock = Lock()
_entries: dict[Hashable, tuple[float, datetime, Any]] = {}


def cached(name: str, key: Hashable, *, as_of: datetime, compute: Callable[[], T],
           ttl: float = TTL_SECONDS, window: float = WINDOW_SECONDS) -> T:
    """``compute()`` 의 결과를 (name, key) 로 잠깐 다시 쓴다 — '지금' 조회끼리만."""
    full = (name, key)
    now = monotonic()
    with _lock:
        hit = _entries.get(full)
        if hit is not None and now - hit[0] < ttl and abs((as_of - hit[1]).total_seconds()) < window:
            return hit[2]  # type: ignore[no-any-return]
    value = compute()
    with _lock:
        if len(_entries) >= _MAX_ENTRIES:
            oldest = min(_entries, key=lambda k: _entries[k][0])
            _entries.pop(oldest, None)
        _entries[full] = (monotonic(), as_of, value)
    return value


def clear() -> None:
    """테스트용."""
    with _lock:
        _entries.clear()


__all__ = ["TTL_SECONDS", "WINDOW_SECONDS", "cached", "clear"]
