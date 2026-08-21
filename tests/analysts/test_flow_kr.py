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
    LOOKBACK_DAYS,
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
                # 수집기가 잠정/확정을 가를 방법이 없다. 모르는 것은 null 이다.
                "is_final": None,
            })
    return rows


def price_rows(
    day: datetime, entities: list[str], *, splits: dict[str, float] | None = None
) -> list[dict[str, object]]:
    """일봉. ``value``(거래대금)를 함께 깐다 — flow_kr 의 분모가 그 컬럼이다.

    ``splits`` 는 그날 발효한 기업행위 배율이다. 원주가·원거래량·거래대금은
    건드리지 않는다. 창고가 저장하는 것이 원본이고, 보정은 읽을 때 걸린다.
    """
    splits = splits or {}
    return [{
        "entity_id": entity,
        "valid_from": day,
        "observed_at": day + timedelta(hours=7),
        "source": "test",
        "market": "KR",
        "open": 1000.0, "high": 1000.0, "low": 1000.0, "close": 1000.0,
        "volume": 10_000.0 * (index + 1),
        "value": 1000.0 * 10_000.0 * (index + 1),
        "adj_factor": splits.get(entity, 1.0),
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


def seed(store, *, tail: list[int], splits: dict[datetime, dict[str, float]] | None = None):  # type: ignore[no-untyped-def]
    """완결 세션을 깔고, 마지막 며칠만 ``tail`` 만큼의 종목으로 좁힌다.

    ``tail=[]`` 이면 전부 완결이다. ``tail=[2]`` 면 마지막 하루만 2종목.
    ``splits`` 는 세션 → {종목: 배율} 의 기업행위다.
    """
    splits = splits or {}
    full = [f"KR:{i:06d}" for i in range(20)]
    days = sessions()
    widths = [len(full)] * (len(days) - len(tail)) + tail

    for index, (day, width) in enumerate(zip(days, widths, strict=True)):
        # 가격과 유니버스는 늘 전 종목이다. 좁아지는 것은 수급뿐이다.
        store.append(
            "prices", price_rows(day, full, splits=splits.get(day)), ingest_run_id=f"p-{index}"
        )
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


def test_turnover_denominator_ignores_corporate_actions(store, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """분모는 **원본 거래대금**이다. 보정가 × 원거래량이 아니다.

    ``price_panel`` 은 보정가를 준다. 보정은 가격에만 곱하고 거래량에는 안
    곱하므로(store/prices.py 의 ADJUSTED_COLUMNS), 둘을 곱하면 기업행위가
    있었던 종목의 분모만 배율만큼 어긋난다. 실측으로 45일 창 2,881종목 중
    65종목(2.3%)이 그랬고 배율은 0.50~5.31 이었다 — 그 종목들은 수급 비율이
    배율만큼 튀어 횡단면 순위의 끝으로 밀렸다.

    수급은 하나도 안 바뀌었는데 분할 하나로 점수가 바뀌면 그것은 신호가
    아니라 사고다. 그래서 두 창고의 피처가 **글자 하나까지 같아야** 한다.
    """
    from quant_rl_trading.store import Store

    target = "KR:000005"
    # 창 안쪽에 둔다. 프레임의 마지막 세션은 누적곱이 비어 배율이 안 먹는다.
    split_on = sessions()[-3]

    seed(store, tail=[])
    plain = analyst(store).features(NOW)

    split = Store(root=tmp_path / "split")
    seed(split, tail=[], splits={split_on: {target: 0.5}})

    # 보정이 실제로 걸리는지부터 확인한다. 안 걸리면 이 테스트는 아무것도
    # 재지 않으면서 통과한다.
    panel = analyst(split).price_panel(NOW, lookback=LOOKBACK_DAYS)
    assert analyst(split).wide(panel, "close")[target].nunique() > 1

    pd.testing.assert_frame_equal(plain, analyst(split).features(NOW))


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
