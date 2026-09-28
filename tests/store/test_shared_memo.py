"""프로세스 읽기 캐시 — **낡은 값을 보여주지 않는 선에서만 빠르다.**

화면 하나가 API 를 일곱 개 부르고 그것들이 같은 표를 각자 다시 읽는다. 요청
경계에서 버리는 `MemoStore` 는 그 사이를 못 잇는다. 여기서 재는 것은 "빨라
졌나" 가 아니라 **"언제 다시 읽나"** 다 — 그게 틀리면 화면이 조용히 낡는다.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime

import pandas as pd
import pytest

from quant_rl_trading.store.memo import SharedMemo, derived

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


# -- 진행 중인 같은 질의에 올라타기 (2026-09-29) ------------------------------------


class _Slow(_Counting):
    """첫 읽기를 붙잡아 두는 창고 대역. 두 스레드가 확실히 겹치게 한다."""

    def __init__(self, *, fail_first: bool = False) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()
        self.fail_first = fail_first

    def get(self, table: str, **kwargs: object) -> pd.DataFrame:
        self.reads += 1
        if self.reads == 1:
            self.entered.set()
            assert self.release.wait(5)
            if self.fail_first:
                raise OSError("창고가 잠깐 안 열렸다")
        return pd.DataFrame({"entity_id": ["KR:005930"], "n": [self.reads]})


def _race(memo: SharedMemo, inner: _Slow) -> tuple[list[pd.DataFrame], list[OSError]]:
    """앞 스레드가 창고 안에 있는 동안 뒤 스레드가 같은 질의를 한다."""
    results: list[pd.DataFrame] = []
    errors: list[OSError] = []

    def call() -> None:
        try:
            results.append(memo.get("signals", as_of=NOW, lookback=14))
        except OSError as error:
            errors.append(error)

    leader = threading.Thread(target=call)
    leader.start()
    assert inner.entered.wait(5)
    follower = threading.Thread(target=call)
    follower.start()
    # 뒤 스레드가 기다리기 시작할 틈을 준다. 늦게 들어와도 판정은 읽기 횟수로 한다 —
    # 이 틈이 모자라면 테스트가 실패하지 통과로 속지는 않는다.
    time.sleep(0.2)
    inner.release.set()
    leader.join(5)
    follower.join(5)
    return results, errors


def test_동시에_온_같은_질의는_한_번만_읽는다(ticking) -> None:
    """화면은 패널 API 를 동시에 부른다. 큰 프레임은 기억 상한에 걸려 못 들고 있으므로,
    겹친 두 요청이 창고를 두 번 열지 않게 하는 것은 이 올라타기뿐이다."""
    _, clock = ticking
    inner = _Slow()
    # 기억 상한 1바이트 — 어떤 프레임도 안 들고 있는다. 올라타기는 상한과 무관해야 한다.
    memo = SharedMemo(inner, ttl_seconds=45, budget_bytes=1, entry_bytes=1, monotonic=clock)

    results, errors = _race(memo, inner)

    assert not errors
    assert inner.reads == 1, "겹친 같은 질의는 창고를 한 번만 연다"
    assert len(results) == 2 and results[0].equals(results[1])
    assert results[0] is not results[1], "사본을 나눠 준다 — 한쪽이 고쳐도 다른 쪽은 멀쩡해야 한다"
    assert memo.joined == 1
    assert not memo._inflight, "끝난 읽기는 곧바로 놓는다"

    # 끝난 뒤에 온 요청은 올라타지 않는다(상한 때문에 기억에도 없다) — 다시 읽는다.
    memo.get("signals", as_of=NOW, lookback=14)
    assert inner.reads == 2


def test_앞_읽기가_실패하면_뒤_요청은_직접_읽는다(ticking) -> None:
    _, clock = ticking
    inner = _Slow(fail_first=True)
    memo = SharedMemo(inner, ttl_seconds=45, budget_bytes=10 << 20, monotonic=clock)

    results, errors = _race(memo, inner)

    assert len(errors) == 1, "앞 스레드는 자기 예외를 받는다"
    assert len(results) == 1, "뒤 스레드는 남의 예외를 떠안지 않고 직접 읽는다"
    assert inner.reads == 2
    assert not memo._inflight


def test_큰_프레임은_깊이_재지_않고_거른다(ticking) -> None:
    """얕은 크기만으로 상한을 넘으면 기억하지 않는다 — 결론은 깊이 잰 것과 같다."""
    _, clock = ticking

    class _Wide(_Counting):
        def get(self, table: str, **kwargs: object) -> pd.DataFrame:
            self.reads += 1
            return pd.DataFrame({"entity_id": ["KR:005930"] * 10_000, "n": range(10_000)})

    inner = _Wide()
    memo = SharedMemo(
        inner, ttl_seconds=45, budget_bytes=10 << 20, entry_bytes=1024, monotonic=clock
    )
    memo.get("signals", as_of=NOW)
    memo.get("signals", as_of=NOW)
    assert inner.reads == 2, "상한을 넘는 프레임은 들고 있지 않는다"


# -- 작은 집계 기억 (derived, 2026-09-29) ---------------------------------------------


def test_집계는_TTL_동안_한_번만_계산한다(ticking) -> None:
    """요약 카드와 패널이 차례로 같은 집계를 낸다 — 큰 프레임은 못 들고 있어도 결과는 든다."""
    holder, clock = ticking
    memo = SharedMemo(
        _Counting(), ttl_seconds=45, budget_bytes=1, entry_bytes=1, monotonic=clock
    )
    calls: list[int] = []

    def compute() -> dict[str, object]:
        calls.append(1)
        return {"total": 3, "rows": [{"n": 1}]}

    first = derived(memo, ("signal_activity", NOW, 14), compute)
    first["rows"][0]["n"] = 99  # type: ignore[index]  # 호출자가 고쳐도
    second = derived(memo, ("signal_activity", NOW, 14), compute)
    assert len(calls) == 1
    assert second == {"total": 3, "rows": [{"n": 1}]}, "깊은 사본이라 기억은 오염되지 않는다"

    derived(memo, ("signal_activity", NOW, 7), compute)
    assert len(calls) == 2, "키가 다르면 다른 집계다"

    holder["t"] += 46.0
    derived(memo, ("signal_activity", NOW, 14), compute)
    assert len(calls) == 3, "TTL 이 지나면 다시 계산한다 — 화면이 낡지 않게"


def test_적재가_오면_집계_기억을_버린다(ticking) -> None:
    _, clock = ticking
    memo = SharedMemo(_Counting(), ttl_seconds=45, budget_bytes=10 << 20, monotonic=clock)
    calls: list[int] = []

    def compute() -> int:
        calls.append(1)
        return len(calls)

    assert derived(memo, ("k",), compute) == 1
    memo.append("signals", [], ingest_run_id="t")
    assert derived(memo, ("k",), compute) == 2


def test_캐시_없는_창고에서는_그냥_계산한다() -> None:
    """테스트·세션의 맨 Store 에는 기억이 없다 — 답은 캐시 유무와 무관해야 한다."""
    calls: list[int] = []

    def compute() -> str:
        calls.append(1)
        return "x"

    assert derived(_Counting(), ("k",), compute) == "x"
    assert derived(_Counting(), ("k",), compute) == "x"
    assert len(calls) == 2


def test_요청_캐시를_거쳐도_프로세스_캐시에_닿는다(ticking) -> None:
    """대시보드는 요청마다 MemoStore(SharedMemo(Store)) 를 쓴다."""
    from quant_rl_trading.store.memo import MemoStore

    _, clock = ticking
    shared = SharedMemo(_Counting(), ttl_seconds=45, budget_bytes=10 << 20, monotonic=clock)
    calls: list[int] = []

    def compute() -> int:
        calls.append(1)
        return 1

    derived(MemoStore(shared), ("k",), compute)  # type: ignore[arg-type]
    derived(MemoStore(shared), ("k",), compute)  # type: ignore[arg-type]
    assert len(calls) == 1, "요청이 달라도(MemoStore 가 새것이어도) 한 번만 계산한다"
