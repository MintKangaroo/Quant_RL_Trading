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

import json
from datetime import UTC, datetime, timedelta

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quant_rl_trading.selector import filters
from quant_rl_trading.store import paths
from quant_rl_trading.store.schema import compute_row_hash
from quant_rl_trading.store.tables import get_spec

NOW = datetime(2026, 8, 12, 6, 40, tzinfo=UTC)
#: 창(400일)보다 길게 깔아야 "창 끝에 닿았다" 와 "진짜 신규주" 가 구분된다.
SESSIONS = [NOW - timedelta(days=offset) for offset in range(460, -1, -1)]

PARAMS = filters.FilterParams(
    min_turnover=500_000_000.0, min_listed_days=180, max_price_ratio=0.15,
    capacity_multiple=0.0,
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


def _seed_legacy_row(store, table: str, row: dict, *, ingest_run_id: str) -> None:
    """schema.validate_batch 를 안 거치고 parquet 을 직접 쓴다.

    2026-08-15 이전에 이미 들어간 오염 행(entity_id 의 시장 접두어와
    market 컬럼이 다른 행)을 재현하기 위해서다 — 그 사고 이후로는
    ``store.append()`` 자체가 이런 행을 거부하므로, 정상 경로로는 이미
    창고에 있는 낡은 오염을 흉내낼 수 없다.
    """
    spec = get_spec(table)
    full = {"revision": 0, **row, "ingest_run_id": ingest_run_id}
    full["row_hash"] = compute_row_hash(full)
    columns = {name: [full.get(name)] for name in spec.all_columns}
    table_arrow = pa.Table.from_pydict(columns, schema=spec.arrow_schema)

    observed = paths.observed_date(row["observed_at"])
    target = paths.data_file(store.root, table, observed, ingest_run_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table_arrow, target)

    manifest = paths.manifest_path(store.root, table, ingest_run_id)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(
            {
                "table": table, "ingest_run_id": ingest_run_id, "rows": 1,
                "files": [str(target.relative_to(store.root))],
            }
        ),
        encoding="utf-8",
    )


def test_시장_접두어가_다른_기존_오염행은_걸러진다(seeded) -> None:
    """**실측 사고 재현(2026-08-15).** KR 백필의 상폐 감지가 시장을 안
    가려서 US 종목에 market="KR" 이 잘못 찍힌 적이 있다. 쓰기 시점 방어
    (schema.validate_batch)는 그 뒤로 재발만 막을 뿐, 이미 들어간 행은
    append-only 라 지울 수 없다 — tradable_universe 가 entity_id 접두어로
    다시 걸러야 한다.
    """
    _seed_legacy_row(
        seeded, "universe",
        {**_universe_row("US:AA", NOW), "market": "KR"},
        ingest_run_id="legacy-contamination",
    )

    result = _run(seeded)

    assert "US:AA" not in result.kept
    assert result.dropped["US:AA"] == "시장 불일치"
    # 진짜 KR 종목은 이 방어에 안 걸려야 한다.
    assert "KR:000100" in result.kept


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
        min_turnover=500_000_000.0, min_listed_days=5, max_price_ratio=0.15,
        capacity_multiple=0.0,
    )
    result = filters.tradable_universe(
        store, as_of=NOW, market="KR", params=short, equity=100_000_000.0
    )

    # 창(10세션)이 전 종목의 과거를 다 덮으므로 신규주 판정은 걸리지 않는다.
    assert result.kept == ("KR:000100",)
    assert result.dropped["KR:000300"] == "상장폐지·거래불가"


def test_자본이_커지면_용량_하한이_상수를_넘어선다(store) -> None:
    """상수 하한은 통과해도 목표금액이 못 담을 종목은 배수 하한이 뺀다.

    자본 5억 · 배수 1.6 → 실효 하한 8억. 6억짜리는 상수(5억)는 넘지만 8억엔
    못 미쳐 **"거래대금 용량 미달"** — "못 파는 종목" 이 아니라 "못 사는 종목"
    이다. 10억짜리는 남는다 (portfolio-construction.md §부록).
    """
    days = [NOW - timedelta(days=offset) for offset in range(9, -1, -1)]
    rows, prices = [], []
    for day in days:
        for entity, value in (("KR:000600", 6e8), ("KR:001000", 1e9)):
            rows.append(_universe_row(entity, day))
            price = _price_row(entity, day)
            price["value"] = value
            prices.append(price)
    store.append("universe", rows, ingest_run_id="u-cap")
    store.append("prices", prices, ingest_run_id="p-cap")

    params = filters.FilterParams(
        min_turnover=500_000_000.0, min_listed_days=5, max_price_ratio=0.15,
        capacity_multiple=1.6,
    )
    result = filters.tradable_universe(
        store, as_of=NOW, market="KR", params=params, equity=500_000_000.0
    )

    assert result.kept == ("KR:001000",)
    assert result.dropped["KR:000600"] == "거래대금 용량 미달"


def test_배수_하한은_KR_에만_걸린다(store) -> None:
    """US 는 거래대금이 달러라 원화 자본에 배수를 걸 수 없다 — 절대 하한만.

    같은 6억짜리라도 시장이 US 면 배수가 꺼져 절대 하한(5억)만 본다.
    """
    days = [NOW - timedelta(days=offset) for offset in range(9, -1, -1)]
    rows, prices = [], []
    for day in days:
        row = _universe_row("US:AAA", day)
        row["entity_id"] = "US:AAA"
        row["market"] = "US"
        rows.append(row)
        price = _price_row("US:AAA", day)
        price["market"] = "US"
        price["value"] = 6e8
        prices.append(price)
    store.append("universe", rows, ingest_run_id="u-us")
    store.append("prices", prices, ingest_run_id="p-us")

    params = filters.FilterParams(
        min_turnover=500_000_000.0, min_listed_days=5, max_price_ratio=0.15,
        capacity_multiple=1.6,
    )
    result = filters.tradable_universe(
        store, as_of=NOW, market="US", params=params, equity=500_000_000.0
    )

    assert result.kept == ("US:AAA",)


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
