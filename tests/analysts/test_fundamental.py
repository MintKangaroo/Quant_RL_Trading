"""Fundamental Analyst.

여기서 고정하는 것 둘. 둘 다 틀리면 **그럴듯한 가짜 알파**가 나온다 —
IC 가 낮게 나와 버려지는 게 아니라 오히려 좋아 보인다는 점이 위험하다.

1. **Q4 는 연간 누적이다.** 그대로 쓰면 매년 4분기에 매출이 4배로 뛴다.
   전 종목에 같은 시기에 같은 방향으로 생기는 왜곡이라 눈에 안 띈다.
2. **재무는 회계기간 종료 45~69일 뒤에 공시된다.** 게이트가 막아 주지만,
   Analyst 가 게이트를 우회하면 그 순간 미래를 본다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from quant_rl_trading.analysts.fundamental import (
    FundamentalAnalyst,
    to_quarterly,
    trailing_twelve_months,
)
from quant_rl_trading.collectors.market_hours import Market
from quant_rl_trading.replay.clock import ReplayClock

NOW = datetime(2026, 8, 12, tzinfo=UTC)

QUARTER_END = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
#: 공시는 회계기간 종료 45일 뒤. 실측값이다.
FILING_LAG = timedelta(days=45)


def fundamental_row(
    entity: str, year: int, quarter: int, metric: str, value: float
) -> dict[str, object]:
    month, day = QUARTER_END[quarter]
    period_end = datetime(year, month, day, tzinfo=UTC)
    return {
        "entity_id": entity,
        "valid_from": period_end,
        "observed_at": period_end + FILING_LAG,
        "source": "dart",
        "market": "KR",
        "metric": metric,
        "value": value,
        "fiscal_period": f"{year}Q{quarter}",
        "report_type": "dart_cfs",
    }


def universe_row(entity: str, moment: datetime) -> dict[str, object]:
    return {
        "entity_id": entity, "valid_from": moment, "observed_at": moment,
        "source": "test", "market": "KR", "name": entity,
        "is_listed": True, "is_tradable": True, "delisted_on": None,
    }


# -- Q4 누적 복원 ------------------------------------------------------------------


def test_q4_is_annual_cumulative_and_gets_restored() -> None:
    """실측: 삼성전자 2024 매출 Q1=71.9 Q2=74.1 Q3=79.1 Q4=300.9(조원).

    Q4 는 연간 누적이라 실제 4분기는 300.9 - (71.9+74.1+79.1) = 75.8 이다.
    복원하지 않으면 4분기 매출이 4배로 뛴다.
    """
    frame = pd.DataFrame([
        fundamental_row("KR:005930", 2024, q, "revenue", v)
        for q, v in [(1, 71.9), (2, 74.1), (3, 79.1), (4, 300.9)]
    ])

    restored = to_quarterly(frame)
    q4 = restored[restored["quarter"] == 4].iloc[0]["value"]

    assert q4 == pytest.approx(75.8, abs=0.01)


def test_q4_is_dropped_when_earlier_quarters_are_missing() -> None:
    """복원할 수 없으면 버린다. 4배 부풀린 값을 남기는 것보다 없는 편이 낫다."""
    frame = pd.DataFrame([
        fundamental_row("KR:000100", 2024, 1, "revenue", 10.0),
        fundamental_row("KR:000100", 2024, 4, "revenue", 100.0),   # Q2·Q3 없음
    ])

    restored = to_quarterly(frame)

    assert set(restored["quarter"]) == {1}


def test_balance_sheet_items_are_not_touched() -> None:
    """자산·자본은 시점 잔액이다. 누적 개념이 없으므로 빼면 안 된다."""
    frame = pd.DataFrame([
        fundamental_row("KR:005930", 2024, q, "total_assets", v)
        for q, v in [(1, 470.9), (2, 485.8), (3, 491.3), (4, 514.5)]
    ])

    restored = to_quarterly(frame)
    q4 = restored[restored["quarter"] == 4].iloc[0]["value"]

    assert q4 == pytest.approx(514.5)


def test_ttm_sums_four_quarters() -> None:
    """분기 하나만 보면 계절성이 신호를 덮는다."""
    frame = pd.DataFrame([
        fundamental_row("KR:000100", 2024, q, "revenue", 10.0) for q in (1, 2, 3)
    ] + [fundamental_row("KR:000100", 2024, 4, "revenue", 45.0)])  # 연간 → Q4=15

    series = trailing_twelve_months(to_quarterly(frame))
    last = series[series["quarter"] == 4].iloc[0]

    assert last["ttm"] == pytest.approx(45.0)   # 10+10+10+15


# -- 공시 지연 --------------------------------------------------------------------


@pytest.fixture
def seeded(store):  # type: ignore[no-untyped-def]
    """두 종목 × 8분기. 하나는 고ROE, 하나는 저ROE."""
    store.seed_config_defaults()
    rows: list[dict[str, object]] = []
    universe: list[dict[str, object]] = []

    for entity, margin in (("KR:000100", 0.20), ("KR:000200", 0.02)):
        for year in (2025, 2026):
            for quarter in (1, 2, 3, 4):
                if year == 2026 and quarter > 2:
                    continue
                revenue = 100.0 * (1.1 if year == 2026 else 1.0)
                cumulative = quarter == 4
                factor = 4.0 if cumulative else 1.0
                rows += [
                    fundamental_row(entity, year, quarter, "revenue", revenue * factor),
                    fundamental_row(entity, year, quarter, "operating_income",
                                    revenue * margin * factor),
                    fundamental_row(entity, year, quarter, "net_income",
                                    revenue * margin * factor),
                    fundamental_row(entity, year, quarter, "total_equity", 500.0),
                    fundamental_row(entity, year, quarter, "total_liabilities", 200.0),
                    fundamental_row(entity, year, quarter, "total_assets", 700.0),
                    fundamental_row(entity, year, quarter, "current_assets", 300.0),
                    fundamental_row(entity, year, quarter, "current_liabilities", 150.0),
                ]
        universe.append(universe_row(entity, NOW - timedelta(days=1)))

    store.append("fundamentals", rows, ingest_run_id="f-seed")
    store.append("universe", universe, ingest_run_id="u-seed")
    return store


def run(seeded, as_of: datetime = NOW):  # type: ignore[no-untyped-def]
    analyst = FundamentalAnalyst(seeded, ReplayClock(as_of), market=Market.KR)
    return {signal.entity_id: signal for signal in analyst.run(as_of)}


def test_higher_margin_scores_higher(seeded) -> None:
    signals = run(seeded)

    assert signals["KR:000100"].score > signals["KR:000200"].score


def test_financials_are_invisible_before_the_filing(seeded) -> None:
    """2026Q2 는 6/30 에 유효하지만 8/14 에야 알 수 있다.

    게이트가 막는다. Analyst 가 게이트를 우회하면 그 순간 미래를 본다.
    """
    period_end = datetime(2026, 6, 30, tzinfo=UTC)
    analyst = FundamentalAnalyst(seeded, ReplayClock(NOW), market=Market.KR)

    # 회계기간 종료 직후 — 아직 공시 전이다
    early = analyst.store.get(
        "fundamentals", as_of=period_end + timedelta(days=1), lookback=900
    )
    assert (early["fiscal_period"] == "2026Q2").sum() == 0

    # 공시 후
    late = analyst.store.get(
        "fundamentals", as_of=period_end + FILING_LAG + timedelta(days=1), lookback=900
    )
    assert (late["fiscal_period"] == "2026Q2").sum() > 0


def test_no_financials_yields_no_signals(store) -> None:
    store.seed_config_defaults()

    assert FundamentalAnalyst(store, ReplayClock(NOW), market=Market.KR).run(NOW) == []


# -- 자본잠식 ----------------------------------------------------------------------


def test_negative_equity_does_not_produce_positive_roe(store) -> None:
    """자본잠식 기업은 자본이 음수다. 그대로 나누면 적자가 양수 ROE 가 된다.

    부호가 두 번 뒤집혀서 **가장 위험한 기업이 가장 높은 점수**를 받는다.
    """
    store.seed_config_defaults()
    rows = []
    for entity, equity, income in (("KR:000100", 500.0, 50.0), ("KR:000900", -100.0, -80.0)):
        for year, quarter in ((2025, 1), (2025, 2), (2025, 3), (2026, 1)):
            rows += [
                fundamental_row(entity, year, quarter, "net_income", income),
                fundamental_row(entity, year, quarter, "total_equity", equity),
                fundamental_row(entity, year, quarter, "revenue", 100.0),
                fundamental_row(entity, year, quarter, "operating_income", income),
            ]
    store.append("fundamentals", rows, ingest_run_id="f-neg")
    store.append(
        "universe",
        [universe_row(e, NOW - timedelta(days=1)) for e in ("KR:000100", "KR:000900")],
        ingest_run_id="u-neg",
    )

    analyst = FundamentalAnalyst(store, ReplayClock(NOW), market=Market.KR)
    features = analyst.features(NOW)

    # 자본잠식 종목의 ROE 는 비워지고 중앙값(z=0)으로 채워진다.
    assert features.loc["KR:000900", "roe"] <= features.loc["KR:000100", "roe"]


# -- 조용히 죽는 피처 --------------------------------------------------------------


def test_filings_before_the_period_end_are_rejected(store) -> None:
    """회계기간이 끝나기 전에 공시될 수는 없다.

    12월 결산이 아닌 회사인데 수집기가 fiscal_period 를 **요청값**으로 붙여
    오라벨한 행이다. 실측에서 2026Q3 의 관측시각이 2026-03-30 으로 찍혀 있었다.
    """
    store.seed_config_defaults()
    period_end = datetime(2026, 9, 30, tzinfo=UTC)
    store.append(
        "fundamentals",
        [{
            **fundamental_row("KR:000100", 2026, 3, "revenue", 100.0),
            "observed_at": period_end - timedelta(days=180),   # 기간 종료 전 공시
        }],
        ingest_run_id="f-impossible",
    )
    store.append("universe", [universe_row("KR:000100", NOW)], ingest_run_id="u-imp")

    analyst = FundamentalAnalyst(store, ReplayClock(NOW), market=Market.KR)

    assert analyst.features(NOW).empty


def test_window_must_cover_whole_calendar_years(seeded) -> None:
    """Q4 복원이 같은 해 Q1~Q3 를 요구한다.

    창이 달력연도를 온전히 품지 못하면 매년 Q4 가 버려져 8분기가 안 채워지고,
    **YoY 피처가 조용히 0이 된다.** 점수는 계속 나오므로 눈치채기 어렵다.
    실측에서 800일 창일 때 revenue_growth 의 표준편차가 정확히 0이었다.
    """
    from quant_rl_trading.analysts.fundamental import LOOKBACK_DAYS

    # 8분기(730일) + 달력연도 여유. 이 값이 730 근처로 줄면 YoY 가 죽는다.
    assert LOOKBACK_DAYS >= 1095
