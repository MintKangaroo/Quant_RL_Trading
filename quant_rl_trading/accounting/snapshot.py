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

from quant_rl_trading.accounting import benchmark as benchmark_module
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

    def row(
        self,
        *,
        observed_at: datetime,
        benchmark: benchmark_module.Benchmark | None = None,
    ) -> dict:
        valuation = self.valuation
        benchmark = benchmark or benchmark_module.Benchmark(index_value=None, note=None)
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
            "benchmark_index": benchmark.index_value,
            "benchmark_note": benchmark.note,
        }


def last_prices(store: Store, *, as_of: datetime, entities: list[str]) -> dict[str, float]:
    """평가 가격. **미장은 직전 종가다** — 그게 15:40 에 알 수 있는 전부다.

    거래정지로 그날 봉이 없는 종목은 창 안의 마지막 종가를 쓴다. 0 으로 치면
    그 종목이 사라진 것과 같아져 NAV 가 조용히 떨어진다 (nav.value 참조).

    **0 이하를 먼저 걷어내고 그 다음에 마지막 행을 고른다. 순서가 전부다.**
    반대로 하면 창의 마지막 세션이 종가 0 일 때 그 0 행이 뽑히고, 뒤이은
    필터가 그것을 버리면서 **종목이 결과에서 통째로 사라진다.** 그러면
    `nav.value` 가 "가격이 없다" 로 예외를 던지고 스냅샷이 죽는다 — 위
    문장이 약속한 "창 안의 마지막 종가" 는 영영 쓰이지 않는다.

    실제로 그렇게 죽었다. KRX 는 휴장일에도 전 종목을 0 으로 채운 표를 주고,
    방어(`krx_source.ohlcv_on`)가 생기기 전에 2026-06-03(지방선거)·2026-07-17
    두 세션이 그렇게 적재됐다. 워크포워드 백테스트가 06-03 세션에서
    `KeyError: 'KR:138930: 가격이 없다'` 로 죽었고, 증상은 "며칠째 안 끝난다"
    였다. 그 두 날은 받아올 시세가 존재하지 않으므로(소스가 지금도 0 을 준다)
    **정정본으로 메울 수도 없다** — 읽는 쪽이 견뎌야 한다.
    """
    if not entities:
        return {}
    frame = store.get(PRICES, as_of=as_of, entity=entities, lookback=30)
    if frame.empty:
        return {}
    close = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame[close.notna() & (close > 0)]
    if frame.empty:
        return {}
    latest = frame.sort_values(["valid_from", "observed_at"]).groupby("entity_id").tail(1)
    return {
        str(row["entity_id"]): float(row["close"])
        for row in latest.to_dict(orient="records")
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
    benchmark: benchmark_module.Benchmark | None = None,
) -> int:
    """스냅샷 적재. 하루에 한 행이고, 같은 날을 두 번 쓰면 창고가 거부한다.

    벤치마크를 안 넘기면 **여기서 계산한다.** 호출부에 맡기면 한 곳만 빠져도
    그 경로의 ``benchmark_index`` 가 통째로 null 이 되고 — 실제로 그랬다 —
    화면은 초과수익을 그릴 근거를 잃는다. 여기서 계산하면 NAV 가 쓴 것과
    **같은 as_of, 같은 환율**이라는 것이 구조로 보장된다 (accounting.md §2).
    """
    if benchmark is None:
        benchmark = benchmark_module.level(
            store, as_of=snapshot.as_of, fx_rate=snapshot.valuation.fx_rate
        )
    run_id = f"nav-{snapshot.as_of.date().isoformat()}"
    if store.ingest_run_recorded(ledger.NAV_DAILY, run_id):
        return 0
    return store.append(
        ledger.NAV_DAILY,
        [snapshot.row(observed_at=clock.now(), benchmark=benchmark)],
        ingest_run_id=run_id,
        source=SOURCE,
    )
