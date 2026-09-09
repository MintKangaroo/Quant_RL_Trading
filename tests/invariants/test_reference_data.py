"""화면용 명칭만 참조 예외다. 업종·유동주식비율은 관측 전 사용을 금지한다."""

from __future__ import annotations

import pytest

from quant_rl_trading.store.tables import get_spec

pytestmark = pytest.mark.invariant


REFERENCE_TABLES = ["names_ko"]  # 화면 표시 전용. 투자 입력에는 예외가 없다.


def test_참조_속성은_등록된_표뿐이다() -> None:
    from quant_rl_trading.store.tables import _SPECS

    flagged = sorted(name for name, spec in _SPECS.items() if spec.reference_data)
    assert flagged == REFERENCE_TABLES, flagged


def test_reference_inputs_are_point_in_time(store, ts) -> None:  # type: ignore[no-untyped-def]
    store.append(
        "sectors",
        [{
            "entity_id": "KR:005930", "valid_from": ts(2021, 8, 11), "observed_at": ts(2026, 8, 27),
            "source": "dart_company", "market": "KR", "sector": "KSIC:264",
        }],
        ingest_run_id="sectors-ref",
    )
    seen = store.get("sectors", as_of=ts(2025, 3, 3))
    assert seen.empty
    known = store.get("sectors", as_of=ts(2026, 8, 27))
    assert list(known["sector"]) == ["KSIC:264"]
    # valid_from 이 as_of 뒤면 안 보인다 — 게이트는 여전히 있다.
    assert store.get("sectors", as_of=ts(2021, 8, 10)).empty


def test_float_ratio_cannot_be_backdated(store, ts) -> None:
    store.append("float_ratio", [{
        "entity_id": "KR:005930", "valid_from": ts(2024, 1, 1),
        "observed_at": ts(2026, 9, 9), "source": "test", "market": "KR",
        "shares_outstanding": 1000., "float_ratio": .5,
    }], ingest_run_id="float-late")
    assert store.get("float_ratio", as_of=ts(2025, 1, 1)).empty
    assert len(store.get("float_ratio", as_of=ts(2026, 9, 9))) == 1


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
