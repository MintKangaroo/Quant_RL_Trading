"""프로세스 읽기 캐시 — **낡은 값을 보여주지 않는 선에서만 빠르다.**

화면 하나가 API 를 일곱 개 부르고 그것들이 같은 표를 각자 다시 읽는다. 요청
경계에서 버리는 `MemoStore` 는 그 사이를 못 잇는다. 여기서 재는 것은 "빨라
졌나" 가 아니라 **"언제 다시 읽나"** 다 — 그게 틀리면 화면이 조용히 낡는다.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

from quant_rl_trading.store.memo import SharedMemo

NOW = datetime(2026, 9, 17, 4, 0, tzinfo=UTC)


class _Counting:
    """읽기 횟수를 세는 창고 대역. 내용은 중요하지 않다 — 횟수가 중요하다."""

    root = "/tmp/nowhere"

    def __init__(self) -> None:
        self.reads = 0
        self.appends = 0

    def get(self, table: str, **kwargs: object) -> pd.DataFrame:
        self.reads += 1
        return pd.DataFrame({"entity_id": ["KR:005930"], "n": [self.reads]})

    def append(self, table: str, records: object, *, ingest_run_id: str, source: str | None = None) -> int:
        self.appends += 1
        return 1


@pytest.fixture
def ticking():
    """손으로 돌리는 단조시계. 벽시계를 기다리는 테스트는 느리고 흔들린다."""
    holder = {"t": 1000.0}
    return holder, (lambda: holder["t"])


def test_같은_질의는_한_번만_읽는다(ticking) -> None:
    holder, clock = ticking
    inner = _Counting()
    memo = SharedMemo(inner, ttl_seconds=45, budget_bytes=10 << 20, monotonic=clock)

    first = memo.get("prices", as_of=NOW, market="KR")
    second = memo.get("prices", as_of=NOW, market="KR")

    assert inner.reads == 1, "요청이 둘이어도 창고는 한 번만 읽는다"
    assert first.equals(second)
    assert memo.hits == 1 and memo.misses == 1


def test_인자가_다르면_다른_질의다(ticking) -> None:
    holder, clock = ticking
    inner = _Counting()
    memo = SharedMemo(inner, ttl_seconds=45, budget_bytes=10 << 20, monotonic=clock)

    memo.get("prices", as_of=NOW, market="KR")
    memo.get("prices", as_of=NOW, market="US")
    memo.get("prices", as_of=NOW, market="KR", lookback=5)

    assert inner.reads == 3


def test_TTL_이_지나면_다시_읽는다(ticking) -> None:
    """**이것이 이 캐시를 쓸 수 있게 하는 조건이다.** 안 만료되면 화면이 영영 낡는다."""
    holder, clock = ticking
    inner = _Counting()
    memo = SharedMemo(inner, ttl_seconds=45, budget_bytes=10 << 20, monotonic=clock)

    memo.get("prices", as_of=NOW)
    holder["t"] += 44.0
    memo.get("prices", as_of=NOW)
    assert inner.reads == 1, "TTL 안이면 그대로"

    holder["t"] += 2.0
    memo.get("prices", as_of=NOW)
    assert inner.reads == 2, "TTL 이 지나면 창고를 다시 읽는다"


def test_적재하면_그_표의_기억을_버린다(ticking) -> None:
    holder, clock = ticking
    inner = _Counting()
    memo = SharedMemo(inner, ttl_seconds=45, budget_bytes=10 << 20, monotonic=clock)

    memo.get("prices", as_of=NOW)
    memo.get("signals", as_of=NOW)
    memo.append("prices", [], ingest_run_id="r1")

    memo.get("signals", as_of=NOW)
    assert inner.reads == 2, "손대지 않은 표는 그대로 캐시"
    memo.get("prices", as_of=NOW)
    assert inner.reads == 3, "방금 쓴 표는 다시 읽는다"


def test_killswitch_는_캐시하지_않는다(ticking) -> None:
    """다른 프로세스가 건 latch 가 같은 시각의 다음 조회에서 보여야 한다."""
    holder, clock = ticking
    inner = _Counting()
    memo = SharedMemo(inner, ttl_seconds=45, budget_bytes=10 << 20, monotonic=clock)

    memo.get("killswitch", as_of=NOW)
    memo.get("killswitch", as_of=NOW)

    assert inner.reads == 2


def test_예산을_넘으면_오래된_것부터_버린다(ticking) -> None:
    holder, clock = ticking
    inner = _Counting()
    memo = SharedMemo(inner, ttl_seconds=45, budget_bytes=1, monotonic=clock)

    memo.get("prices", as_of=NOW)
    memo.get("prices", as_of=NOW)

    assert inner.reads == 2, "예산이 0에 가까우면 아무것도 안 남는다 — 느려도 안 낡는다"


def test_폭이_0이면_캐시를_끈다(ticking) -> None:
    holder, clock = ticking
    inner = _Counting()
    memo = SharedMemo(inner, ttl_seconds=0, budget_bytes=10 << 20, monotonic=clock)

    memo.get("prices", as_of=NOW)
    memo.get("prices", as_of=NOW)

    assert inner.reads == 2


def test_돌려주는_프레임은_사본이다(ticking) -> None:
    """호출자가 제자리에서 고쳐도 다음 호출자가 오염된 것을 보면 안 된다."""
    holder, clock = ticking
    inner = _Counting()
    memo = SharedMemo(inner, ttl_seconds=45, budget_bytes=10 << 20, monotonic=clock)

    first = memo.get("prices", as_of=NOW)
    first.loc[0, "n"] = -999

    assert memo.get("prices", as_of=NOW).loc[0, "n"] != -999


def test_큰_프레임은_아예_안_들고_있는다(ticking) -> None:
    """조회 비용은 행이 아니라 **파일 수**로 붙는다 — 큰 프레임일수록 행당 싸다.

    들고 있으면 그것이 스왑으로 밀리고 적중이 디스크 읽기가 된다(2026-09-18: 대시보드
    첫 응답 4.9s → 10~14s, 프로세스 스왑 227MB).
    """
    holder, clock = ticking
    inner = _Counting()
    memo = SharedMemo(
        inner, ttl_seconds=45, budget_bytes=10 << 20, entry_bytes=1, monotonic=clock
    )

    memo.get("prices", as_of=NOW)
    memo.get("prices", as_of=NOW)

    assert inner.reads == 2, "상한을 넘는 프레임은 기억하지 않는다"
    assert memo._bytes == 0
