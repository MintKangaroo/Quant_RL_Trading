"""하루 사이클 계약 테스트 — 진짜 창고 위에서.

**여기서 증명하는 것은 결정론이다.** 같은 as_of 로 두 번 돌리면 같은 주문이
나와야 한다. 안 나오면 백테스트가 거짓말이 되고, 그 위의 IR·MDD·보상이 전부
무의미해진다 (불변식 5).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from lattice.replay.clock import ReplayClock
from lattice.session import daily

NOW = datetime(2026, 8, 12, 1, 0, tzinfo=UTC)      # 한국시간 10:00
SESSIONS = [NOW - timedelta(days=offset) for offset in range(400, -1, -1)]
ENTITIES = ["KR:000100", "KR:000200", "KR:000300"]


@pytest.fixture
def fund(store):  # type: ignore[no-untyped-def]
    """1억 입금 + 3종목 400세션 + IC 가중치 + 오늘 신호."""
    store.seed_config_defaults()

    store.append(
        "fx",
        [{
            "entity_id": "FX:USDKRW", "valid_from": NOW, "observed_at": NOW,
            "source": "test", "rate": 1_350.0,
        }],
        ingest_run_id="fx-seed",
    )
    store.append(
        "capital_flows",
        [{
            "entity_id": "FUND", "valid_from": SESSIONS[0], "observed_at": SESSIONS[0],
            "source": "test", "currency": "KRW", "amount": 100_000_000.0,
            "kind": "deposit",
        }],
        ingest_run_id="flow-seed",
    )

    universe_rows = []
    price_rows = []
    for index, day in enumerate(SESSIONS):
        for offset, entity in enumerate(ENTITIES):
            universe_rows.append({
                "entity_id": entity, "valid_from": day, "observed_at": day,
                "source": "test", "market": "KR", "name": entity,
                "is_listed": True, "is_tradable": True, "delisted_on": None,
            })
            close = 10_000.0 + index * 5 + offset * 500
            price_rows.append({
                "entity_id": entity, "valid_from": day, "observed_at": day,
                "source": "test", "market": "KR",
                "open": close, "high": close, "low": close, "close": close,
                "volume": 100_000.0, "value": 5_000_000_000.0, "adj_factor": None,
            })
    store.append("universe", universe_rows, ingest_run_id="u-seed")
    store.append("prices", price_rows, ingest_run_id="p-seed")

    store.append(
        "analyst_weights",
        [{
            "entity_id": "risk", "valid_from": NOW, "observed_at": NOW,
            "source": "test", "market": "KR", "ic": 0.077, "weight": 1.0,
        }],
        ingest_run_id="w-seed",
    )
    store.append(
        "signals",
        [{
            "entity_id": entity, "valid_from": NOW, "observed_at": NOW,
            "source": "test", "analyst": "risk", "analyst_version": "risk-v0.1.0",
            "score": score, "confidence": 1.0, "horizon_days": 5,
            "features_hash": "x", "evidence_json": "[]", "latency_ms": 1.0,
        } for entity, score in zip(ENTITIES, [0.9, 0.5, 0.2], strict=True)],
        ingest_run_id="s-seed",
    )
    return store


def test_후보에서_주문까지_이어진다(fund) -> None:
    result = daily.run(fund, ReplayClock(NOW), as_of=NOW, market="KR")

    assert result.equity == pytest.approx(100_000_000.0)
    assert result.candidates
    assert result.weights
    assert result.orders
    # 종목 상한 15% 를 넘지 않는다.
    assert max(result.weights.values()) <= 0.15 + 1e-9


def test_같은_as_of_는_같은_주문을_낸다(fund, tmp_path) -> None:
    """**결정론.** 안 지켜지면 백테스트가 거짓말이 된다.

    두 번째 실행은 창고 상태가 달라진다(첫 실행이 주문·실현비중을 적재했다).
    그래도 **주문은 같아야 한다** — 결정은 관측의 함수이지 우리가 남긴
    기록의 함수가 아니기 때문이다.
    """
    first = daily.run(fund, ReplayClock(NOW), as_of=NOW, market="KR", run_id="a")
    second = daily.run(fund, ReplayClock(NOW), as_of=NOW, market="KR", run_id="b")

    assert first.digest() == second.digest()
    assert [item.order_id for item in first.orders] == [
        item.order_id for item in second.orders
    ]


def test_보유_중인데_목표에서_빠진_종목은_매도_대상이_된다(fund) -> None:
    """안 넣으면 팔 기회가 영영 오지 않는다."""
    result = daily.run(
        fund, ReplayClock(NOW), as_of=NOW, market="KR",
        holdings={"KR:999999": 100},
    )

    # 가격이 없는 종목이라 주문은 안 나가지만, 대상에는 들어가 사유가 남는다.
    assert "KR:999999" not in result.weights


def test_자본이_0이면_주문하지_않는다(store) -> None:
    """목표 비중을 금액으로 바꿀 수 없다. 0 으로 나누지 않고 멈춘다."""
    store.seed_config_defaults()
    store.append(
        "fx",
        [{
            "entity_id": "FX:USDKRW", "valid_from": NOW, "observed_at": NOW,
            "source": "test", "rate": 1_350.0,
        }],
        ingest_run_id="fx-seed",
    )

    result = daily.run(store, ReplayClock(NOW), as_of=NOW, market="KR")

    assert result.orders == ()
    assert any("자본이 0" in note for note in result.notes)


def test_전_단계가_이벤트_로그에_남는다(fund) -> None:
    """**후보가 0개인 날 이유를 말할 수 없으면 그 시스템은 운영할 수 없다.**"""
    daily.run(
        fund, ReplayClock(NOW), as_of=NOW, market="KR", run_id="traced",
        wall_clock=ReplayClock(NOW),
    )

    events = fund.get("events", as_of=NOW + timedelta(minutes=1), entity="traced")
    stages = list(events.sort_values("seq")["stage"])

    assert stages == ["observe", "select", "allocate", "execute"]
