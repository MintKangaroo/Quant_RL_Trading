"""파티션 프루닝 — **빨라지되 행이 사라지면 안 된다.**

``observation_lag_days`` 를 선언하면 게이트가 ``observed_date`` 파티션의 하한을
잘라낸다. 그 선언이 없으면 하한 프루닝이 통째로 꺼져 창을 좁혀도 5년 파티션을
전부 연다(실측: `_quotes` 8.14초 → 0.05초).

**빠른 대신 조용히 틀릴 수 있는 최적화라** 여기서 두 가지를 고정한다.

1. 선언한 테이블에서 프루닝 전후 결과가 **같다**
2. 사실보다 **먼저** 관측될 수 있는 테이블에는 선언이 없다 — 배당락 예고,
   미래 일정, 미래 발효 설정은 하한을 걸면 행이 사라진다
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from quant_rl_trading.store.tables import get_spec, table_names

NOW = datetime(2026, 8, 12, tzinfo=UTC)

#: **선언하면 안 되는 테이블.** 관측이 사실 시각보다 앞설 수 있다.
#:
#: - ``dividends``: 배당락일을 미리 공시한다
#: - ``events`` · ``documents``: 예정 일정이 먼저 들어온다
#: - ``config``: ``effective_at`` 이 미래일 수 있다 (정정본 발효)
#: - ``verdicts`` · ``killswitch`` · ``capital_flows`` 등: 아직 판단하지 않았다.
#:   위험이 적다고 켜 두면, 켤 이유를 따진 적이 없는 채로 켜져 있게 된다
FORWARD_LOOKING = {"dividends", "events", "documents", "config"}


def test_미리_알_수_있는_테이블에는_하한을_걸지_않는다() -> None:
    for table in FORWARD_LOOKING:
        spec = get_spec(table)
        assert getattr(spec, "observation_lag_days", None) is None, (
            f"{table} 은 사실보다 먼저 관측될 수 있다. 하한을 걸면 그 행이 "
            "조용히 사라진다"
        )


def test_선언한_테이블은_전부_양수다() -> None:
    """0 이나 음수는 '관측이 사실보다 앞선다' 는 뜻이라 의미가 뒤집힌다."""
    for table in table_names():
        lag = getattr(get_spec(table), "observation_lag_days", None)
        if lag is not None:
            assert lag > 0, f"{table}: observation_lag_days 는 양수여야 한다"


def _price_row(entity: str, session: datetime, observed: datetime) -> dict[str, Any]:
    return {
        "entity_id": entity,
        "valid_from": session,
        "observed_at": observed,
        "source": "test",
        "market": "KR",
        "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0,
        "volume": 10.0, "value": 1_000.0, "adj_factor": None,
    }


@pytest.fixture
def deep(store):  # type: ignore[no-untyped-def]
    """5년치 파티션. 프루닝이 켜져도 창 안의 행은 전부 나와야 한다."""
    rows = []
    for offset in range(0, 1800, 7):
        day = NOW - timedelta(days=offset)
        rows.append(_price_row("KR:000100", day, day))
    # **늦게 도착한 정정본** — valid_from 은 과거인데 observed_at 이 오늘이다.
    # 하한은 과거 쪽을 자르므로 이 행은 잘리면 안 된다.
    rows.append(_price_row("KR:000100", NOW - timedelta(days=900), NOW))
    store.append("prices", rows, ingest_run_id="deep")
    return store


def test_창_안의_행은_프루닝_후에도_전부_나온다(deep) -> None:
    narrow = deep.get("prices", as_of=NOW, entity="KR:000100", lookback=30)
    wide = deep.get("prices", as_of=NOW, entity="KR:000100", lookback=1800)

    # 30일 창에는 5행(7일 간격), 1800일 창에는 전부.
    assert len(narrow) == len(
        [row for row in wide.to_dict(orient="records")
         if (NOW - row["valid_from"]).days <= 30]
    )
    assert not narrow.empty


def test_늦게_도착한_정정본은_잘리지_않는다(deep) -> None:
    """백필·정정본은 observed_at 이 최근이라 하한 **위**에 있다."""
    frame = deep.get("prices", as_of=NOW, entity="KR:000100", lookback=1000)
    target = NOW - timedelta(days=900)
    matched = [
        row for row in frame.to_dict(orient="records")
        if row["valid_from"].date() == target.date()
    ]
    assert matched, "정정본이 사라졌다. 하한 프루닝이 과하게 잘랐다"
