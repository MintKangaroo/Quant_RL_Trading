"""참조 속성 예외 (data-contract.md §3) — 게이트가 valid_from 을 보는 테이블은
sectors 하나뿐이고, 예측 정보 테이블은 여전히 observed_at 으로 막힌다."""

from __future__ import annotations

import pytest

from quant_rl_trading.store.tables import get_spec

pytestmark = pytest.mark.invariant


def test_참조_속성은_sectors_뿐이다() -> None:
    from quant_rl_trading.store.tables import _SPECS

    flagged = sorted(name for name, spec in _SPECS.items() if spec.reference_data)
    assert flagged == ["sectors"], flagged


def test_섹터는_늦게_받았어도_그때의_분류로_보인다(store, ts) -> None:  # type: ignore[no-untyped-def]
    store.append(
        "sectors",
        [{
            "entity_id": "KR:005930", "valid_from": ts(2021, 8, 11), "observed_at": ts(2026, 8, 27),
            "source": "dart_company", "market": "KR", "sector": "KSIC:264",
        }],
        ingest_run_id="sectors-ref",
    )
    seen = store.get("sectors", as_of=ts(2025, 3, 3))
    assert list(seen["sector"]) == ["KSIC:264"]
    # valid_from 이 as_of 뒤면 안 보인다 — 게이트는 여전히 있다.
    assert store.get("sectors", as_of=ts(2021, 8, 10)).empty


def test_시세는_여전히_observed_at_으로_막힌다(store, ts) -> None:  # type: ignore[no-untyped-def]
    assert get_spec("prices").reference_data is False
    store.append(
        "prices",
        [{
            "entity_id": "KR:005930", "valid_from": ts(2024, 3, 4), "observed_at": ts(2026, 8, 27),
            "source": "test", "market": "KR", "close": 100.0,
        }],
        ingest_run_id="prices-late",
    )
    assert store.get("prices", as_of=ts(2025, 1, 1)).empty
