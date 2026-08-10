"""미래 훔쳐보기 — 가짜 미래 행을 심고 과거 조회에서 나오지 않는지 확인한다.

look-ahead 는 코드리뷰로 못 막는다. 구조로 막는다.
게이트가 ``observed_at <= as_of`` 를 강제하지 못하면 이 프로젝트의 모든
백테스트 숫자가 거짓이 된다.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

pytestmark = pytest.mark.invariant

SOURCE = "test"


def test_future_observation_is_invisible(store, ts) -> None:  # type: ignore[no-untyped-def]
    store.append(
        "prices",
        [
            {
                "entity_id": "KR:005930",
                "valid_from": ts(2024, 3, 4),
                "observed_at": ts(2024, 3, 4, 9),
                "source": SOURCE,
                "market": "KR",
                "close": 100.0,
            },
            {
                "entity_id": "KR:005930",
                "valid_from": ts(2024, 3, 5),
                "observed_at": ts(2024, 3, 5, 9),
                "source": SOURCE,
                "market": "KR",
                "close": 200.0,
            },
        ],
        ingest_run_id="run-a",
    )

    seen = store.get("prices", as_of=ts(2024, 3, 4, 18))

    assert list(seen["close"]) == [100.0]


def test_backdated_valid_from_does_not_leak(store, ts) -> None:  # type: ignore[no-untyped-def]
    """유효시점이 과거라도 관측이 미래면 보이지 않는다.

    정정공시가 대표적이다. 2024-03-01 자 사실을 3월 10일에 알았다면,
    3월 5일 시점의 나는 그것을 몰랐다.
    """
    store.append(
        "fundamentals",
        [
            {
                "entity_id": "KR:005930",
                "valid_from": ts(2024, 3, 1),
                "observed_at": ts(2024, 3, 10),
                "source": SOURCE,
                "metric": "eps",
                "value": 1234.0,
                "fiscal_period": "2023Q4",
                "report_type": "annual",
            }
        ],
        ingest_run_id="run-b",
    )

    assert store.get("fundamentals", as_of=ts(2024, 3, 5)).empty
    assert len(store.get("fundamentals", as_of=ts(2024, 3, 11))) == 1


def test_pre_announced_fact_is_visible_before_it_takes_effect(store, ts) -> None:  # type: ignore[no-untyped-def]
    """valid_from > observed_at 은 정상이다.

    실적발표 예정일·지수 리밸런싱 발효일이 여기 해당한다. 이걸 미래
    데이터로 오인해 잘라내면, 실제로 알 수 있었던 정보를 버리게 된다.
    """
    store.append(
        "universe",
        [
            {
                "entity_id": "KR:000660",
                "valid_from": ts(2024, 6, 1),
                "observed_at": ts(2024, 3, 4),
                "source": SOURCE,
                "market": "KR",
                "name": "SK하이닉스",
                "is_listed": True,
                "is_tradable": True,
                "delisted_on": None,
            }
        ],
        ingest_run_id="run-c",
    )

    assert len(store.get("universe", as_of=ts(2024, 3, 5))) == 1


@pytest.fixture
def ten_days(store, ts):  # type: ignore[no-untyped-def]
    store.append(
        "prices",
        [
            {
                "entity_id": "KR:005930",
                "valid_from": ts(2024, 3, day),
                "observed_at": ts(2024, 3, day, 9),
                "source": SOURCE,
                "market": "KR",
                "close": float(day),
            }
            for day in range(1, 11)
        ],
        ingest_run_id="run-d",
    )
    return store


def test_timedelta_lookback_is_exact(ten_days, ts) -> None:  # type: ignore[no-untyped-def]
    """timedelta 는 정확히 그만큼 이전의 '순간' 이다.

    18시에 3일을 빼면 7일 18시이고, 7일 00시의 봉은 창 밖이다.
    """
    seen = ten_days.get("prices", as_of=ts(2024, 3, 10, 18), lookback=timedelta(days=3))

    assert list(seen["close"]) == [8.0, 9.0, 10.0]


def test_int_lookback_is_calendar_days(ten_days, ts) -> None:  # type: ignore[no-untyped-def]
    """정수는 달력일이다. 같은 날 안에서는 조회 시각에 따라 창이 흔들리지 않는다.

    (관측 이전인 09시 전에는 그날 봉 자체가 안 보인다. 그건 lookback 이 아니라
    as_of 게이트가 하는 일이고, 아래 두 시각은 둘 다 관측 이후다.)
    """
    at_morning = ten_days.get("prices", as_of=ts(2024, 3, 10, 10), lookback=3)
    at_dusk = ten_days.get("prices", as_of=ts(2024, 3, 10, 18), lookback=3)

    assert list(at_dusk["close"]) == [7.0, 8.0, 9.0, 10.0]
    assert list(at_morning["close"]) == list(at_dusk["close"])


def test_entity_filter_does_not_relax_the_as_of_gate(store, ts) -> None:  # type: ignore[no-untyped-def]
    store.append(
        "prices",
        [
            {
                "entity_id": "US:AAPL",
                "valid_from": ts(2024, 3, 20),
                "observed_at": ts(2024, 3, 20, 21),
                "source": SOURCE,
                "market": "US",
                "close": 999.0,
            }
        ],
        ingest_run_id="run-e",
    )

    assert store.get("prices", as_of=ts(2024, 3, 19), entity="US:AAPL").empty
