"""유니버스 필터 — **접기를 창고로 내려도 규칙이 그대로인가.**

``tradable_universe`` 는 400일 창을 보지만 종목당 두 값만 쓴다(마지막 상태,
창 안 최초 등장일). 그 접기를 pandas 에서 DuckDB 로 옮겼다. 빨라지는 대신
조용히 틀릴 수 있는 최적화라, 여기서 규칙 자체를 못 박는다.

1. 상장 6개월 경계 양쪽 — 최초 등장일이 창 전체에서 계산돼야 성립한다.
   짧은 창에서 구하면 오래된 종목이 통째로 신규주가 된다
2. 마지막 상태(상폐·거래정지)는 **마지막 행**으로 판정된다 — 과거에 살아
   있었다는 사실이 오늘의 판정을 덮으면 안 된다
3. 정정본이 마지막 상태를 이긴다 — 접기와 함께 창고로 내려간 규칙이다
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from quant_rl_trading.selector import filters

NOW = datetime(2026, 8, 12, 6, 40, tzinfo=UTC)
#: 창(400일)보다 길게 깔아야 "창 끝에 닿았다" 와 "진짜 신규주" 가 구분된다.
SESSIONS = [NOW - timedelta(days=offset) for offset in range(460, -1, -1)]

PARAMS = filters.FilterParams(
    min_turnover=500_000_000.0, min_listed_days=180, max_price_ratio=0.15
)


def _universe_row(entity: str, day: datetime, *, listed: bool = True, tradable: bool = True):
    return {
        "entity_id": entity, "valid_from": day, "observed_at": day,
        "source": "test", "market": "KR", "name": entity,
        "is_listed": listed, "is_tradable": tradable, "delisted_on": None,
    }


def _price_row(entity: str, day: datetime, *, close: float = 10_000.0):
    return {
        "entity_id": entity, "valid_from": day, "observed_at": day,
        "source": "test", "market": "KR",
        "open": close, "high": close, "low": close, "close": close,
        "volume": 100_000.0, "value": 5_000_000_000.0, "adj_factor": None,
    }


@pytest.fixture
def seeded(store):  # type: ignore[no-untyped-def]
    """네 종목. 각자 다른 사연을 가진다.

    - ``KR:000100`` 창보다 오래 상장 — 남아야 한다
    - ``KR:000200`` 30세션 전 상장 — 6개월 미만이라 빠진다
    - ``KR:000300`` 마지막 세션에 거래정지 — 마지막 상태로 빠진다
    - ``KR:000400`` 181세션 전 상장 — 경계 바로 바깥이라 남는다
    """
    universe_rows = []
    price_rows = []
    for day in SESSIONS:
        universe_rows.append(_universe_row("KR:000100", day))
        price_rows.append(_price_row("KR:000100", day))

        if day >= SESSIONS[-30]:
            universe_rows.append(_universe_row("KR:000200", day))
            price_rows.append(_price_row("KR:000200", day))

        universe_rows.append(
            _universe_row("KR:000300", day, tradable=day != SESSIONS[-1])
        )
        price_rows.append(_price_row("KR:000300", day))

        if day >= NOW - timedelta(days=181):
            universe_rows.append(_universe_row("KR:000400", day))
            price_rows.append(_price_row("KR:000400", day))

    store.append("universe", universe_rows, ingest_run_id="u-seed")
    store.append("prices", price_rows, ingest_run_id="p-seed")
    return store


def _run(store, *, equity: float = 100_000_000.0):
    return filters.tradable_universe(
        store, as_of=NOW, market="KR", params=PARAMS, equity=equity
    )


def test_상장_6개월_미만은_빠진다(seeded) -> None:
    result = _run(seeded)

    assert "KR:000200" not in result.kept
    assert result.dropped["KR:000200"] == "상장 6개월 미만"


def test_오래_상장된_종목은_남는다(seeded) -> None:
    assert "KR:000100" in _run(seeded).kept


def test_경계_바깥은_남는다(seeded) -> None:
    """181세션 전 상장 — 6개월을 하루 넘겼다.

    최초 등장일을 창 전체가 아니라 짧은 창에서 구하면 이 종목이 신규주로
    보인다. 경계 양쪽을 같이 못 박아야 그 실수가 잡힌다.
    """
    assert "KR:000400" in _run(seeded).kept


def test_마지막_상태로_판정한다(seeded) -> None:
    """400세션 동안 거래 가능했어도 **마지막 세션**에 정지면 빠진다.
    접기가 마지막 행이 아닌 아무 행이나 집으면 이 종목이 살아 남는다."""
    result = _run(seeded)

    assert "KR:000300" not in result.kept
    assert result.dropped["KR:000300"] == "상장폐지·거래불가"


def test_짧은_창에서도_최신_상태가_유지된다(store) -> None:
    """창이 ``min_listed_days`` 보다 짧아도 마지막 상태 판정은 그대로다.

    상장 판정과 최신 상태 판정은 같은 조회에서 나오지만 서로 다른 축을 본다.
    창을 줄였을 때 둘이 같이 무너지지 않는지 본다.
    """
    days = [NOW - timedelta(days=offset) for offset in range(9, -1, -1)]
    rows = []
    prices = []
    for day in days:
        rows.append(_universe_row("KR:000100", day))
        rows.append(_universe_row("KR:000300", day, listed=day != days[-1]))
        prices.append(_price_row("KR:000100", day))
        prices.append(_price_row("KR:000300", day))
    store.append("universe", rows, ingest_run_id="u-short")
    store.append("prices", prices, ingest_run_id="p-short")

    short = filters.FilterParams(
        min_turnover=500_000_000.0, min_listed_days=5, max_price_ratio=0.15
    )
    result = filters.tradable_universe(
        store, as_of=NOW, market="KR", params=short, equity=100_000_000.0
    )

    # 창(10세션)이 전 종목의 과거를 다 덮으므로 신규주 판정은 걸리지 않는다.
    assert result.kept == ("KR:000100",)
    assert result.dropped["KR:000300"] == "상장폐지·거래불가"


def test_정정본은_마지막_행_판정을_바꾼다(store) -> None:
    """같은 세션에 revision 1 이 오면 그것이 마지막 상태다.

    접기를 창고로 내리면서 정정본 선택이 함께 내려갔다. 원본이 이기면
    상폐 정정이 조용히 무시된다.
    """
    for day in SESSIONS:
        store.append("universe", [_universe_row("KR:000100", day)],
                     ingest_run_id=f"u-{day.date()}")
        store.append("prices", [_price_row("KR:000100", day)],
                     ingest_run_id=f"p-{day.date()}")
    store.append(
        "universe",
        [{**_universe_row("KR:000100", SESSIONS[-1], listed=False), "revision": 1}],
        ingest_run_id="u-fix",
    )

    result = _run(store)

    assert result.kept == ()
    assert result.dropped["KR:000100"] == "상장폐지·거래불가"
