"""flow_kr — 커버리지 가드가 무엇을 버리고 무엇을 살리나.

여기서 고정하는 것은 **침묵과 거절의 차이**다.

수급은 종목 축으로 들어온다. 991종목을 다 받기 전에 수집이 끊기면 마지막
하루가 168종목짜리로 남는다. 그 하루 때문에 창 전체를 버리면 Analyst 는
매일 "신호 0건" 을 낸다 — 실제로 2026-08 내내 그랬고, 로그만 봐서는 "수급이
안 먹혔다" 와 구별되지 않는다.

덜 찬 꼬리는 잘라내고 완결된 창으로 잰다. 다만 사흘 넘게 덜 차 있으면 그건
수집이 멈춘 것이므로 그때는 정말로 아무것도 내지 않는다. 낡은 것을 오늘
것처럼 말하는 쪽이 침묵보다 나쁘다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from quant_rl_trading.analysts.flow_kr import (
    FOREIGN,
    INSTITUTION,
    RETAIL,
    FlowKrAnalyst,
)
from quant_rl_trading.collectors.market_hours import Market
from quant_rl_trading.replay.clock import ReplayClock

NOW = datetime(2026, 8, 13, 6, 40, tzinfo=UTC)

INVESTORS = (FOREIGN, INSTITUTION, RETAIL)
#: 창이 20세션 누적을 볼 수 있을 만큼은 있어야 한다.
SESSIONS = 25


def sessions(count: int = SESSIONS) -> list[datetime]:
    """NOW 직전의 연속 세션. 주말은 신경 쓰지 않는다 — 창고는 달력이 아니라
    들어온 날짜만 안다."""
    return [NOW.replace(hour=0, minute=0) - timedelta(days=count - i) for i in range(count)]


def flow_rows(day: datetime, entities: list[str]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index, entity in enumerate(entities):
        for investor in INVESTORS:
            rows.append({
                "entity_id": entity,
                "valid_from": day,
                # 그날 마감 후 공표. 게이트가 as_of 로 거른다.
                "observed_at": day + timedelta(hours=7),
                "source": "test",
                "market": "KR",
                "investor": investor,
                "net_value": float(index + 1) * (1.0 if investor == FOREIGN else -1.0),
                "net_volume": float(index + 1),
                "is_final": True,
            })
    return rows


def price_rows(day: datetime, entities: list[str]) -> list[dict[str, object]]:
    return [{
        "entity_id": entity,
        "valid_from": day,
        "observed_at": day + timedelta(hours=7),
        "source": "test",
        "market": "KR",
        "open": 1000.0, "high": 1000.0, "low": 1000.0, "close": 1000.0,
        "volume": 10_000.0 * (index + 1),
    } for index, entity in enumerate(entities)]


def universe_rows(day: datetime, entities: list[str]) -> list[dict[str, object]]:
    return [{
        "entity_id": entity,
        "valid_from": day,
        "observed_at": day + timedelta(hours=7),
        "source": "test",
        "market": "KR",
        "name": entity,
        "is_listed": True,
        "is_tradable": True,
        "delisted_on": None,
    } for entity in entities]


def seed(store, *, tail: list[int]):  # type: ignore[no-untyped-def]
    """완결 세션을 깔고, 마지막 며칠만 ``tail`` 만큼의 종목으로 좁힌다.

    ``tail=[]`` 이면 전부 완결이다. ``tail=[2]`` 면 마지막 하루만 2종목.
    """
    full = [f"KR:{i:06d}" for i in range(20)]
    days = sessions()
    widths = [len(full)] * (len(days) - len(tail)) + tail

    for index, (day, width) in enumerate(zip(days, widths, strict=True)):
        # 가격과 유니버스는 늘 전 종목이다. 좁아지는 것은 수급뿐이다.
        store.append("prices", price_rows(day, full), ingest_run_id=f"p-{index}")
        store.append("universe", universe_rows(day, full), ingest_run_id=f"u-{index}")
        store.append("flows", flow_rows(day, full[:width]), ingest_run_id=f"f-{index}")


def analyst(store):  # type: ignore[no-untyped-def]
    return FlowKrAnalyst(store, ReplayClock(NOW), market=Market.KR)


def test_full_window_scores(store) -> None:  # type: ignore[no-untyped-def]
    """멀쩡한 창은 당연히 점수가 나온다. 나머지 테스트의 기준선이다."""
    seed(store, tail=[])
    features = analyst(store).features(NOW)
    assert not features.empty
    assert len(features) == 20


def test_partial_last_session_is_trimmed_not_fatal(store) -> None:  # type: ignore[no-untyped-def]
    """마지막 하루가 덜 찼다고 창 전체를 버리지 않는다.

    이것이 매일 "신호 0건" 의 원인이었다. 잘라낸 뒤 남는 창은 완결된
    과거뿐이라 미래를 보지 않는다.
    """
    seed(store, tail=[2])
    features = analyst(store).features(NOW)
    assert not features.empty
    # 잘린 세션의 2종목이 아니라, 완결 세션의 전 종목이 대상이다.
    assert len(features) == 20


def test_collection_stopped_for_days_is_refused(store) -> None:  # type: ignore[no-untyped-def]
    """사흘째 덜 차 있으면 그건 부분 수집이 아니라 멈춘 수집이다.

    이때 점수를 내면 사흘 전 수급을 오늘의 것으로 말하게 된다.
    """
    seed(store, tail=[2, 2, 2])
    assert analyst(store).features(NOW).empty


def test_flows_absent_stays_silent(store) -> None:  # type: ignore[no-untyped-def]
    """수급 자체가 없으면 의견이 없다. 0 으로 채우지 않는다."""
    days = sessions()
    full = [f"KR:{i:06d}" for i in range(20)]
    for index, day in enumerate(days):
        store.append("prices", price_rows(day, full), ingest_run_id=f"p-{index}")
        store.append("universe", universe_rows(day, full), ingest_run_id=f"u-{index}")
    assert analyst(store).features(NOW).empty


@pytest.mark.parametrize("tail", [[2], []])
def test_covered_entities_only(store, tail) -> None:  # type: ignore[no-untyped-def]
    """수급이 관측된 종목만 의견 대상이다 — 나머지를 0 으로 채우면 IC 가
    동점 덩어리에 눌린다."""
    seed(store, tail=tail)
    features = analyst(store).features(NOW)
    assert set(features.index) <= {f"KR:{i:06d}" for i in range(20)}
