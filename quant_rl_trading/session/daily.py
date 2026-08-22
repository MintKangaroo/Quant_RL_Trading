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

from quant_rl_trading.accounting import ledger as ledger_module
from quant_rl_trading.accounting import snapshot as snapshot_module
from quant_rl_trading.accounting.rates import Rates
from quant_rl_trading.allocator.baseline import AllocatorParams, Baseline, allocate
from quant_rl_trading.analysts.regime import RegimeAnalyst
from quant_rl_trading.replay.clock import ReplayClock
from quant_rl_trading.selector import exposure
from quant_rl_trading.broker import Broker
from quant_rl_trading.executor import pipeline as executor_pipeline
from quant_rl_trading.executor.sizing import Target
from quant_rl_trading.replay.events import EventLog, payload_hash
from quant_rl_trading.selector import pipeline as selector_pipeline
from quant_rl_trading.store.prices import adjust, read_prices

if TYPE_CHECKING:
    from quant_rl_trading.replay.clock import Clock
    from quant_rl_trading.store import Store

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
    #: 선정이 시작조차 못 한 사유(`selector.weights` 의 상수). 빈 문자열이면
    #: 정상이다. **후보 0개와 다른 사건이다** — 후보 0개는 "오늘 살 게 없다"
    #: 일 수 있지만 이 값이 차 있으면 설비가 고장 나 있다. 세션 실행기가
    #: 종료코드로 내보낸다(tools/run_session.py).
    #:
    #: `blocked_by` 와도 다르다. 그쪽은 안전장치가 **일한** 것이고 이쪽은
    #: 알파 합성이 **못 돈** 것이다.
    fault: str = ""

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

    **시세는 ``read_prices`` 로만 읽는다.** 아래 ``closes.iloc[-1] <= 0`` 가드는
    창의 **마지막 종가만** 보므로 창 안쪽의 휴장일 0 을 못 막았다. 그래서
    ``pct_change`` 가 ``inf`` 를 내고 ``std`` 가 nan 이 되어 그 종목이 변동성
    사전에서 조용히 빠졌다 — 실측으로 휴장일 이후 21세션 동안 후보 400개 중
    변동성이 나온 종목이 1개였다. 역변동성 가중이 그동안 동일가중으로
    퇴화한다 (``store/prices.py`` 참조).
    """
    if not entities:
        return {}, {}, {}
    frame = read_prices(
        store,
        as_of=as_of,
        entity=entities,
        lookback=STATS_WINDOW * 3,
        market=market,
        columns=["close", "value", "adj_factor"],
    )
    if frame.empty:
        return {}, {}, {}

    ordered = frame.sort_values("valid_from")
    # **한 프레임에서 두 값을 뽑는다 — 가격은 원주가, 변동성은 보정가.**
    #
    # 가격은 목표비중을 수량으로 바꾸는 데 쓰이므로 실제로 거래되는 값이어야
    # 한다. 보정가로 주문을 내면 분할 직후 수량이 배율만큼 틀어진다.
    #
    # 변동성은 수익률로 재므로 반대다. 분할 하루가 -90% 로 남으면 그 종목의
    # 변동성이 통째로 부풀고, 역변동성 가중이 그 종목의 비중을 0 에 가깝게
    # 눌러 버린다 — 실제로는 아무 일도 없었는데.
    adjusted = adjust(ordered)
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

    for entity, group in adjusted.groupby("entity_id"):
        if str(entity) not in prices:
            continue
        closes = group["close"].astype(float).tail(STATS_WINDOW + 1)
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
    broker: Broker | None = None,
) -> DailySession:
    """하루치 결정. 주문을 만들고 기록한다.

    ``broker`` 를 안 주면 **보내지 않는다**(``PaperBroker``). 실전은 호출자가
    ``broker.factory.build_broker`` 로 만들어 주입한다 — 이 함수 안에 분기는
    없고, 갈리는 것은 주입된 브로커뿐이다(불변식 5).

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
    # 여기서 스냅샷을 **쓰지 않는다.** 자본을 알려고 접어 볼 뿐이다. 적재는
    # 하루의 3단계(`backtest/loop.py`)가 한 번만 하고, 벤치마크도 거기서
    # 같은 as_of·같은 환율로 함께 실린다. 여기에 두 번째 write 를 넣으면
    # 같은 날 두 행이 되어 창고가 거부하거나(ingest_run) 벤치마크만 다른
    # 시점으로 기록된다.
    snapshot = snapshot_module.take(store, clock, as_of=as_of, book=book)
    equity = snapshot.valuation.nav
    # **자본과 주문가능금액은 다른 숫자다** (accounting.md §1). 자본은 목표
    # 비중을 금액으로 바꾸는 데 쓰고, 살 수 있는 한도는 결제가 끝난 현금이다.
    # 둘을 같다고 보면 미결제 대금까지 쓰게 된다 — 그게 이 백테스트를
    # 레버리지 2.83배로 끌고 간 길이다.
    #
    # **출처는 장부다.** 실전에서 증권사 주문가능금액(LS `t0424`)을 쓰는 것이
    # 더 정확하지만, 그러면 백테스트와 라이브가 다른 숫자를 보게 된다
    # (불변식 5). 지금은 양쪽 다 장부에서 접는다.
    settlement_days = int(store.config("execution.settlement_days", as_of=as_of))
    cash = ledger_module.available_cash(
        store,
        as_of=as_of,
        book=book,
        settlement_days=settlement_days,
        market=market,
    )
    log.record(
        "observe", "accounting", {"nav": round(equity, 4), "available_cash": round(cash, 4)}
    )

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
    result.fault = selection.fault
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
    # **후보 + 보유.** 후보만 조회하면 후보에서 밀려난 보유 종목의 시세가 0 이
    # 되고, sizing 이 그것을 "시세 없음" 으로 스킵해 **영영 못 판다** — 장부가
    # 한 방향 래칫이 된다(top-N 에 들어야 사고, top-N 에 남아야만 판다).
    # 2026-08 OOS 백테스트에서 실제로 그랬다: 매도 주문 191건이 전부 그날의
    # 후보였던 종목이고, 후보 밖 보유 3,109건(종목×일) 에는 한 건도 안 나갔다.
    # 밑의 targets 주석이 막으려던 것이 바로 이 자리에서 무너져 있었다.
    entities = list(dict.fromkeys([*result.candidates, *holdings]))
    prices, adv, volatility = market_stats(
        store, as_of=as_of, entities=entities, market=market
    )
    scores = {item.entity_id: item.score for item in selection.candidates}
    allocate_driver = str(params.baseline)
    if params.baseline is Baseline.RISK_PARITY:
        # **위험 구조로 나눈다** (§3). 창고를 타므로 순수 allocate 가 아니다.
        # 팩터 모델이 못 서면 스코어 비례로 물러서고, 그 사실을 driver 로 남긴다.
        #
        # **지연 import 다.** risk_parity_baseline 은 portfolio→selector→backtest→
        # 이 모듈로 도는 import 고리에 걸린다. 여기서 늦게 물어 고리를 끊는다 —
        # AST 로 닫힘을 보는 test_cache_config_scope 는 함수 안 import 도 세므로
        # 지문 커버는 그대로다.
        from quant_rl_trading.allocator.risk_parity_baseline import (
            RiskParityParams,
            allocate_risk_parity,
        )

        rp_params = RiskParityParams.from_store(store, as_of=as_of)
        weights, path = allocate_risk_parity(
            store,
            as_of=as_of,
            market=str(market),
            scores=scores,
            entities=entities,
            params=rp_params,
            fallback=AllocatorParams(
                baseline=Baseline.SCORE,
                max_position_weight=params.max_position_weight,
                cash_buffer=params.cash_buffer,
            ),
            volatility=volatility,
        )
        # path 는 이미 자기서술적이다: "risk_parity:crisis" / "risk_parity:fallback".
        allocate_driver = path
    else:
        weights = allocate(
            scores=scores,
            params=params,
            volatility=volatility if params.baseline is Baseline.SCORE_INVERSE_VOL else None,
        )

    # 2-b. 노출 제어 (③) — **얼마나 살지.** 무엇을 살지는 위에서 끝났다.
    #
    # `chart` 가 여기로 온 이유는 `selector/exposure.py` 에 있다: 횡단면 랭크
    # IC 는 새 상태 피처 여덟도 전부 미달이었지만, **변동성 압축이 이후
    # 변동성을 맞히는 것은 82만 행에서 단조로 살아남았다**(5분위 0.857→1.179).
    # 종목을 줄세우는 데는 못 쓰고 얼마나 들지 정하는 데는 쓴다.
    #
    # 곱한 뒤 **정규화하지 않는다** — 하면 합이 도로 1 이 되어 줄인 것이
    # 사라진다. `exposure.apply` 가 그 실수를 막는 자리다.
    exposure_params = exposure.ExposureParams.from_store(store, as_of=as_of)
    index_id = str(store.config(f"benchmark.{market.lower()}_index", as_of=as_of))
    regime = RegimeAnalyst(store, ReplayClock(as_of))
    decision = exposure.decide(
        store,
        as_of=as_of,
        index_id=index_id,
        regime_state=regime.state(as_of),
        params=exposure_params,
    )
    scaled = exposure.apply(weights, decision)
    result.weights = scaled
    # **allocate 를 먼저 적고 exposure 를 뒤에 적는다.** 로그는 일어난 순서를
    # 말해야 하고, 노출 제어는 배분 결과에 **덧씌우는** 단계다. 순서를 뒤집으면
    # 나중에 흔적을 읽는 사람이 "노출을 정하고 나서 비중을 나눴다" 로 읽는다.
    #
    # allocate 에는 **줄이기 전 비중**을 남긴다 — 배분이 무엇을 골랐는지와
    # 노출이 얼마나 깎았는지가 갈려 있어야 어느 쪽이 문제인지 가른다.
    log.record(
        "allocate",
        allocate_driver,
        {"weights": {name: round(value, 6) for name, value in weights.items()}},
    )
    log.record("exposure", decision.driver, decision.as_dict())

    # **여기서부터는 줄인 비중이다.** 이 한 줄이 없으면 노출 제어가 로그에만
    # 남고 주문은 원래대로 나간다 — 이 저장소에서 제일 자주 나는 결함이
    # 정확히 그 모양이다(코드는 있는데 아무도 안 부른다). 실제로 이 자리에서
    # 한 번 그랬다.
    weights = scaled

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
        cash=cash,
        market_open=market_open,
        board=board,
        broker=broker,
    )
    result.orders = execution.planned
    result.notes.extend(execution.notes)
    # **못 판 보유 종목은 노트로 올린다.** ``execution.skipped`` 에만 남기면
    # 화면과 리포트가 그것을 못 보고, 청산 불가가 조용히 쌓인다.
    result.notes.extend(
        f"{item.entity_id}: {item.reason}"
        for item in execution.skipped
        if item.entity_id in holdings
    )
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
