"""불변식 3 — observed_at 없는 레코드는 저장을 거부한다.

거부는 전부-아니면-전무여야 한다. 절반만 들어간 배치는 append-only 창고에서
되돌릴 수 없다.

store 는 Clock 을 갖지 않는다(불변식 2). 따라서 "관측시각이 미래인가" 는
여기서 판정하지 않는다 — 그건 Clock 을 주입받는 Collector 의 몫이다.
store 가 책임지는 것은 '없거나 모호한 시각을 거부하는 것' 뿐이다.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from quant_rl_trading.store.errors import MissingObservedAt, NaiveTimestamp, SchemaViolation, UnknownTable

pytestmark = pytest.mark.invariant

SOURCE = "test"


def _row(ts, **overrides):  # type: ignore[no-untyped-def]
    row = {
        "entity_id": "KR:005930",
        "valid_from": ts(2024, 3, 4),
        "observed_at": ts(2024, 3, 4, 9),
        "source": SOURCE,
        "market": "KR",
        "close": 100.0,
    }
    row.update(overrides)
    return row


def test_missing_observed_at_is_rejected(store, ts) -> None:  # type: ignore[no-untyped-def]
    row = _row(ts)
    del row["observed_at"]

    with pytest.raises(MissingObservedAt):
        store.append("prices", [row], ingest_run_id="bad-1")


def test_null_observed_at_is_rejected(store, ts) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(MissingObservedAt):
        store.append("prices", [_row(ts, observed_at=None)], ingest_run_id="bad-2")


def test_naive_observed_at_is_rejected(store, ts) -> None:  # type: ignore[no-untyped-def]
    """타임존 없는 시각은 국장/미장 중 어느 쪽 자정인지 알 수 없다."""
    with pytest.raises(NaiveTimestamp):
        store.append(
            "prices", [_row(ts, observed_at=datetime(2024, 3, 4, 9))], ingest_run_id="bad-3"
        )


def test_naive_valid_from_is_rejected(store, ts) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(NaiveTimestamp):
        store.append("prices", [_row(ts, valid_from=datetime(2024, 3, 4))], ingest_run_id="bad-4")


def test_unregistered_column_is_rejected(store, ts) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(SchemaViolation):
        store.append("prices", [_row(ts, mystery=1.0)], ingest_run_id="bad-5")


def test_unknown_table_is_rejected(store, ts) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(UnknownTable):
        store.append("prices_v2", [_row(ts)], ingest_run_id="bad-6")


def test_rejection_leaves_nothing_behind(store, ts) -> None:  # type: ignore[no-untyped-def]
    """한 행이라도 틀리면 배치 전체가 저장되지 않는다."""
    good = _row(ts)
    bad = _row(ts, entity_id="KR:000660")
    del bad["observed_at"]

    with pytest.raises(MissingObservedAt):
        store.append("prices", [good, bad], ingest_run_id="half")

    assert store.get("prices", as_of=ts(2024, 3, 31)).empty
    assert not store.ingest_run_recorded("prices", "half")


def test_accepted_row_is_readable(store, ts) -> None:  # type: ignore[no-untyped-def]
    store.append("prices", [_row(ts)], ingest_run_id="good")

    seen = store.get("prices", as_of=ts(2024, 3, 31))

    assert list(seen["close"]) == [100.0]
    assert list(seen["ingest_run_id"]) == ["good"]
    assert list(seen["revision"]) == [0], "revision 을 생략하면 원본(0)이다"
