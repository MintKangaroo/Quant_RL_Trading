"""휴장일 종가 0 세션이 수익률·상관·변동성을 오염시키지 않는다.

## 왜 "std 가 nan 이 아니다" 로는 부족한가

종가 0 이 한 행 섞이면 ``pct_change`` 가 그 자리에서 ``-1.0`` 을, 다음
자리에서 ``+inf`` 를 낸다. **전 종목이 같은 날 동시에 -100%** 이므로 그 공통
하루가 60일 상관을 통째로 지배한다 — 서로 아무 관계 없는 종목들이 거의
완전상관으로 보인다.

이것이 실제로 한 일: 상관행렬이 부풀자 후보 절반이 음수 알파로 뒤집혔고,
Allocator 가 살아남은 소수에 비중을 몰아 MDD 를 -15.9%p 밀어 냈다. 그러니
여기서 지켜야 할 것은 "nan 이 아니다" 가 아니라 **"관계없는 것들이 관계있어
보이지 않는다"** 다.

실측(실제 창고, KR 300종목, 2026-08-14 기준):

    있는 그대로   평균 쌍상관 +0.920,  |corr| > 0.7 인 쌍 94.0%
    0 행 제거     평균 쌍상관 +0.302,  |corr| > 0.7 인 쌍  3.5%

## 합성 데이터와 실측을 둘 다 둔다

합성만 두면 현실을 말해 주지 않고(상수 피처가 통과하던 전례가 있다), 실측만
두면 창고 없는 곳에서 통째로 못 돈다. 그래서 구조는 합성으로 못 박고,
**진짜 휴장일 두 날**은 창고가 있을 때만 확인한다.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quant_rl_trading.selector.candidates import CORRELATION_WINDOW, correlation_matrix
from quant_rl_trading.session.daily import STATS_WINDOW, market_stats
from quant_rl_trading.store import Store
from quant_rl_trading.store.prices import read_prices

#: 창고에 실재하는 종가 0 세션. 둘 다 진짜 휴장일이다 —
#: 2026-06-03 지방선거(임시공휴일), 2026-07-17 제헌절.
DEAD_SESSIONS = (datetime(2026, 6, 3, tzinfo=UTC), datetime(2026, 7, 17, tzinfo=UTC))

ENTITIES = tuple(f"KR:{index:06d}" for index in range(1, 41))


def _sessions(count: int, *, end: datetime) -> list[datetime]:
    """거래일 흉내. 주말만 건너뛴다 — 여기서 필요한 것은 순서지 달력이 아니다."""
    days: list[datetime] = []
    cursor = end
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    return sorted(days)


def _seed(store: Store, *, dead: datetime | None) -> list[datetime]:
    """서로 독립인 랜덤워크 40종목. ``dead`` 세션은 전 종목 종가 0 으로 적재한다.

    독립이라는 것이 이 테스트의 전부다 — 진짜 상관이 0 근처인 종목들을 깔아
    두어야 "0 하나가 상관을 만들어 낸다" 를 잡을 수 있다.
    """
    store.seed_config_defaults()
    sessions = _sessions(CORRELATION_WINDOW * 2, end=datetime(2026, 8, 14, tzinfo=UTC))

    rng = np.random.default_rng(20260815)
    steps = rng.normal(0.0, 0.015, size=(len(sessions), len(ENTITIES)))
    paths = 10_000.0 * np.exp(np.cumsum(steps, axis=0))

    universe_rows = []
    price_rows = []
    for index, day in enumerate(sessions):
        for offset, entity in enumerate(ENTITIES):
            universe_rows.append({
                "entity_id": entity, "valid_from": day, "observed_at": day,
                "source": "test", "market": "KR", "name": entity,
                "is_listed": True, "is_tradable": True, "delisted_on": None,
            })
            is_dead = dead is not None and day == dead
            close = 0.0 if is_dead else float(paths[index, offset])
            price_rows.append({
                "entity_id": entity, "valid_from": day, "observed_at": day,
                "source": "test", "market": "KR",
                "open": close, "high": close, "low": close, "close": close,
                "volume": 0.0 if is_dead else 100_000.0,
                "value": 0.0 if is_dead else 5_000_000_000.0,
                "adj_factor": None,
            })
    store.append("universe", universe_rows, ingest_run_id="u-seed")
    store.append("prices", price_rows, ingest_run_id="p-seed")
    return sessions


def _pairwise(matrix: pd.DataFrame) -> np.ndarray:
    off_diagonal = ~np.eye(len(matrix), dtype=bool)
    return matrix.values[off_diagonal]


# -----------------------------------------------------------------------------
# 구조 — 합성 데이터
# -----------------------------------------------------------------------------


def test_dead_session_does_not_manufacture_correlation(store: Store) -> None:
    """종가 0 하루가 들어와도 **평균 쌍상관이 튀지 않는다.**

    이 테스트가 이 파일의 이유다. 나머지는 이것의 따름이다.
    """
    dead = _sessions(CORRELATION_WINDOW, end=datetime(2026, 8, 14, tzinfo=UTC))[20]
    sessions = _seed(store, dead=dead)
    as_of = sessions[-1] + timedelta(hours=9)

    matrix = correlation_matrix(
        store, as_of=as_of, entities=list(ENTITIES), market="KR"
    )
    assert not matrix.empty, "상관행렬이 비었다 — 거르기가 창을 통째로 지웠다"

    pairs = _pairwise(matrix)
    assert np.isfinite(pairs).all(), "상관에 inf/nan 이 남았다"

    # 독립 랜덤워크의 60일 표본상관은 0 근처에 흩어진다. 0.30 은 표본오차를
    # 넉넉히 덮으면서 오염(실측 +0.92)과는 확실히 갈라지는 자리다.
    assert abs(float(pairs.mean())) < 0.30, (
        f"평균 쌍상관 {pairs.mean():+.3f} — 종가 0 세션이 상관을 만들어 냈다"
    )
    over = float(np.mean(np.abs(pairs) > 0.7))
    assert over < 0.10, f"|corr|>0.7 인 쌍이 {over:.1%} — 오염 신호다"


def test_dead_session_is_removed_not_blanked(store: Store) -> None:
    """0 은 NaN 으로 바뀌는 것이 아니라 **행째로 빠진다.**

    휴장일은 없었던 날이다. NaN 으로 두면 앞뒤를 잇는 수익률까지 끊겨 창에서
    관측이 2개 사라진다 — 실측으로 20일 창의 관측수가 20 → 18 이 됐다.
    """
    dead = _sessions(CORRELATION_WINDOW, end=datetime(2026, 8, 14, tzinfo=UTC))[20]
    sessions = _seed(store, dead=dead)
    as_of = sessions[-1] + timedelta(hours=9)

    frame = read_prices(store, as_of=as_of, market="KR", columns=["close"])
    assert not frame.empty
    assert (frame["close"] > 0).all(), "종가 0 행이 남았다"
    assert frame["close"].notna().all(), "NaN 으로 바뀌었다 — 행이 빠져야 한다"
    assert dead not in set(frame["valid_from"]), "휴장일 세션이 그대로 있다"


def test_volatility_survives_a_dead_session(store: Store) -> None:
    """변동성 사전이 비지 않는다.

    예전에는 ``inf`` 하나가 ``std`` 를 nan 으로 만들고, ``if deviation > 0`` 이
    nan 을 조용히 떨어뜨려 그 종목이 사전에서 사라졌다. 실측으로 휴장일 이후
    21세션 동안 후보 400개 중 변동성이 나온 종목이 1개였다.
    """
    dead = _sessions(STATS_WINDOW, end=datetime(2026, 8, 14, tzinfo=UTC))[5]
    sessions = _seed(store, dead=dead)
    as_of = sessions[-1] + timedelta(hours=9)

    prices, adv, volatility = market_stats(
        store, as_of=as_of, entities=list(ENTITIES), market="KR"
    )
    assert len(prices) == len(ENTITIES), "가격 사전이 비었다"
    assert len(adv) == len(ENTITIES)
    assert len(volatility) == len(ENTITIES), (
        f"변동성이 {len(volatility)}/{len(ENTITIES)}종목뿐이다 — inf 가 std 를 죽였다"
    )
    assert all(np.isfinite(value) and value > 0 for value in volatility.values())


def test_session_after_a_dead_session_still_has_prices(store: Store) -> None:
    """휴장 **다음 날**에도 가격 사전이 차 있다.

    창의 마지막 종가가 0 이면 ``closes.iloc[-1] <= 0`` 에 전 종목이 걸려
    가격 사전이 통째로 비고, 목표비중을 수량으로 바꿀 수 없어 **그날 주문이
    0건**이 된다. 실측으로 2026-06-04·2026-07-20 이 그랬다.
    """
    sessions = _sessions(CORRELATION_WINDOW * 2, end=datetime(2026, 8, 14, tzinfo=UTC))
    dead = sessions[-1]
    _seed(store, dead=dead)

    # 휴장일 장중 — 창고의 마지막 세션이 그 0 세션인 시점이다.
    as_of = dead + timedelta(hours=9)
    prices, _, _ = market_stats(
        store, as_of=as_of, entities=list(ENTITIES), market="KR"
    )
    assert len(prices) == len(ENTITIES), (
        "휴장일 종가 0 이 마지막 세션이면 전 종목이 '가격 없음' 으로 떨어진다"
    )
    assert all(value > 0 for value in prices.values())


def test_clean_warehouse_is_unchanged(store: Store) -> None:
    """0 세션이 없으면 아무것도 바뀌지 않는다 — 거르기가 멀쩡한 행을 안 먹는다."""
    sessions = _seed(store, dead=None)
    as_of = sessions[-1] + timedelta(hours=9)

    raw = store.get("prices", as_of=as_of, market="KR", columns=["close"])
    filtered = read_prices(store, as_of=as_of, market="KR", columns=["close"])
    assert len(filtered) == len(raw)
    assert list(filtered.columns) == list(raw.columns)


def test_requested_columns_come_back_unchanged(store: Store) -> None:
    """거르려고 얹은 ``close`` 는 돌려줄 때 다시 뺀다."""
    sessions = _seed(store, dead=None)
    as_of = sessions[-1] + timedelta(hours=9)

    frame = read_prices(store, as_of=as_of, market="KR", columns=["value"])
    assert "close" not in frame.columns, "거르려고 얹은 컬럼이 새어 나왔다"
    assert "value" in frame.columns


# -----------------------------------------------------------------------------
# 실측 — 진짜 창고가 있을 때만
# -----------------------------------------------------------------------------


def _real_store() -> Store | None:
    root = Path(os.environ.get("QUANT_RL_DATA_ROOT", "data")) / "curated" / "prices"
    return Store() if root.exists() else None


def test_real_holidays_are_gone_from_the_read_path() -> None:
    """실제 창고의 2026-06-03·2026-07-17 이 읽기 경로에서 사라진다.

    합성 데이터는 내가 만든 사고만 재현한다. 이 두 날은 창고에 실재하는
    사고이므로, 창고가 있을 때는 그것으로 확인한다.
    """
    store = _real_store()
    if store is None:
        pytest.skip("실전 창고가 없다")

    as_of = datetime(2026, 8, 15, tzinfo=UTC)
    frame = read_prices(store, as_of=as_of, lookback=120, market="KR", columns=["close"])
    if frame.empty:
        pytest.skip("창고에 KR 시세가 없다")

    assert (frame["close"] > 0).all()
    present = {pd.Timestamp(value).date() for value in frame["valid_from"]}
    for dead in DEAD_SESSIONS:
        assert dead.date() not in present, f"{dead.date()} 가 읽기 경로에 남아 있다"
