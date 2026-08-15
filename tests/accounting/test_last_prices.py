"""평가 가격은 **창 안의 마지막 유효 종가**다 — 마지막 행이 아니라.

## 왜 순서 하나에 테스트를 붙이나

`last_prices` 는 0 이하를 걸러 낸다. 문제는 **거르는 시점**이었다.

    latest = ...groupby("entity_id").tail(1)      # ① 마지막 행을 고르고
    return {... if float(row["close"]) > 0}       # ② 그 다음에 0 을 버린다

이러면 창의 마지막 세션이 종가 0 일 때 ①이 그 0 행을 뽑고 ②가 그걸 버리면서
**종목이 결과에서 통째로 사라진다.** 사라진 종목을 `nav.value` 가 만나면
"가격이 없다" 로 예외를 던진다 — 독스트링이 약속한 "창 안의 마지막 종가" 는
쓰이지도 못한다.

`> 0` 필터가 양쪽 순서에 다 있어서 **코드만 보면 똑같아 보인다.** 다음 사람이
"0 을 버리는 건데 순서가 무슨 상관이야" 하고 되돌리기 쉬운 자리라 못 박는다.

## 이게 가상의 사고가 아니다

KRX 는 휴장일에도 전 종목을 0 으로 채운 표를 준다. 방어가 생기기 전에
2026-06-03(지방선거)·2026-07-17 두 세션이 그렇게 적재됐고, 워크포워드
백테스트가 06-03 에서 `KeyError: 'KR:138930: 가격이 없다'` 로 죽었다.
그 두 날은 실제로 시세가 존재하지 않으므로(소스가 지금도 0 을 준다)
정정본으로 메울 수 없다 — **읽는 쪽이 견뎌야 한다.**
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from quant_rl_trading.accounting.snapshot import last_prices

pytestmark = pytest.mark.invariant

ENTITY = "KR:000100"
OTHER = "KR:000200"
DAY0 = datetime(2026, 6, 1, 6, 0, tzinfo=UTC)


def _row(entity: str, moment: datetime, close: float) -> dict[str, object]:
    return {
        "entity_id": entity, "valid_from": moment, "observed_at": moment,
        "source": "test", "market": "KR",
        "open": close, "high": close, "low": close, "close": close,
        "volume": 1000.0, "value": close * 1000.0, "adj_factor": None,
    }


@pytest.fixture
def holiday(store):  # type: ignore[no-untyped-def]
    """이틀 정상, 사흘째가 **전 종목 종가 0** 인 창고. 휴장일의 모양이다."""
    store.seed_config_defaults()
    rows = [
        _row(ENTITY, DAY0, 10_000.0),
        _row(OTHER, DAY0, 5_000.0),
        _row(ENTITY, DAY0 + timedelta(days=1), 11_000.0),
        _row(OTHER, DAY0 + timedelta(days=1), 5_500.0),
        # 휴장일 — KRX 가 0 으로 채운 표를 준 그 모양
        _row(ENTITY, DAY0 + timedelta(days=2), 0.0),
        _row(OTHER, DAY0 + timedelta(days=2), 0.0),
    ]
    store.append("prices", rows, ingest_run_id="p-seed")
    return store


def test_zero_close_falls_back_to_the_previous_session(holiday) -> None:
    """창의 마지막이 종가 0 이면 **그 전 세션 종가**를 쓴다."""
    prices = last_prices(
        holiday, as_of=DAY0 + timedelta(days=2, hours=12), entities=[ENTITY, OTHER]
    )

    assert prices == {ENTITY: 11_000.0, OTHER: 5_500.0}


def test_the_entity_does_not_disappear(holiday) -> None:
    """**사라지는 것이 예외의 원인이다.** 값보다 존재를 먼저 못 박는다.

    빠진 종목을 `nav.value` 가 만나면 KeyError 를 던지고, 백테스트는
    그 자리에서 죽는다. 실제로 그렇게 죽었다.
    """
    prices = last_prices(
        holiday, as_of=DAY0 + timedelta(days=2, hours=12), entities=[ENTITY, OTHER]
    )

    for entity in (ENTITY, OTHER):
        assert entity in prices, f"{entity} 가 사라졌다 — nav.value 가 여기서 터진다"


def test_a_normal_session_still_uses_that_session(holiday) -> None:
    """정상 세션에서는 당연히 그날 종가다. 폴백이 항상 이기면 그것도 버그다."""
    prices = last_prices(
        holiday, as_of=DAY0 + timedelta(days=1, hours=12), entities=[ENTITY, OTHER]
    )

    assert prices == {ENTITY: 11_000.0, OTHER: 5_500.0}
    earlier = last_prices(holiday, as_of=DAY0 + timedelta(hours=12), entities=[ENTITY])
    assert earlier == {ENTITY: 10_000.0}


def test_only_zeros_in_window_yields_nothing(holiday, store) -> None:
    """창 전체가 0 이면 돌려줄 값이 없다 — 지어내지 않는다.

    이때 `nav.value` 가 예외를 던지는 것은 **옳은 동작**이다. 0 으로 평가해
    NAV 를 조용히 떨어뜨리는 것보다 멈추는 편이 낫다.
    """
    store.seed_config_defaults()
    store.append(
        "prices",
        [_row(ENTITY, DAY0, 0.0), _row(ENTITY, DAY0 + timedelta(days=1), 0.0)],
        ingest_run_id="zeros",
    )

    prices = last_prices(store, as_of=DAY0 + timedelta(days=1, hours=12), entities=[ENTITY])

    assert prices == {}
