"""정정공시 — 정정 전/후 시점 조회가 서로 다른 값을 주는지.

DART 재무는 정정된다. 정정본이 과거 조회까지 덮어쓰면, 백테스트는 그때
아무도 몰랐던 숫자로 매매하게 된다. append-only 와 revision 이 이걸 막는다.
"""

from __future__ import annotations

import pytest

from quant_rl_trading.store.errors import DuplicateIngestRun

pytestmark = pytest.mark.invariant

SOURCE = "dart"


def _fact(ts, *, revision: int, value: float, observed_day: int):  # type: ignore[no-untyped-def]
    return {
        "entity_id": "KR:005930",
        "valid_from": ts(2024, 2, 14),
        "observed_at": ts(2024, 3, observed_day, 8),
        "source": SOURCE,
        "revision": revision,
        "metric": "net_income",
        "value": value,
        "fiscal_period": "2023Q4",
        "report_type": "annual",
    }


@pytest.fixture
def restated(store, ts):  # type: ignore[no-untyped-def]
    store.append(
        "fundamentals",
        [_fact(ts, revision=0, value=100.0, observed_day=2)],
        ingest_run_id="orig",
    )
    store.append(
        "fundamentals",
        [_fact(ts, revision=1, value=180.0, observed_day=20)],
        ingest_run_id="fix",
    )
    return store


def test_before_restatement_sees_the_original(restated, ts) -> None:  # type: ignore[no-untyped-def]
    seen = restated.get("fundamentals", as_of=ts(2024, 3, 10))

    assert list(seen["value"]) == [100.0]
    assert list(seen["revision"]) == [0]


def test_after_restatement_sees_the_correction(restated, ts) -> None:  # type: ignore[no-untyped-def]
    seen = restated.get("fundamentals", as_of=ts(2024, 3, 25))

    assert list(seen["value"]) == [180.0]
    assert list(seen["revision"]) == [1]


def test_original_row_is_never_removed(restated, ts) -> None:  # type: ignore[no-untyped-def]
    """정정은 삭제가 아니라 새 행이다. 원본은 그대로 남아 있어야 한다."""
    assert list(restated.get("fundamentals", as_of=ts(2024, 3, 10))["value"]) == [100.0]
    assert list(restated.get("fundamentals", as_of=ts(2024, 3, 25))["value"]) == [180.0]
    assert list(restated.get("fundamentals", as_of=ts(2024, 3, 10))["value"]) == [100.0]


def test_only_the_latest_revision_survives_per_key(restated, ts) -> None:  # type: ignore[no-untyped-def]
    seen = restated.get("fundamentals", as_of=ts(2024, 4, 1))

    assert len(seen) == 1, "같은 자연키에서 두 revision 이 동시에 살아남았다"


def test_repeated_ingest_run_is_rejected(store, ts) -> None:  # type: ignore[no-untyped-def]
    """append-only 창고에서 중복은 사후 정정이 불가능하다. 사전에 막는다."""
    store.append(
        "fundamentals", [_fact(ts, revision=0, value=100.0, observed_day=2)], ingest_run_id="once"
    )

    with pytest.raises(DuplicateIngestRun):
        store.append(
            "fundamentals",
            [_fact(ts, revision=0, value=100.0, observed_day=2)],
            ingest_run_id="once",
        )

    assert len(store.get("fundamentals", as_of=ts(2024, 4, 1))) == 1
