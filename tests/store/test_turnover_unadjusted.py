"""명단용 거래대금은 원주가 × 원 거래량이다 — 미래 분할이 과거 명단을 못 흔든다 (2026-10-07).

미장 연구 명단(`tools/trial_us_kit.us_panel`, 20일 거래대금 상위 1,000)이 **보정 종가 × 원 거래량**으로 골라졌다.
창 전체를 ``as_of`` 한 번(창 끝)으로 읽으니 그 뒤의 분할이 과거 명단을 정했다 — 나중에 역분할한 동전주는 과거 보정가가
부풀어 들어오고, 나중에 액면분할한 대형주는 빠졌다(docs/diag/us-alpha.md §1 정정, data-contract §4-1).

합성 종목 셋으로 못 박는다. 셋 다 가격·거래량이 한 번도 안 움직인다 — 진짜 거래대금은 분할 전후로 같아야 한다.

- ``US:BIG``   주가 $100 · 일 100만 주 = $1억/일 — 창 끝에 **10:1 액면분할**(배율 0.1)
- ``US:MID``   주가 $50 · 일 100만 주 = $5천만/일 — 사건 없음
- ``US:PENNY`` 주가 $1 · 일 100만 주 = $100만/일 — 창 끝에 **1:100 역분할**(배율 100)

옛 계산(보정 종가 × 원 거래량)으로 분할 뒤 ``as_of`` 에서 분할 전 세션의 상위 1 을 고르면 PENNY($1억/일로 부풀어)와
BIG($1천만/일로 쪼그라들어)이 뒤집혀 PENNY 가 MID 위로 올라온다. 새 계산은 ``as_of`` 와 무관하게 BIG > MID > PENNY 다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from quant_rl_trading.store import Store
from quant_rl_trading.store.prices import read_prices, wide_close_and_turnover

END = datetime(2026, 6, 30, 20, tzinfo=UTC)
#: 분할 발효 세션(인덱스). 그 앞 30세션의 명단을 분할 뒤 as_of 로 다시 고른다.
SPLIT_AT = 40
N_SESSIONS = 45

#: (종목, 분할 전 원주가, 배율) — 배율은 발효 세션에 적는다(None 이면 사건 없음).
NAMES = (("US:BIG", 100.0, 0.1), ("US:MID", 50.0, None), ("US:PENNY", 1.0, 100.0))
VOLUME = 1_000_000.0


def _sessions() -> list[datetime]:
    days: list[datetime] = []
    cursor = END
    while len(days) < N_SESSIONS:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    return sorted(days)


@pytest.fixture
def seeded(store: Store) -> tuple[Store, list[datetime]]:
    store.seed_config_defaults()
    days = _sessions()
    rows = []
    for entity, price, factor in NAMES:
        for index, day in enumerate(days):
            after = factor is not None and index >= SPLIT_AT
            close = price * factor if after else price
            # 분할하면 주식수가 늘어 거래량은 배율의 **역수**로 움직인다 — 오간 돈(원 거래대금)은 그대로다.
            volume = VOLUME / factor if after else VOLUME
            rows.append({
                "entity_id": entity, "valid_from": day, "observed_at": day, "source": "test", "market": "US",
                "open": close, "high": close, "low": close, "close": close,
                "volume": volume, "value": close * volume,
                "adj_factor": factor if (factor is not None and index == SPLIT_AT) else None,
            })
    store.append("prices", rows, ingest_run_id="seed-turnover")
    return store, days


def _top(turnover: pd.DataFrame, day) -> list[str]:
    return list(turnover.loc[day].dropna().sort_values(ascending=False).index)


def test_past_ranking_does_not_move_with_a_later_split(seeded: tuple[Store, list[datetime]]) -> None:
    store, days = seeded
    probe = days[SPLIT_AT - 1].date()                 # 분할 바로 전 세션
    before = days[SPLIT_AT - 1] + timedelta(hours=1)  # 분할을 아직 모르는 시각
    after = END + timedelta(hours=1)                  # 창 끝 — 분할을 아는 시각(회차 패널이 이렇게 구웠다)

    _, early = wide_close_and_turnover(store, as_of=before, lookback=90, market="US")
    _, late = wide_close_and_turnover(store, as_of=after, lookback=90, market="US")

    assert _top(early, probe) == ["US:BIG", "US:MID", "US:PENNY"]
    assert _top(late, probe) == ["US:BIG", "US:MID", "US:PENNY"]
    pd.testing.assert_series_equal(early.loc[probe], late.loc[probe].reindex(early.columns))
    assert late.loc[probe, "US:BIG"] == pytest.approx(100.0 * VOLUME)
    assert late.loc[probe, "US:PENNY"] == pytest.approx(1.0 * VOLUME)


def test_old_adjusted_product_did_flip_the_ranking(seeded: tuple[Store, list[datetime]]) -> None:
    """대조 — 옛 계산(보정 종가 × 원 거래량)은 같은 세션의 순위를 뒤집었다. 이 테스트가 깨지면 위 테스트는 아무것도 못 잡는다."""
    store, days = seeded
    probe = days[SPLIT_AT - 1].date()
    frame = read_prices(store, as_of=END + timedelta(hours=1), lookback=90, market="US",
                        columns=["close", "volume"], adjusted=True)
    frame["day"] = pd.to_datetime(frame["valid_from"]).dt.date
    close = frame.pivot_table(index="day", columns="entity_id", values="close", aggfunc="last")
    volume = frame.pivot_table(index="day", columns="entity_id", values="volume", aggfunc="last")
    old = (close * volume).rolling(20, min_periods=10).mean()
    assert _top(old, probe) == ["US:PENNY", "US:MID", "US:BIG"]


def test_returned_close_is_still_adjusted(seeded: tuple[Store, list[datetime]]) -> None:
    """수익 쪽은 그대로 보정가다 — 분할이 수익률로 새지 않는다."""
    store, _ = seeded
    close, _ = wide_close_and_turnover(store, as_of=END + timedelta(hours=1), lookback=90, market="US")
    ret = close.pct_change(fill_method=None).iloc[1:]
    assert ret.abs().max().max() == pytest.approx(0.0, abs=1e-12)
