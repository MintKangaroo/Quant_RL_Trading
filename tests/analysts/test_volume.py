"""volume — chart 에서 떼어낸 거래량 축 (#27).

여기서 고정하는 것은 두 가지다.

1. **정의가 chart 에 있던 그대로다.** 5일 평균 / 60일 평균 에서 1 을 뺀 값. 이 값이
   바뀌면 갈라낸 근거였던 측정(IC +0.0140 · t +2.28, `signal-combination.md`
   §6)이 이 코드의 성적이 아니게 된다. 그래서 손으로 계산한 값과 대조한다.
2. **없는 것을 0 으로 지어내지 않는다.** 창고가 비었거나 관측이 얕으면 빈
   프레임이다. 거래량이 한 번도 없던 종목은 점수를 받지 않는다 — 평소가
   없는 종목에 "평소 대비" 를 말할 수 없다.

그리고 chart 쪽 회귀 가드 하나 — 떼어낸 피처가 저쪽에 다시 살아나면 같은
신호를 두 번 세게 된다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from quant_rl_trading.analysts.chart import WEIGHTS as CHART_WEIGHTS
from quant_rl_trading.analysts.chart import ChartAnalyst
from quant_rl_trading.analysts.volume import VolumeAnalyst
from quant_rl_trading.collectors.market_hours import Market
from quant_rl_trading.replay.clock import ReplayClock

NOW = datetime(2026, 8, 13, 6, 40, tzinfo=UTC)

#: 60일 평소를 말할 수 있을 만큼. 최소 관측 가드(60)보다 넉넉하게.
SESSIONS = 70

#: 급증 창. Analyst 의 정의와 같은 값이라 여기서 다시 적는다 — 한쪽만
#: 바뀌면 테스트가 먼저 깨져야 한다.
SHORT = 5
LONG = 60


def sessions(count: int = SESSIONS) -> list[datetime]:
    return [NOW.replace(hour=0, minute=0) - timedelta(days=count - i) for i in range(count)]


def rows(day: datetime, volumes: dict[str, float]) -> list[dict[str, object]]:
    return [{
        "entity_id": entity,
        "valid_from": day,
        # 그날 마감 후 공표. 게이트가 as_of 로 거른다.
        "observed_at": day + timedelta(hours=7),
        "source": "test",
        "market": "KR",
        "open": 1000.0, "high": 1000.0, "low": 1000.0, "close": 1000.0,
        "volume": volume,
    } for entity, volume in volumes.items()]


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


def seed(store, series: dict[str, list[float | None]]) -> None:  # type: ignore[no-untyped-def]
    """종목별 거래량 시계열을 심는다. ``None`` 인 날은 그 종목의 행이 없다.

    가격은 전 종목 고정이다 — 이 Analyst 는 가격을 안 본다.
    """
    days = sessions()
    for index, day in enumerate(days):
        volumes = {
            entity: values[index]
            for entity, values in series.items()
            if values[index] is not None
        }
        if not volumes:
            continue
        store.append("prices", rows(day, volumes), ingest_run_id=f"p-{index}")
        store.append(
            "universe", universe_rows(day, list(volumes)), ingest_run_id=f"u-{index}"
        )


def analyst(store):  # type: ignore[no-untyped-def]
    return VolumeAnalyst(store, ReplayClock(NOW), market=Market.KR)


def flat(level: float) -> list[float]:
    return [level] * SESSIONS


def surge(base: float, recent: float) -> list[float]:
    return [base] * (SESSIONS - SHORT) + [recent] * SHORT


def test_빈_창고는_빈_프레임이다(store) -> None:  # type: ignore[no-untyped-def]
    """관측이 없으면 침묵한다. 0 은 "평소와 같다" 는 의견이라 지어내면 안 된다."""
    assert analyst(store).features(NOW).empty
    assert analyst(store).run(NOW) == []


def test_급증한_종목이_위로_간다(store) -> None:  # type: ignore[no-untyped-def]
    """세 종목의 가격은 전부 같다. 다른 것은 거래량뿐이다."""
    seed(store, {
        "KR:000000": surge(1_000.0, 5_000.0),   # 몰렸다
        "KR:000001": flat(1_000.0),             # 평소 그대로
        "KR:000002": surge(1_000.0, 200.0),     # 식었다
    })

    scores = analyst(store).raw_score(analyst(store).features(NOW))

    assert scores["KR:000000"] > scores["KR:000001"] > scores["KR:000002"], (
        f"거래량 급증 순서가 안 맞는다: {scores.to_dict()}"
    )


def test_정의는_5일_대_60일_배율_그대로다(store) -> None:  # type: ignore[no-untyped-def]
    """chart 에 있던 식과 같은 값이어야 한다 — 손으로 계산해 대조한다.

    이 숫자가 달라지면 `signal-combination.md` §6 의 IC +0.0140 은 더 이상
    이 코드의 성적이 아니다.
    """
    seed(store, {
        "KR:000000": surge(1_000.0, 5_000.0),
        "KR:000001": flat(1_000.0),
    })
    long_mean = ((LONG - SHORT) * 1_000.0 + SHORT * 5_000.0) / LONG
    expected = 5_000.0 / long_mean - 1.0

    # rank_score 가 씌워지기 전 원값을 같은 식으로 다시 만든다.
    prices = analyst(store).price_panel(NOW, lookback=130)
    volume = VolumeAnalyst.wide(prices, "volume")
    measured = volume.tail(SHORT).mean() / volume.tail(LONG).mean() - 1.0

    assert measured["KR:000000"] == pytest.approx(expected)
    assert measured["KR:000001"] == pytest.approx(0.0)


def test_관측이_얕은_종목은_빠진다(store) -> None:  # type: ignore[no-untyped-def]
    """상장한 지 얼마 안 된 종목에는 "평소" 가 없다. 감점이 아니라 배제다."""
    thin: list[float | None] = [None] * (SESSIONS - 30) + [1_000.0] * 30
    seed(store, {
        "KR:000000": flat(1_000.0),
        "KR:000001": flat(2_000.0),
        "KR:000002": thin,
    })

    features = analyst(store).features(NOW)

    assert not features.empty
    assert "KR:000002" not in features.index


def test_거래량이_0_뿐이면_점수를_안_낸다(store) -> None:  # type: ignore[no-untyped-def]
    """분모가 0 이면 배율이 무한이다. inf 를 점수로 흘리면 그 하나가 횡단면
    순위를 통째로 끌고 간다 — 창고의 종가 0 세션이 그랬던 것과 같은 사고다."""
    seed(store, {
        "KR:000000": flat(1_000.0),
        "KR:000001": flat(2_000.0),
        "KR:000002": flat(0.0),
    })

    features = analyst(store).features(NOW)
    scores = analyst(store).raw_score(features)

    assert "KR:000002" not in features.index
    assert scores.notna().all()


def test_결측_거래량은_중앙값_자리다(store) -> None:  # type: ignore[no-untyped-def]
    """행이 있는 날만으로 배율을 낸다. 앞뒤로 채우면 미래를 본다."""
    gapped: list[float | None] = flat(1_000.0)  # type: ignore[assignment]
    for index in range(SESSIONS - 3, SESSIONS):
        gapped[index] = None
    seed(store, {
        "KR:000000": surge(1_000.0, 5_000.0),
        "KR:000001": flat(1_000.0),
        "KR:000002": gapped,
    })

    features = analyst(store).features(NOW)

    assert features.notna().all().all(), "결측이 점수로 새어 나갔다"


def test_chart_는_거래량을_다시_보지_않는다(store) -> None:  # type: ignore[no-untyped-def]
    """떼어낸 피처가 저쪽에 되살아나면 같은 신호를 두 번 세게 된다."""
    assert "volume_surge" not in CHART_WEIGHTS

    seed(store, {"KR:000000": surge(1_000.0, 5_000.0), "KR:000001": flat(1_000.0)})
    chart = ChartAnalyst(store, ReplayClock(NOW), market=Market.KR)

    assert "volume_surge" not in chart.features(NOW).columns


def test_등록된_이름이_하나뿐이다() -> None:
    """이름이 어긋나면 등록은 됐는데 가중치가 영영 안 붙는다 — 창고의
    `analyst_weights` 키가 이 문자열이다."""
    from quant_rl_trading.session.signals import SCORERS
    from tools.measure_ic import ANALYSTS

    assert ANALYSTS["volume"] is VolumeAnalyst
    assert SCORERS[Market.KR]["volume"] is VolumeAnalyst
    assert SCORERS[Market.US]["volume"] is VolumeAnalyst
    assert VolumeAnalyst.name == "volume"
