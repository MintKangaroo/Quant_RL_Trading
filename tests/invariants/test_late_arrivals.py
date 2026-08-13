"""늦게 도착한 정정본 — 게이트가 보는 것과 화면이 보는 것이 같아야 한다.

M1 감사에서 실제로 잡힌 두 위반의 회귀 테스트다.

1. 창 단위 집계가 **창마다 as_of 를 옮기면** 늦게 도착한 정정본이 어느 창에도
   안 걸린다. 자기 창에서는 아직 관측되지 않았고, 다음 창에서는 valid_from
   하한 아래다. 그러면 게이트는 정정본을 보는데 화면만 정정 전 값을 그린다 —
   사람이 판단하는 화면이 조용히 틀린 숫자를 낸다.

2. 원본 아카이브의 순번이 메모리에만 있으면, 죽은 세션을 새 프로세스가
   재시도할 때 먼저 받아 둔 원본을 덮어쓴다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from quant_rl_trading.collectors.raw import RawArchive
from quant_rl_trading.dashboard.services import data_quality as dq

pytestmark = pytest.mark.invariant

SESSION = datetime(2024, 1, 10, tzinfo=UTC)
LATER = datetime(2024, 6, 1, tzinfo=UTC)


def _price(observed_at: datetime, close: float | None, revision: int = 0) -> dict[str, object]:
    return {
        "entity_id": "KR:000100",
        "valid_from": SESSION,
        "observed_at": observed_at,
        "source": "krx",
        "revision": revision,
        "market": "KR",
        "open": 1.0, "high": 1.0, "low": 1.0, "close": close,
        "volume": 1.0, "value": None, "adj_factor": None,
    }


@pytest.fixture
def restated(store):  # type: ignore[no-untyped-def]
    """세션 당일엔 결측, 60일 뒤에 정정본이 도착한다."""
    store.seed_config_defaults()
    store.append("prices", [_price(SESSION + timedelta(hours=7), None)], ingest_run_id="orig")
    store.append(
        "prices",
        [_price(SESSION + timedelta(days=60), 100.0, revision=1)],
        ingest_run_id="fix",
    )
    return store


def test_windowed_aggregation_sees_late_restatements(restated) -> None:
    """화면 집계가 게이트와 같은 답을 내야 한다."""
    gate = restated.get("prices", as_of=LATER, lookback=200)
    assert list(gate["close"]) == [100.0], "게이트 자체가 정정본을 못 보면 전제가 틀렸다"

    screen = dq.missing_series(restated, as_of=LATER, lookback=200)
    assert screen["close_rate"] == 0.0


def test_restatement_is_invisible_before_it_arrived(restated) -> None:
    """정정 전 시점에는 정정 전 값이 보여야 한다. 그게 이중시간의 요점이다."""
    early = SESSION + timedelta(days=10)

    assert dq.missing_series(restated, as_of=early, lookback=200)["close_rate"] == 1.0


def test_window_boundaries_do_not_drop_sessions(store) -> None:
    """창 경계에 걸친 세션이 사라지지 않는다."""
    store.seed_config_defaults()
    days = [SESSION + timedelta(days=offset) for offset in range(0, 120, 3)]
    store.append(
        "prices",
        [
            {**_price(day + timedelta(hours=7), 10.0), "valid_from": day}
            for day in days
        ],
        ingest_run_id="spread",
    )

    covered = dq.collect_coverage(store, as_of=LATER + timedelta(days=90), lookback=300)

    assert len(covered.rows) == len(days)
    assert covered.total == len(days), "창이 겹치는 구간을 두 번 세면 안 된다"


def test_raw_archive_never_overwrites_across_processes(tmp_path) -> None:
    """run id 가 결정론적이라, 재시도가 먼저 받아 둔 원본을 덮으면 안 된다."""
    observed = LATER
    first = RawArchive(root=tmp_path)
    first.save("krx", {"v": "첫 수집"}, observed_at=observed, ingest_run_id="bf-x", label="s")

    # 프로세스가 죽고 새로 뜬 상황. 메모리 카운터는 비어 있다.
    second = RawArchive(root=tmp_path)
    second.save("krx", {"v": "재시도"}, observed_at=observed, ingest_run_id="bf-x", label="s")

    saved = sorted(tmp_path.rglob("*.json"))
    assert len(saved) == 2
    assert "첫 수집" in saved[0].read_text(encoding="utf-8")
    assert "재시도" in saved[1].read_text(encoding="utf-8")
