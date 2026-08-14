"""불변식 — ``entity_id`` 의 시장 접두어와 ``market`` 컬럼 값은 같아야 한다.

**실측 사고(2026-08-15).** `Backfiller._known_listed()` 가 창고의 `universe`
테이블을 시장으로 안 거르고 읽어서, KR 백필의 첫 세션이 US 종목 6,648개를
"직전엔 상장이었는데 오늘 명단엔 없다" 로 오인했다. `entity_id` 는 원래 값
("US:AA")을 그대로 물려받았지만 `market` 은 실행 인자(KR)로 덮여 찍혔다.

append-only 창고에서는 이런 값을 **나중에 되돌릴 방법이 없다.** `market` 은
조회의 WHERE 절에서 정정본 선택보다 먼저 걸리므로(reader.py `_scope`), 정정
행을 새로 얹어도 그 필터 자체가 정정 행을 걸러내고 원래의 잘못된 행만
살아남는다. 그래서 유일한 방어선은 **쓰기 시점**이다 — 이 테스트가 그걸
지킨다.
"""

from __future__ import annotations

import pytest

from quant_rl_trading.store.errors import SchemaViolation

pytestmark = pytest.mark.invariant

SOURCE = "test"


def _row(ts, **overrides):  # type: ignore[no-untyped-def]
    row = {
        "entity_id": "KR:005930",
        "valid_from": ts(2024, 3, 4),
        "observed_at": ts(2024, 3, 4, 9),
        "source": SOURCE,
        "market": "KR",
        "close": 100.0,
    }
    row.update(overrides)
    return row


def test_다른_시장_접두어는_거부된다(store, ts) -> None:  # type: ignore[no-untyped-def]
    """사고를 그대로 재현한다 — US 종목이 market="KR" 로 들어오면 막힌다."""
    with pytest.raises(SchemaViolation, match=r"entity_id.*market"):
        store.append(
            "prices", [_row(ts, entity_id="US:AA", market="KR")], ingest_run_id="bad-market",
        )


def test_일치하는_행은_그대로_통과한다(store, ts) -> None:  # type: ignore[no-untyped-def]
    store.append("prices", [_row(ts)], ingest_run_id="good-market")

    seen = store.get("prices", as_of=ts(2024, 3, 31))
    assert list(seen["entity_id"]) == ["KR:005930"]


def test_세그먼트가_셋이어도_첫_세그먼트만_본다(store, ts) -> None:  # type: ignore[no-untyped-def]
    """지수 entity_id 는 'KR:IDX:KOSPI' 처럼 콜론이 두 번 나온다."""
    row = {
        "entity_id": "KR:IDX:KOSPI",
        "valid_from": ts(2024, 3, 4),
        "observed_at": ts(2024, 3, 4, 9),
        "source": SOURCE,
        "market": "KR",
        "board": "KOSPI",
        "close": 2650.0,
    }
    store.append("indices", [row], ingest_run_id="idx-ok")

    seen = store.get("indices", as_of=ts(2024, 3, 31))
    assert list(seen["entity_id"]) == ["KR:IDX:KOSPI"]


def test_market이_비어있으면_검사하지_않는다(store, ts) -> None:  # type: ignore[no-untyped-def]
    """market 을 늘 채우지 않는 테이블도 있다 — 비교할 게 없으면 건너뛴다."""
    store.append("prices", [_row(ts, market=None)], ingest_run_id="no-market")

    seen = store.get("prices", as_of=ts(2024, 3, 31))
    assert list(seen["entity_id"]) == ["KR:005930"]


def test_analyst_weights는_예외다(store, ts) -> None:  # type: ignore[no-untyped-def]
    """entity_id 가 종목이 아니라 analyst 이름이다 — 이 규칙이 안 맞는
    테이블이라 TableSpec.market_prefixed_entity=False 로 명시적으로 뺐다."""
    row = {
        "entity_id": "risk",
        "valid_from": ts(2024, 3, 4),
        "observed_at": ts(2024, 3, 4, 9),
        "source": SOURCE,
        "market": "KR",
        "analyst_version": "risk-v0.1.0",
        "weight": 1.0,
        "ic": 0.05,
        "ic_threshold": 0.03,
        "sample_days": 60,
        "passed": True,
    }
    store.append("analyst_weights", [row], ingest_run_id="analyst-ok")

    seen = store.get("analyst_weights", as_of=ts(2024, 3, 31))
    assert list(seen["entity_id"]) == ["risk"]


def test_거부되면_배치_전체가_안_들어간다(store, ts) -> None:  # type: ignore[no-untyped-def]
    """한 행이라도 틀리면 나머지도 저장되지 않는다 — 부분 저장은 되돌릴 수 없다."""
    good = _row(ts)
    bad = _row(ts, entity_id="US:AA", market="KR", valid_from=ts(2024, 3, 5))

    with pytest.raises(SchemaViolation):
        store.append("prices", [good, bad], ingest_run_id="half-market")

    assert store.get("prices", as_of=ts(2024, 3, 31)).empty
    assert not store.ingest_run_recorded("prices", "half-market")
