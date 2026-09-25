"""Z2 트랙 비중 — 유동시총 가중, 종목 상한, 넘친 몫 재분배, 시총을 모르면 동일가중으로 물러섬."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from quant_rl_trading.allocator.float_cap_baseline import allocate_float_cap, capped

NOW = datetime(2026, 9, 23, 7, 0, tzinfo=UTC)


def test_상한을_넘친_몫은_나머지에_비례해_간다() -> None:
    w = capped(pd.Series({"A": 70.0, "B": 20.0, "C": 10.0}), 0.5)
    assert w["A"] == pytest.approx(0.5)
    assert w["B"] == pytest.approx(0.5 * 20 / 30) and w["C"] == pytest.approx(0.5 * 10 / 30)
    assert w.sum() == pytest.approx(1.0)


def _caps(store, caps: dict[str, float], floats: dict[str, float]) -> None:  # type: ignore[no-untyped-def]
    when = NOW - timedelta(days=1)
    store.append("market_stats", [
        {"entity_id": e, "valid_from": when, "observed_at": when, "source": "t", "market": "KR", "metric": "market_cap", "value": v}
        for e, v in caps.items()
    ], ingest_run_id="caps", source="t")
    if floats:
        store.append("float_ratio", [
            {"entity_id": e, "valid_from": when, "observed_at": when, "source": "t", "float_ratio": v} for e, v in floats.items()
        ], ingest_run_id="floats", source="t")


def test_유동시총_가중_상한_현금버퍼(store) -> None:  # type: ignore[no-untyped-def]
    names = [f"KR:{i}" for i in range(12)]
    caps = {n: 100.0 for n in names} | {"KR:0": 5000.0}     # 한 종목이 압도적
    _caps(store, caps, {n: 0.5 for n in names} | {"KR:1": 1.0})
    w, path = allocate_float_cap(store, as_of=NOW, market="KR", candidates=names, limit=0.10, cash_buffer=0.05)
    assert path == "float_cap"
    assert sum(w.values()) == pytest.approx(0.95)
    assert w["KR:0"] == pytest.approx(0.10 * 0.95)          # 상한
    assert w["KR:1"] > w["KR:2"]                              # 유동비율 1.0 이 0.5 보다 크게


def test_시총을_모르면_동일가중으로_물러선다(store) -> None:  # type: ignore[no-untyped-def]
    names = [f"KR:{i}" for i in range(6)]
    _caps(store, {"KR:0": 100.0}, {})
    w, path = allocate_float_cap(store, as_of=NOW, market="KR", candidates=names, limit=0.10, cash_buffer=0.0)
    assert path == "float_cap:equal_fallback" and w["KR:3"] == pytest.approx(1 / 6)


def test_같은_회사_두_클래스는_한_번만_센다(store) -> None:  # type: ignore[no-untyped-def]
    """미장 시총은 회사 합계라 GOOG·GOOGL 이 같은 값을 받는다 — 둘 다 두면 알파벳이 두 번(2026-09-25 G1 트랙 11.7%)."""
    names = ["US:GOOG", "US:GOOGL"] + [f"US:X{i}" for i in range(8)]
    when = NOW - timedelta(days=1)
    store.append("market_stats", [
        {"entity_id": e, "valid_from": when, "observed_at": when, "source": "t", "market": "US", "metric": "market_cap",
         "value": 4000.0 if e.startswith("US:GOOG") else 100.0 + i}
        for i, e in enumerate(names)
    ], ingest_run_id="caps", source="t")
    w, _ = allocate_float_cap(store, as_of=NOW, market="US", candidates=names, limit=0.5, cash_buffer=0.0)
    assert "US:GOOG" in w and "US:GOOGL" not in w
