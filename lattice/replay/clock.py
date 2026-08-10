"""Clock — 시간의 유일한 출처.

백테스트와 라이브는 같은 코드를 쓴다. Clock 만 바꿔 낀다.
``if backtest:`` 같은 분기를 만드는 순간 두 코드는 갈라지고 백테스트는
거짓말이 된다 (불변식 5).

레포 전체에서 벽시계를 읽는 지점은 이 파일의 ``LiveClock.now`` 한 줄뿐이다.
tools/invariant_guard.py 가 나머지 전부를 거부한다 (불변식 2).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable

from lattice.store.errors import NaiveTimestamp


def require_aware(name: str, moment: datetime) -> datetime:
    """타임존 없는 시각을 거부한다.

    국장·미장을 한 시스템에서 돌리는 이상, naive 시각은 어느 쪽 자정인지
    알 수 없다. 추측하지 않고 거부한다.
    """
    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        raise NaiveTimestamp(f"{name} 에 타임존이 없다: {moment!r}")
    return moment


@runtime_checkable
class Clock(Protocol):
    """지금이 언제인지 아는 것. 그것만 한다."""

    def now(self) -> datetime: ...


class LiveClock:
    """실제 시각. 운영에서만 쓴다."""

    def now(self) -> datetime:
        return datetime.now(UTC)  # invariant-allow: wallclock

    def __repr__(self) -> str:
        return "LiveClock()"


class ReplayClock:
    """지정된 과거 시각에 멈춰 있는 시계.

    ``advance`` 로만 움직인다. 스스로 흐르지 않는다 — 리플레이 도중 시간이
    저절로 가면 같은 as_of 로 두 번 돌린 결과가 달라진다.
    """

    def __init__(self, as_of: datetime) -> None:
        self._now = require_aware("as_of", as_of)

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> datetime:
        if delta < timedelta(0):
            raise ValueError("시계는 뒤로 가지 않는다")
        self._now = self._now + delta
        return self._now

    def __repr__(self) -> str:
        return f"ReplayClock({self._now.isoformat()})"
