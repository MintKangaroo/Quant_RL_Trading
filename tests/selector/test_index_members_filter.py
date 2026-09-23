"""Z2 트랙 — 지수 구성 필터. 켜져 있으면 스냅샷 안만 남기고, 스냅샷이 없으면 조용히 넓히지 않고 비운다."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from quant_rl_trading.selector.filters import _apply_index_members

NOW = datetime(2026, 9, 23, 7, 0, tzinfo=UTC)


def _seed(store, day_offset: int, members: list[str]) -> None:  # type: ignore[no-untyped-def]
    when = NOW - timedelta(days=day_offset)
    store.append("index_members", [
        {"entity_id": e, "valid_from": when, "observed_at": when, "source": "test", "market": "KR", "index_id": "KOSPI200"}
        for e in members
    ], ingest_run_id=f"t-{day_offset}", source="test")


def test_꺼져_있으면_그대로(store) -> None:  # type: ignore[no-untyped-def]
    dropped: dict[str, str] = {}
    assert _apply_index_members(store, ["KR:A", "KR:B"], dropped, as_of=NOW, market="KR", index="") == ["KR:A", "KR:B"]


def test_최신_스냅샷_안만_남긴다(store) -> None:  # type: ignore[no-untyped-def]
    _seed(store, 3, ["KR:A", "KR:B"])
    _seed(store, 1, ["KR:A", "KR:C"])  # B 편출 · C 편입
    dropped: dict[str, str] = {}
    kept = _apply_index_members(store, ["KR:A", "KR:B", "KR:C", "KR:D"], dropped, as_of=NOW, market="KR", index="KOSPI200")
    assert kept == ["KR:A", "KR:C"]
    assert dropped["KR:B"] == "KOSPI200 구성 밖" and dropped["KR:D"] == "KOSPI200 구성 밖"


def test_스냅샷이_없으면_후보를_비운다(store) -> None:  # type: ignore[no-untyped-def]
    dropped: dict[str, str] = {}
    assert _apply_index_members(store, ["KR:A"], dropped, as_of=NOW, market="KR", index="KOSPI200") == []
    assert dropped["KR:A"] == "KOSPI200 구성 스냅샷 없음"
