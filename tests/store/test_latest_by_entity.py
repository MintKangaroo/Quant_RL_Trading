"""접기(fold)를 창고 안에서 끝내도 **어느 행이 남는지는 같아야 한다.**

``latest_by_entity`` 는 ``get`` 결과를 종목별로 잘라 마지막을 취한 것과 같은
행을 돌려준다고 주장한다. 빠른 대신 조용히 틀릴 수 있는 최적화라, 그 주장을
같은 창고 위에서 직접 대조한다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from quant_rl_trading.store.errors import SchemaViolation

NOW = datetime(2026, 8, 12, tzinfo=UTC)
SESSIONS = [NOW - timedelta(days=offset) for offset in range(30, -1, -1)]


def _row(entity: str, day: datetime, *, listed: bool = True, revision: int = 0):
    return {
        "entity_id": entity, "valid_from": day, "observed_at": day,
        "source": "test", "market": "KR", "name": entity,
        "is_listed": listed, "is_tradable": True, "delisted_on": None,
        "revision": revision,
    }


@pytest.fixture
def seeded(store):  # type: ignore[no-untyped-def]
    rows = []
    for day in SESSIONS:
        rows.append(_row("KR:000100", day))
        if day >= SESSIONS[10]:
            rows.append(_row("KR:000200", day))
        # 중간에 사라진 종목. 마지막 행이 창 안에 있지만 최근은 아니다.
        if day <= SESSIONS[5]:
            rows.append(_row("KR:000300", day, listed=False))
    store.append("universe", rows, ingest_run_id="u-seed")
    return store


def test_get_을_종목별로_잘라_마지막을_취한_것과_같다(seeded) -> None:
    full = seeded.get("universe", as_of=NOW, lookback=40, market="KR")
    expected = (
        full.sort_values("valid_from").groupby("entity_id").tail(1)
        .sort_values("entity_id").reset_index(drop=True)
    )

    folded = seeded.latest_by_entity("universe", as_of=NOW, lookback=40, market="KR")

    assert list(folded["entity_id"]) == list(expected["entity_id"])
    assert list(folded["valid_from"]) == list(expected["valid_from"])
    assert list(folded["is_listed"]) == list(expected["is_listed"])
    # 사라진 종목도 마지막 행으로 남는다 — 창을 줄여 얻는 답과 다른 지점이다.
    assert "KR:000300" in set(folded["entity_id"])


def test_first_valid_from_은_창_안_최초_등장이다(seeded) -> None:
    full = seeded.get("universe", as_of=NOW, lookback=40, market="KR")
    expected = full.groupby("entity_id")["valid_from"].min()

    folded = seeded.latest_by_entity("universe", as_of=NOW, lookback=40, market="KR")
    actual = folded.set_index("entity_id")["first_valid_from"]

    assert actual.to_dict() == expected.to_dict()


def test_정정본이_이긴다(seeded) -> None:
    """마지막 세션에 revision 1 이 오면 그것이 마지막 상태다."""
    seeded.append(
        "universe",
        [_row("KR:000100", SESSIONS[-1], listed=False, revision=1)],
        ingest_run_id="u-fix",
    )

    folded = seeded.latest_by_entity("universe", as_of=NOW, lookback=40, market="KR")
    row = folded.set_index("entity_id").loc["KR:000100"]

    assert not bool(row["is_listed"])


def test_관측_게이트는_그대로다(seeded) -> None:
    """``observed_at <= as_of`` 는 접기와 무관하게 걸린다."""
    earlier = SESSIONS[-5]

    folded = seeded.latest_by_entity("universe", as_of=earlier, lookback=40, market="KR")

    assert max(folded["valid_from"]).to_pydatetime() <= earlier


def test_빈_창고도_같은_스키마를_준다(store) -> None:
    folded = store.latest_by_entity("universe", as_of=NOW, lookback=40, market="KR")

    assert folded.empty
    assert "first_valid_from" in folded.columns


def test_키가_더_긴_테이블은_거부한다(store) -> None:
    """``documents`` 는 한 (종목, valid_from) 에 공시가 여럿이다. 종목당 한
    행으로 접으면 나머지가 조용히 사라지므로 접지 않는다."""
    with pytest.raises(SchemaViolation):
        store.latest_by_entity("documents", as_of=NOW, lookback=40)
