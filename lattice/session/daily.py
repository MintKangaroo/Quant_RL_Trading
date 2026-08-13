"""하루의 의사결정 사이클 — Selector → Allocator → Executor.

    Analyst 신호 → 후보 선정 → 목표 비중 → 주문 → 실현 비중 기록

``replay/session.py`` 와 다른 것이다. 저 파일은 **결정론을 증명하는 일반
하네스**이고(전략 자리에 무엇을 끼우든 리플레이가 재현되는지), 이 파일은 M3
의 **실제 전략**이다. 둘을 합치지 않는 이유는 하네스가 전략을 알면 하네스로
전략을 검증할 수 없기 때문이다.

## 백테스트와 라이브가 같은 코드를 쓴다 (불변식 5)

바뀌는 것은 Clock 뿐이다. ``if backtest:`` 분기는 없다. 그래서 이 함수는
주문을 **만들기만** 하고 보내지 않는다 — 전송은 브로커 어댑터가 한다.
shadow 운용도 같은 코드를 그대로 탄다.

## 자본은 회계에서만 온다

목표 비중을 금액으로 바꾸려면 NAV 가 필요하다. 그 값을 여기서 다시 계산하지
않고 ``accounting`` 에서 받는다 — 각자 계산하면 반드시 어긋나고, 어긋나면
Executor 가 쓰는 자본과 성과에 기록되는 자본이 다른 값이 된다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING


from lattice.accounting import ledger as ledger_module
from lattice.accounting import snapshot as snapshot_module
from lattice.accounting.rates import Rates
from lattice.allocator.baseline import AllocatorParams, Baseline, allocate
from lattice.executor import pipeline as executor_pipeline
from lattice.executor.sizing import Target
from lattice.replay.events import EventLog, payload_hash
from lattice.selector import pipeline as selector_pipeline

if TYPE_CHECKING:
    from lattice.replay.clock import Clock
    from lattice.store import Store

PRICES = "prices"

#: 변동성·거래대금을 재는 창(거래일).
STATS_WINDOW = 20


@dataclass
class DailySession:
    as_of: datetime
    market: str
    equity: float
    candidates: tuple[str, ...] = ()
    weights: dict[str, float] = field(default_factory=dict)
    orders: tuple = ()
    notes: list[str] = field(default_factory=list)
    blocked_by: str = ""

    def digest(self) -> str:
        """주문의 지문. **같은 as_of 는 같은 지문이어야 한다.**

        벽시계는 넣지 않는다 — 리플레이마다 당연히 다르므로 넣으면 결정론
        테스트가 영원히 실패한다.
        """
        return payload_hash(
            {
                "market": self.market,
                "orders": [
                    {
                        "order_id": item.order_id,
                        "slice_seq": item.slice_seq,
                        **item.order.canonical(),
                    }
                    for item in self.orders
                ],
            }
        )


def market_stats(
    store: Store, *, as_of: datetime, entities: list[str], market: str
) -> tuple[dict[str, float], dict[str, float], dict[str, float]]:
    """(종가, 20일 평균 거래대금, 일간 변동성).

    셋을 한 번에 내는 이유는 같은 프레임을 세 번 읽지 않기 위해서다.
    """
    if not entities:
        return {}, {}, {}
    frame = store.get(
        PRICES,
        as_of=as_of,
        entity=entities,
        lookback=STATS_WINDOW * 3,
        market=market,
        columns=["close", "value"],
    )
    if frame.empty:
        return {}, {}, {}

    ordered = frame.sort_values("valid_from")
    prices: dict[str, float] = {}
    adv: dict[str, float] = {}
    volatility: dict[str, float] = {}

    for entity, group in ordered.groupby("entity_id"):
        closes = group["close"].astype(float).tail(STATS_WINDOW + 1)
        if closes.empty or float(closes.iloc[-1]) <= 0:
            continue
        prices[str(entity)] = float(closes.iloc[-1])
        values = group["value"].astype(float).tail(STATS_WINDOW)
        if not values.empty and float(values.mean()) > 0:
            adv[str(entity)] = float(values.mean())
        returns = closes.pct_change(fill_method=None).dropna()
        if len(returns) >= 5:
            deviation = float(returns.std())
            if deviation > 0:
                volatility[str(entity)] = deviation
    return prices, adv, volatility


def run(
    store: Store,
    clock: Clock,
    *,
    as_of: datetime,
    market: str,
    holdings: dict[str, int] | None = None,
    run_id: str | None = None,
    market_open: datetime | None = None,
    board: str = "KOSPI",
    wall_clock: Clock | None = None,
) -> DailySession:
    """하루치 결정. 주문을 만들고 기록한다. **보내지는 않는다.**

    ``wall_clock`` 은 "언제 이 계산을 실제로 돌렸나" 다. 라이브에서는 ``clock``
    과 같고, 리플레이에서는 다르다 — 이벤트의 ``observed_at`` 이 이것이라,
    과거를 재생하면서 벽시계를 안 주면 그 이벤트는 **미래에 관측된 것**이 되어
    같은 as_of 조회에서 안 보인다 (replay/session.py 와 같은 규칙).
    """
    holdings = holdings or {}
    session_run_id = run_id or f"session-{market}-{as_of.date().isoformat()}"
    log = EventLog(
        store=store, clock=clock, run_id=session_run_id,
        **({"wall_clock": wall_clock} if wall_clock is not None else {}),
    )

    # 자본. 회계 한 곳에서만 온다.
    rates = Rates.from_store(store, as_of=as_of)
    book = ledger_module.build_book(store, as_of=as_of, rates=rates)
    snapshot = snapshot_module.take(store, clock, as_of=as_of, book=book)
    equity = snapshot.valuation.nav
    log.record("observe", "accounting", {"nav": round(equity, 4)})

    result = DailySession(as_of=as_of, market=market, equity=equity)
    if equity <= 0:
        result.notes.append("자본이 0 이다. 목표 비중을 금액으로 바꿀 수 없다")
        log.record("decide", "session", {"skipped": "no_equity"})
        log.flush()
        return result

    # 1. 후보 선정
    selection = selector_pipeline.run(
        store, as_of=as_of, market=market, equity=equity
    )
    result.candidates = tuple(item.entity_id for item in selection.candidates)
    result.notes.extend(selection.trace.notes)
    log.record(
        "select",
        "selector",
        {
            "candidates": list(result.candidates),
            "weights": {name: round(value, 6) for name, value in selection.weights.items()},
        },
    )
    if not selection.candidates:
        log.flush()
        return result

    # 2. 목표 비중
    params = AllocatorParams.from_store(store, as_of=as_of)
    entities = list(result.candidates)
    prices, adv, volatility = market_stats(
        store, as_of=as_of, entities=entities, market=market
    )
    scores = {item.entity_id: item.score for item in selection.candidates}
    weights = allocate(
        scores=scores,
        params=params,
        volatility=volatility if params.baseline is Baseline.SCORE_INVERSE_VOL else None,
    )
    result.weights = weights
    log.record(
        "allocate",
        str(params.baseline),
        {"weights": {name: round(value, 6) for name, value in weights.items()}},
    )

    # 3. 집행. 보유 중인데 목표에서 빠진 종목도 넣는다 — 안 넣으면 팔 기회가
    #    영영 오지 않는다.
    targets = [
        Target(
            entity_id=entity,
            weight=weights.get(entity, 0.0),
            price=prices.get(entity, 0.0),
            adv_value=adv.get(entity, 0.0),
        )
        for entity in dict.fromkeys([*weights, *holdings])
    ]
    execution = executor_pipeline.run(
        store,
        clock,
        as_of=as_of,
        market=market,
        targets=targets,
        holdings=holdings,
        equity=equity,
        market_open=market_open,
        board=board,
    )
    result.orders = execution.planned
    result.notes.extend(execution.notes)
    result.blocked_by = execution.blocked_by
    log.record(
        "execute",
        "executor",
        {
            "orders": [
                {"order_id": item.order_id, **item.order.canonical()}
                for item in execution.planned
            ],
            "blocked_by": execution.blocked_by,
        },
    )

    log.flush()
    return result
