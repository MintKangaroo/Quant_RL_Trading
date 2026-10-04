"""지수+V6 shadow — 설정 덮어쓰기만으로 KODEX200 한 종목을 100% × 노출 배수로 사고파는지, 끝에서 끝까지.

체크인된 덮어쓰기 템플릿(config/shadow/idxv6.config-overrides.yaml)을 그대로 창고 루트에 깔고 ``loop.run`` 을 하루씩
``tools/run_session.py`` 모양으로 돌린다. 신호·유니버스는 하나도 없다 — 고정 바구니가 선정을 건너뛰어야 한다.
"""

from __future__ import annotations

import shutil
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from quant_rl_trading.accounting import ledger as ledger_module
from quant_rl_trading.accounting.rates import Rates
from quant_rl_trading.backtest import loop
from quant_rl_trading.collectors.benchmark_etf import BENCHMARK_ETF
from quant_rl_trading.collectors.market_hours import Market, trading_days
from quant_rl_trading.store import OVERRIDES_FILE
from quant_rl_trading.store.prices import read_prices

SEOUL = ZoneInfo("Asia/Seoul")
REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "config" / "shadow" / "idxv6.config-overrides.yaml"
START = date(2026, 8, 3)
DAY_TWO = date(2026, 8, 4)


def _moment(day: date) -> datetime:
    return datetime.combine(day, loop.DEFAULT_SNAPSHOT_TIME, tzinfo=SEOUL)


@pytest.fixture
def book(store):  # type: ignore[no-untyped-def]
    store.seed_config_defaults()
    shutil.copy(TEMPLATE, Path(store.root) / OVERRIDES_FILE)
    history = trading_days(Market.KR, START - timedelta(days=420), DAY_TWO)
    store.append("fx", [{
        "entity_id": "FX:USDKRW", "valid_from": _moment(d), "observed_at": _moment(d), "source": "test", "rate": 1_350.0,
    } for d in history], ingest_run_id="fx")
    etf, index = [], []
    for i, d in enumerate(history):
        label = datetime(d.year, d.month, d.day, 9, 0, tzinfo=SEOUL)
        close = 100_000.0 + 50.0 * i
        # 마감 직후 LS 봉처럼 16:00 전에 관측됐다.
        etf.append({"entity_id": BENCHMARK_ETF, "valid_from": label, "observed_at": _moment(d) + timedelta(minutes=10),
                    "source": "test", "market": "KR", "board": "ETF", "open": close, "high": close * 1.01,
                    "low": close * 0.99, "close": close, "volume": 2e7, "value": close * 2e7})
        index.append({"entity_id": "KR:IDX:KOSPI", "valid_from": label, "observed_at": _moment(d), "source": "test",
                      "market": "KR", "board": "KOSPI", "open": None, "high": None, "low": None,
                      "close": 2_500.0 + i, "volume": None, "value": None})
    store.append("indices", etf + index, ingest_run_id="idx")
    return store


def _run_day(store, day: date, *, capital: float = 0.0):  # type: ignore[no-untyped-def]
    return loop.run(store, start=day, end=day, market="KR", capital=capital, warmup_days=1,
                    record_warmup=False, produce_signals=False)


def test_신호_없이_ETF_를_사고_다음_날_체결된다(book) -> None:
    first = _run_day(book, START, capital=503_000_000.0).days[-1]
    assert first.candidates == (BENCHMARK_ETF,), "고정 바구니가 선정을 건너뛴다"
    assert first.fault == "" and not first.blocked_by
    assert first.planned_orders >= 1

    second = _run_day(book, DAY_TWO).days[-1]
    assert second.filled > 0, "어제 주문이 오늘 ETF 봉으로 체결된다"

    as_of = second.as_of
    position = ledger_module.build_book(book, as_of=as_of, rates=Rates.from_store(book, as_of=as_of)).positions
    held = position[BENCHMARK_ETF]
    close = float(read_prices(book, as_of=as_of, entity=[BENCHMARK_ETF], lookback=5)["close"].iloc[-1])
    weight = held.quantity * close / second.nav
    assert 0.95 < weight <= 1.0, f"100% × 노출 배수(규칙 V6 · 국면 미상 = 1.0) — 쿠션만큼만 현금: {weight:.3f}"


def test_체크인_템플릿은_고정_바구니만_켠다() -> None:
    import yaml

    overrides = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    assert overrides["selector.fixed_basket"] == [BENCHMARK_ETF]
    assert overrides["allocator.max_position_weight"] == 1.0 and overrides["allocator.cash_buffer"] == 0.0
    # 노출 규칙은 모의계좌 그대로 — 학습 노출(exposure.source)이나 노출 설정을 덮지 않는다.
    assert not any(key.startswith("exposure.") for key in overrides)
