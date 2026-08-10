"""생존편향 — 상장폐지 종목이 과거 유니버스 조회에 남아 있는지.

지금 없는 종목을 과거에서도 지우면 수익률이 뻥튀기된다. 망한 회사를 빼고
계산한 성적표는 성적표가 아니다.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

pytestmark = pytest.mark.invariant

SOURCE = "krx"
SURVIVOR = "KR:005930"
DELISTED = "KR:900110"


@pytest.fixture
def universe(store, ts):  # type: ignore[no-untyped-def]
    """3월 1~3일 스냅샷. 상폐 종목은 3월 3일부터 명단에서 빠진다."""
    rows = []
    for day in (1, 2, 3):
        rows.append(
            {
                "entity_id": SURVIVOR,
                "valid_from": ts(2024, 3, day),
                "observed_at": ts(2024, 3, day, 7),
                "source": SOURCE,
                "market": "KR",
                "name": "삼성전자",
                "is_listed": True,
                "is_tradable": True,
                "delisted_on": None,
            }
        )
        if day < 3:
            rows.append(
                {
                    "entity_id": DELISTED,
                    "valid_from": ts(2024, 3, day),
                    "observed_at": ts(2024, 3, day, 7),
                    "source": SOURCE,
                    "market": "KR",
                    "name": "상폐예정",
                    "is_listed": True,
                    "is_tradable": True,
                    "delisted_on": None,
                }
            )
    rows.append(
        {
            "entity_id": DELISTED,
            "valid_from": ts(2024, 3, 3),
            "observed_at": ts(2024, 3, 3, 7),
            "source": SOURCE,
            "market": "KR",
            "name": "상폐예정",
            "is_listed": False,
            "is_tradable": False,
            "delisted_on": ts(2024, 3, 3),
        }
    )
    store.append("universe", rows, ingest_run_id="krx-snapshots")
    return store


def test_delisted_name_remains_in_past_snapshots(universe, ts) -> None:  # type: ignore[no-untyped-def]
    seen = universe.get("universe", as_of=ts(2024, 3, 2, 12), lookback=timedelta(days=1))

    assert DELISTED in set(seen["entity_id"]), "상폐 종목이 과거 유니버스에서 사라졌다"
    assert set(seen[seen["entity_id"] == DELISTED]["is_listed"]) == {True}


def test_delisting_is_visible_only_after_it_was_observed(universe, ts) -> None:  # type: ignore[no-untyped-def]
    before = universe.get("universe", as_of=ts(2024, 3, 2, 12))
    after = universe.get("universe", as_of=ts(2024, 3, 3, 12))

    assert not any(before[before["entity_id"] == DELISTED]["delisted_on"].notna())
    assert any(after[after["entity_id"] == DELISTED]["delisted_on"].notna())


def test_survivor_is_present_throughout(universe, ts) -> None:  # type: ignore[no-untyped-def]
    for day in (1, 2, 3):
        seen = universe.get("universe", as_of=ts(2024, 3, day, 12))
        assert SURVIVOR in set(seen["entity_id"])
