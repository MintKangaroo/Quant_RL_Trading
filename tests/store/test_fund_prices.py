"""상장 펀드(ETF) 시세는 ``indices`` 에서 — **이름으로 물을 때만** (store/prices.py "세 번째 일").

지수+V6 shadow(portfolio-construction.md)가 KODEX200 을 실제로 사고판다. 사이징·체결·평가가 전부 ``read_prices`` 로 시세를
읽으므로 거기서 붙인다. 시장 전체 조회에 끼면 유니버스·횡단면 z 가 오염된다 — 그 경계를 지킨다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd

from quant_rl_trading.collectors.benchmark_etf import BENCHMARK_ETF
from quant_rl_trading.store.prices import read_prices

DAY = datetime(2026, 10, 5, tzinfo=UTC)  # 세션 라벨 = UTC 자정(서울 09:00)
SESSION = DAY + timedelta(hours=7)        # as_of = 서울 16:00
STOCK = "KR:005930"


def _seed(store) -> None:  # type: ignore[no-untyped-def]
    days = [DAY - timedelta(days=2), DAY - timedelta(days=1), DAY]
    store.append("prices", [{
        "entity_id": STOCK, "valid_from": d, "observed_at": d + timedelta(hours=7), "source": "test", "market": "KR",
        "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 10.0, "value": 1000.0, "adj_factor": None,
    } for d in days], ingest_run_id="p")
    store.append("indices", [{
        "entity_id": BENCHMARK_ETF, "valid_from": d, "observed_at": d + timedelta(hours=6, minutes=50),
        "source": "test", "market": "KR", "board": "ETF",
        "open": 110.0, "high": 112.0, "low": 109.0, "close": 111.0 + i, "volume": 2e7, "value": 2e12,
    } for i, d in enumerate(days)] + [{
        "entity_id": "KR:IDX:KOSPI200", "valid_from": DAY, "observed_at": SESSION, "source": "test", "market": "KR",
        "board": "KOSPI200", "open": None, "high": None, "low": None, "close": 1090.0, "volume": None, "value": None,
    }], ingest_run_id="i")


def test_이름으로_물으면_펀드가_붙는다(store) -> None:
    _seed(store)
    frame = read_prices(store, as_of=SESSION, entity=[STOCK, BENCHMARK_ETF], lookback=10, market="KR")

    assert set(frame["entity_id"]) == {STOCK, BENCHMARK_ETF}
    etf = frame[frame["entity_id"] == BENCHMARK_ETF].sort_values("valid_from")
    assert etf["close"].tolist() == [111.0, 112.0, 113.0]
    assert "board" not in frame.columns, "시세 프레임의 축은 prices 와 같아야 한다"
    assert etf["adj_factor"].isna().all(), "indices 엔 계수가 없다 — 분할 없음(빈 값)"


def test_펀드만_물어도_된다_종목_조회를_하지_않는다(store) -> None:
    _seed(store)
    frame = read_prices(store, as_of=SESSION, entity=BENCHMARK_ETF, lookback=10, columns=["close", "volume"])
    assert set(frame["entity_id"]) == {BENCHMARK_ETF}
    assert {"close", "volume"} <= set(frame.columns)


def test_시장_전체_조회에는_안_낀다(store) -> None:
    """유니버스·피처·IC 경로(entity=None)는 이전과 같은 행을 본다."""
    _seed(store)
    frame = read_prices(store, as_of=SESSION, lookback=10, market="KR")
    assert set(frame["entity_id"]) == {STOCK}


def test_같은_게이트를_탄다_늦게_관측된_봉은_안_보인다(store) -> None:
    """KRX 원천은 다음 날 09:10 에 받힌다 — as_of d 16:00 에는 d 봉이 없어야 한다(마감 직후 LS 봉이 그 자리를 메운다)."""
    _seed(store)
    store.append("indices", [{
        "entity_id": BENCHMARK_ETF, "valid_from": DAY + timedelta(days=1), "observed_at": SESSION + timedelta(days=1, hours=1),
        "source": "test", "market": "KR", "board": "ETF",
        "open": 1.0, "high": 1.0, "low": 1.0, "close": 999.0, "volume": 1.0, "value": 1.0,
    }], ingest_run_id="late")
    frame = read_prices(store, as_of=SESSION + timedelta(days=1), entity=[BENCHMARK_ETF], lookback=10)
    assert 999.0 not in frame["close"].tolist()
    assert pd.Timestamp(frame["valid_from"].max()) == pd.Timestamp(DAY)
