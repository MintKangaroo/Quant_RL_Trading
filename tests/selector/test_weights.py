"""가중치 측정 현황 — **빈 dict 셋을 갈라 본다.**

알파 가중치가 비는 길은 셋이고 처방이 전부 다른데, 예전에는 셋이 같은 화면
문구와 같은 종료코드로 끝났다 (태스크 #12).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from quant_rl_trading.selector import weights as weights_module

NOW = datetime(2026, 8, 21, 6, 40, tzinfo=UTC)


def _weights(store, rows, *, tag: str) -> None:  # type: ignore[no-untyped-def]
    store.append(
        "analyst_weights",
        [
            {
                "entity_id": analyst, "valid_from": NOW, "observed_at": NOW,
                "source": "test", "market": market, "ic": weight, "weight": weight,
            }
            for analyst, market, weight in rows
        ],
        ingest_run_id=tag,
    )


@pytest.fixture
def seeded(store):  # type: ignore[no-untyped-def]
    store.seed_config_defaults()
    return store


def test_측정이_아예_없다(seeded) -> None:
    census = weights_module.weight_census(seeded, as_of=NOW, market="US")

    assert census.fault == weights_module.NO_MEASUREMENT
    assert census.measured == ()
    assert "측정 자체가 없다" in census.describe()


def test_측정은_있는데_통과가_0종이다(seeded) -> None:
    """기다려도 안 풀린다. 피처로 돌아가야 하는 경우."""
    _weights(seeded, [("chart", "KR", 0.0), ("regime", "KR", 0.0)], tag="w-none")

    census = weights_module.weight_census(seeded, as_of=NOW, market="KR")

    assert census.fault == weights_module.NONE_PASSED
    assert census.measured == ("chart", "regime")
    assert census.passed == ()
    assert "통과가 0종" in census.describe()


def test_통과했지만_전부_제약_Analyst다(seeded) -> None:
    """US 가 2026-08 내내 있던 자리. 측정도 통과도 있는데 알파가 0종이다."""
    _weights(
        seeded,
        [
            ("chart", "US", 0.0), ("flow_us", "US", 0.0),
            ("regime", "US", 0.0), ("risk", "US", 0.0585),
        ],
        tag="w-const",
    )

    census = weights_module.weight_census(seeded, as_of=NOW, market="US")

    assert census.fault == weights_module.CONSTRAINT_ONLY
    assert census.passed == ("risk",)
    assert census.constrained == ("risk",)
    assert census.alpha == ()
    assert "제약 Analyst" in census.describe()


def test_알파가_있으면_사유가_없다(seeded) -> None:
    _weights(seeded, [("fundamental", "KR", 0.9), ("risk", "KR", 0.5)], tag="w-ok")

    census = weights_module.weight_census(seeded, as_of=NOW, market="KR")

    assert census.fault == ""
    assert census.alpha == ("fundamental",)
    assert census.alpha_map == {"fundamental": 0.9}


def test_시장을_섞지_않는다(seeded) -> None:
    """국장 가중치가 미장 판정에 새면 US 는 영영 정상으로 보인다."""
    _weights(seeded, [("fundamental", "KR", 0.9), ("risk", "US", 0.5)], tag="w-mix")

    assert weights_module.weight_census(seeded, as_of=NOW, market="US").fault == (
        weights_module.CONSTRAINT_ONLY
    )
    assert weights_module.weight_census(seeded, as_of=NOW, market="KR").fault == ""


def test_measured_weights_의_동작은_그대로다(seeded) -> None:
    """census 를 더하면서 기존 호출부(진화·배치 비교)의 계약을 바꾸지 않는다.

    미통과는 여전히 **키째로 빠진다** — 0 을 그대로 돌려주면 합성에서 관찰
    모드가 실제 무게를 받는다.
    """
    _weights(seeded, [("chart", "KR", 0.0), ("risk", "KR", 0.5)], tag="w-keep")

    assert weights_module.measured_weights(seeded, as_of=NOW, market="KR") == {"risk": 0.5}
    assert weights_module.analyst_weights(seeded, as_of=NOW, market="KR") == {}


def _weights_at(store, rows, *, at: datetime, tag: str) -> None:  # type: ignore[no-untyped-def]
    store.append(
        "analyst_weights",
        [
            {
                "entity_id": analyst, "valid_from": at, "observed_at": at,
                "source": "test", "market": market, "ic": weight, "weight": weight,
            }
            for analyst, market, weight in rows
        ],
        ingest_run_id=tag,
    )


def test_가중치_갱신은_K세션에_걸쳐_섞인다(seeded) -> None:
    """2026-09-13 미장: fundamental 0 → 0.797 이 한 번에 들어가 보유 17종목이 전량 매도됐다."""
    first = datetime(2026, 9, 3, 8, 35, tzinfo=UTC)   # 목 17:35 KST
    second = datetime(2026, 9, 13, 0, 4, tzinfo=UTC)  # 토 09:04 KST = 금 20:04 NY
    _weights_at(seeded, [("ranker", "US", 1.0), ("risk", "US", 1.0), ("fundamental", "US", 0.0)], at=first, tag="w1")
    _weights_at(seeded, [("ranker", "US", 1.0), ("risk", "US", 1.0), ("fundamental", "US", 0.8)], at=second, tag="w2")

    # 9/14(월) 미장 세션 = 갱신 뒤 1세션 → 1/5
    monday = datetime(2026, 9, 14, 20, 20, tzinfo=UTC)
    assert weights_module.measured_weights(seeded, as_of=monday, market="US")["fundamental"] == pytest.approx(0.16)
    assert weights_module.weight_census(seeded, as_of=monday, market="US").values["fundamental"] == pytest.approx(0.16)
    # 9/18(금) = 5세션 → 전부
    friday = datetime(2026, 9, 18, 20, 20, tzinfo=UTC)
    assert weights_module.measured_weights(seeded, as_of=friday, market="US")["fundamental"] == pytest.approx(0.8)
    # 갱신 당일(토)엔 0세션 → 직전 값. 0 이라 통과 목록에서 빠진다
    assert "fundamental" not in weights_module.measured_weights(seeded, as_of=second, market="US")
    # 안 바뀐 Analyst 는 그대로
    assert weights_module.measured_weights(seeded, as_of=monday, market="US")["ranker"] == 1.0


def test_첫_측정은_섞지_않는다(seeded) -> None:
    """직전 측정이 없는데 0 에서 출발하면 첫 세션의 알파가 0종이 된다."""
    at = datetime(2026, 9, 13, 0, 4, tzinfo=UTC)
    _weights_at(seeded, [("ranker", "KR", 1.0)], at=at, tag="w-first")
    monday = datetime(2026, 9, 14, 7, 0, tzinfo=UTC)
    assert weights_module.measured_weights(seeded, as_of=monday, market="KR") == {"ranker": 1.0}
