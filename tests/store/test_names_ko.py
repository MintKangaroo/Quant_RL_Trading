"""미장 이름은 names_ko(한국어)가 universe(영문)를 덮는다 — 없으면 영문 그대로."""
from datetime import UTC, datetime
from pathlib import Path

from quant_rl_trading.store import Store, names


def test_us_names_prefer_korean(tmp_path: Path) -> None:
    store = Store(root=tmp_path / "wh")
    t = datetime(2026, 9, 1, tzinfo=UTC)
    store.append("universe", [
        {"entity_id": "US:MU", "valid_from": t, "observed_at": t, "source": "t", "market": "US", "name": "Micron Technology", "is_listed": True, "is_tradable": True, "delisted_on": None},
        {"entity_id": "US:XYZ", "valid_from": t, "observed_at": t, "source": "t", "market": "US", "name": "Xyz Corp", "is_listed": True, "is_tradable": True, "delisted_on": None},
    ], ingest_run_id="u")
    store.append("names_ko", [
        {"entity_id": "US:MU", "valid_from": t, "observed_at": t, "source": "naver", "market": "US", "name_ko": "마이크론 테크놀로지", "name_en": "Micron", "exchange": "NASDAQ"},
    ], ingest_run_id="n")
    got = names.of(store, as_of=datetime(2026, 9, 5, tzinfo=UTC), entities=["US:MU", "US:XYZ"])
    assert got == {"US:MU": "마이크론 테크놀로지", "US:XYZ": "Xyz Corp"}
