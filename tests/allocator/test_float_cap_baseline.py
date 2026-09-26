"""Z2 트랙 비중 — 유동시총 가중, 종목 상한, 넘친 몫 재분배, 시총을 모르면 동일가중으로 물러섬."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from quant_rl_trading.allocator.float_cap_baseline import allocate_float_cap, capped, capped_with_residual

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
    assert path.startswith("float_cap:equal_fallback") and w["KR:3"] == pytest.approx(1 / 6)


def test_시총_커버리지가_얕으면_전량매도하지_않고_동일가중으로_간다(store) -> None:  # type: ignore[no-untyped-def]
    """Z2 에서 KRX 시총 보충이 실패한 날 — 예전엔 시총 아는 8종목만 반환해 16종목이 이유 없이 팔렸다."""
    names = [f"KR:{i}" for i in range(24)]
    _caps(store, {n: 100.0 + i for i, n in enumerate(names[:8])}, {})
    w, path = allocate_float_cap(store, as_of=NOW, market="KR", candidates=names, limit=0.10, cash_buffer=0.05)
    assert set(w) == set(names)                              # 한 종목도 빠지지 않는다
    assert path == "float_cap:equal_fallback:coverage=0.33"   # 사유가 driver 에 남는다
    assert sum(w.values()) == pytest.approx(0.95)
    assert w["KR:20"] == pytest.approx(0.95 / 24)


def test_커버리지가_충분하면_시총_결측은_중앙값을_받는다(store) -> None:  # type: ignore[no-untyped-def]
    names = [f"KR:{i}" for i in range(10)]
    _caps(store, {n: 100.0 for n in names[:9]}, {})          # 커버리지 0.9 — 하한 0.8 위
    w, path = allocate_float_cap(store, as_of=NOW, market="KR", candidates=names, limit=0.5, cash_buffer=0.0)
    assert path == "float_cap:median_cap=1"
    assert set(w) == set(names)
    assert w["KR:9"] == pytest.approx(w["KR:0"])             # 중앙값 = 아는 종목과 같은 시총
    assert sum(w.values()) == pytest.approx(1.0)


def test_커버리지_하한은_config_에서_읽는다(store) -> None:  # type: ignore[no-untyped-def]
    """하한을 0.3 으로 낮추면 커버리지 0.33 도 통과한다 — 하드코딩이 아니라는 증거(불변식 10)."""
    names = [f"KR:{i}" for i in range(24)]
    _caps(store, {n: 100.0 for n in names[:8]}, {})
    store.append("config", [{"entity_id": "allocator.float_cap_min_coverage", "valid_from": NOW - timedelta(days=2),
                             "observed_at": NOW - timedelta(days=2), "source": "t", "value_json": "0.3"}],
                 ingest_run_id="cfg", source="t")
    _, path = allocate_float_cap(store, as_of=NOW, market="KR", candidates=names, limit=0.10, cash_buffer=0.0)
    assert path.startswith("float_cap:median_cap=16")


def test_상한에_다_막히면_못_나눈_몫을_드러낸다(store) -> None:  # type: ignore[no-untyped-def]
    """8종목 × 10% = 80% — 나머지 20% 를 조용히 버리면 노출이 왜 낮은지 아무도 모른다."""
    weights, residual = capped_with_residual(pd.Series({f"KR:{i}": 100.0 for i in range(8)}), 0.10)
    assert weights.sum() == pytest.approx(0.8) and residual == pytest.approx(0.2)

    names = [f"KR:{i}" for i in range(8)]
    _caps(store, {n: 100.0 for n in names}, {})
    w, path = allocate_float_cap(store, as_of=NOW, market="KR", candidates=names, limit=0.10, cash_buffer=0.05)
    assert path == "float_cap:residual=0.2000"
    assert sum(w.values()) == pytest.approx(0.95 * 0.8)


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
