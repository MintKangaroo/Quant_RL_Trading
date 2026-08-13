"""일간 회계 스냅샷 — 한국시간 15:40 하루 한 번 (accounting.md §2).

두 시장의 하루가 어긋난다(국장 15:30 마감, 미장 새벽 05:00). 어긋난 것을
어긋난 채로 두면 "오늘 수익률" 이 무엇인지 말할 수 없으므로, **기준 시각을
하나로 못 박고 미장은 직전 종가를 쓴다.**

벤치마크도 정확히 같은 시각·같은 규칙으로 계산한다. 포트폴리오는 15:40
기준인데 벤치마크가 미장 실시간이면 그 차이가 통째로 가짜 초과수익이 된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

import pandas as pd

from quant_rl_trading.accounting import ledger
from quant_rl_trading.accounting.book import Book
from quant_rl_trading.accounting.nav import BASE_INDEX, Valuation, twr_return, value
from quant_rl_trading.accounting.rates import Rates

if TYPE_CHECKING:
    from quant_rl_trading.replay.clock import Clock
    from quant_rl_trading.store import Store

PRICES = "prices"
SOURCE = "accounting"


@dataclass(frozen=True)
class Snapshot:
    """그날의 회계 결과. 저장 전 형태."""

    as_of: datetime
    valuation: Valuation
    inflow: float
    twr_return: float
    index_value: float
    drawdown: float

    def row(self, *, observed_at: datetime, benchmark_index: float | None = None) -> dict:
        valuation = self.valuation
        return {
            "entity_id": ledger.ACCOUNT,
            "valid_from": self.as_of,
            "observed_at": observed_at,
            "source": SOURCE,
            "nav": valuation.nav,
            "inflow": self.inflow,
            "twr_return": self.twr_return,
            "index_value": self.index_value,
            "drawdown": self.drawdown,
            "cash_krw": valuation.cash_krw,
            "cash_usd": valuation.cash_usd,
            "equity_kr": valuation.equity_kr,
            "equity_us": valuation.equity_us,
            "accrued_dividend": valuation.accrued_dividend,
            "payable": valuation.payable,
            "fx_rate": valuation.fx_rate,
            "tax_provision": valuation.tax_provision,
            "nav_after_tax": valuation.nav_after_tax,
            "benchmark_index": benchmark_index,
        }


def last_prices(store: Store, *, as_of: datetime, entities: list[str]) -> dict[str, float]:
    """평가 가격. **미장은 직전 종가다** — 그게 15:40 에 알 수 있는 전부다.

    거래정지로 그날 봉이 없는 종목은 창 안의 마지막 종가를 쓴다. 0 으로 치면
    그 종목이 사라진 것과 같아져 NAV 가 조용히 떨어진다 (nav.value 참조).
    """
    if not entities:
        return {}
    frame = store.get(PRICES, as_of=as_of, entity=entities, lookback=30)
    if frame.empty:
        return {}
    latest = frame.sort_values(["valid_from", "observed_at"]).groupby("entity_id").tail(1)
    return {
        str(row["entity_id"]): float(row["close"])
        for row in latest.to_dict(orient="records")
        if pd.notna(row["close"]) and float(row["close"]) > 0
    }


def take(
    store: Store,
    clock: Clock,
    *,
    as_of: datetime,
    book: Book | None = None,
) -> Snapshot:
    """그 시점의 스냅샷을 만든다. 저장하지는 않는다.

    ``book`` 을 주면 그것을 쓰고, 없으면 기록에서 재구성한다. 주입을 허용하는
    이유는 백테스트가 이미 장부를 손에 들고 있기 때문이다 — 같은 계산을 두 번
    하지 않게 한다. **계산식은 어느 쪽이든 하나다.**
    """
    rates = Rates.from_store(store, as_of=as_of)
    if book is None:
        book = ledger.build_book(store, as_of=as_of, rates=rates)

    rate = ledger.fx_rate(store, as_of=as_of)
    prices = last_prices(store, as_of=as_of, entities=sorted(book.positions))
    valuation = value(book, prices=prices, fx_rate=rate)

    previous = ledger.previous_snapshot(store, as_of=as_of)
    if previous is None:
        # 첫날. 수익률 0, 지수는 기준값. 없는 어제를 지어내지 않는다.
        return Snapshot(
            as_of=as_of,
            valuation=valuation,
            inflow=book.inflow,
            twr_return=0.0,
            index_value=BASE_INDEX,
            drawdown=0.0,
        )

    previous_nav = float(previous["nav"])
    inflow = ledger.daily_inflow(
        store, as_of=as_of, since=pd.Timestamp(previous["valid_from"]).to_pydatetime()
    )
    daily = twr_return(nav=valuation.nav, previous_nav=previous_nav, inflow=inflow)
    index_value = float(previous["index_value"]) * (1.0 + daily)

    # 낙폭은 **누적지수 기준**이다. NAV 원금액으로 재면 입금이 낙폭을 지운다.
    peak = _peak_index(store, as_of=as_of, current=index_value)
    return Snapshot(
        as_of=as_of,
        valuation=valuation,
        inflow=inflow,
        twr_return=daily,
        index_value=index_value,
        drawdown=index_value / peak - 1.0 if peak > 0 else 0.0,
    )


def _peak_index(store: Store, *, as_of: datetime, current: float) -> float:
    frame = store.get(ledger.NAV_DAILY, as_of=as_of, entity=ledger.ACCOUNT)
    if frame.empty:
        return current
    return max(float(frame["index_value"].max()), current)


def write(
    store: Store,
    clock: Clock,
    *,
    snapshot: Snapshot,
    benchmark_index: float | None = None,
) -> int:
    """스냅샷 적재. 하루에 한 행이고, 같은 날을 두 번 쓰면 창고가 거부한다."""
    run_id = f"nav-{snapshot.as_of.date().isoformat()}"
    if store.ingest_run_recorded(ledger.NAV_DAILY, run_id):
        return 0
    return store.append(
        ledger.NAV_DAILY,
        [snapshot.row(observed_at=clock.now(), benchmark_index=benchmark_index)],
        ingest_run_id=run_id,
        source=SOURCE,
    )
