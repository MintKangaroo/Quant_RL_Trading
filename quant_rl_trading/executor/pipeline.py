"""Executor — 8단계를 순서대로. **순서가 곧 안전이다** (agents.md §7).

    1. 킬스위치 latch 확인    ← 걸려 있으면 여기서 종료
    2. 데이터 품질 게이트
    3. 시장 서킷 브레이커
    4. defer 게이트
    5. 목표비중 → 주식 수
    6. 거래대금 상한 · 청산 제약
    7. 주문 분할 집행 → 전송(``Broker.submit``)
    8. 실현 비중 기록 → Allocator 되먹임   ← 절대 생략 금지

**8번이 빠지면 RL 은 학습할 수 없다** (불변식 7). 소액 구간에서는 5번 라운딩
때문에 목표와 실현이 크게 벌어지고, 되먹임이 없으면 Allocator 는 자기가 하지
않은 행동으로 벌을 받는다.

**Executor 안에는 AI 가 없다** (불변식 6). 이 파일에 LLM 호출이 들어오는 날
마지막 안전장치가 예측 불가능해진다.

## 7단계 = 계획 + 전송

``orders.py`` 가 주문을 만들고 분할한다. 이 파일은 그걸 창고에 적은 뒤
(``record_orders``) ``Broker.submit`` 으로 실제로 내보낸다(``submit_orders``).
**주문을 내는 코드는 여기 하나뿐이다** — 백테스트·shadow·실전이 전부 이
함수를 탄다. 갈리는 것은 어떤 ``Broker`` 를 주입받느냐 뿐이다(불변식 5).
기본값은 ``PaperBroker`` 다 — 아무것도 보내지 않는다. 실전은 호출자가
``LSBroker`` 를 만들어 주입해야 한다. **이 파일은 절대 ``LSBroker`` 를 스스로
만들지 않는다** — 그 순간 테스트가 실계좌를 보게 된다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from quant_rl_trading.broker import Ack, BrokerError, PaperBroker, RejectedOrder
from quant_rl_trading.executor import guards
from quant_rl_trading.executor import orders as orders_module
from quant_rl_trading.executor.orders import PlannedOrder, SliceParams
from quant_rl_trading.executor.sizing import (
    Sized,
    SizingParams,
    Skipped,
    Target,
    replace_weight,
    size_orders,
)
from quant_rl_trading.schemas.order import Side

if TYPE_CHECKING:
    from quant_rl_trading.broker import Broker
    from quant_rl_trading.replay.clock import Clock
    from quant_rl_trading.store import Store

ORDERS = "orders"
REALIZED_WEIGHTS = "realized_weights"
SOURCE = "executor"

#: ``orders.status`` 가 거쳐가는 값들. ``record_orders`` 가 남기는 "planned" 는
#: 여기 없다(그건 계획 단계고, 아직 전송을 시도하지 않았다는 뜻이다).
STATUS_SUBMITTING = "submitting"
STATUS_SENT = "sent"
STATUS_PAPER = "paper"
STATUS_REJECTED = "rejected"


@dataclass
class ExecutionResult:
    session_id: str
    as_of: datetime
    market: str
    planned: tuple[PlannedOrder, ...] = ()
    skipped: tuple[Skipped, ...] = ()
    blocked_by: str = ""
    notes: list[str] = field(default_factory=list)
    #: ``submit_orders`` 가 돌려준 전송 결과. 이미 이전 실행에서 전송을 시도한
    #: 주문(재시작 후 건너뛴 것)은 여기 없다 — 그 세션의 acks 를 다시 알고 싶으면
    #: ``orders`` 테이블에서 status 를 읽어야 한다.
    acks: tuple[Ack, ...] = ()

    @property
    def blocked(self) -> bool:
        return bool(self.blocked_by)


def run(
    store: Store,
    clock: Clock,
    *,
    as_of: datetime,
    market: str,
    targets: list[Target],
    holdings: dict[str, int],
    equity: float,
    cash: float | None = None,
    market_open: datetime | None = None,
    board: str = "KOSPI",
    liquidation_only: bool = False,
    broker: Broker | None = None,
) -> ExecutionResult:
    """한 세션의 집행. 주문을 만들고, 창고에 적고, 전송한다.

    ``broker`` 를 안 주면 ``PaperBroker`` 다 — 기본은 아무것도 보내지 않는다.
    shadow 는 ``PaperBroker``(또는 그와 동등한 기록용 구현)를, 실전은
    ``LSBroker`` 를 호출자가 만들어 주입한다. **결정·기록·전송이 전부 이
    함수 안에 있지만 분기는 없다** — 갈리는 것은 주입된 ``broker`` 뿐이다.
    그래야 shadow 와 실전이 같은 코드를 탄다(불변식 5).
    """
    active_broker: Broker = broker if broker is not None else PaperBroker()
    session = orders_module.session_id(as_of=as_of, market=market)
    result = ExecutionResult(session_id=session, as_of=as_of, market=market)

    # 1. 킬스위치 — 가장 먼저. 다른 어떤 판단보다 앞선다.
    switch = guards.check_killswitch(store, as_of=as_of)
    if not switch:
        # **매도는 막지 않는다.** 청산까지 막는 안전장치는 빠져나올 길을 막는
        # 것이라 안전장치가 아니다. force_liquidation 이면 전량 청산까지 간다.
        liquidation_only = True
        result.notes.append(
            f"{switch.reason} — {'전 포지션 청산' if switch.force_liquidation else '신규매수만 차단'}"
        )
        if switch.force_liquidation:
            targets = [
                replace_weight(target, 0.0) for target in targets
            ] + [
                Target(entity_id=entity, weight=0.0, price=0.0, adv_value=0.0)
                for entity in holdings
                if entity not in {item.entity_id for item in targets}
            ]

    entities = [target.entity_id for target in targets]

    # 2. 데이터 품질
    quality = guards.check_data_quality(
        store, as_of=as_of, market=market, entities=entities
    )
    if not quality:
        # **매도는 막지 않는다.** 킬스위치·서킷브레이커와 같은 이유다 — 청산까지
        # 막는 안전장치는 빠져나올 길을 막는 것이라 안전장치가 아니다. 여기만
        # 통째로 막고 있었다.
        #
        # 결측은 대개 한 종목의 거래정지로 온다(`missing_warn` 이 0.01 이라
        # 24종목 중 하나만 빠져도 넘는다). 거래정지는 나쁜 소식과 함께 오고,
        # 그때가 정확히 나머지를 정리해야 할 때다. 게다가 신용·미수를 안 쓰므로
        # **매도가 유일한 현금 조달 수단**이다 — 그걸 막는 게이트는 덫이다.
        #
        # 시세를 모르는 종목 자체는 그대로 못 판다. `sizing` 의 `price <= 0`
        # 가드가 남아 있고, 그건 옳다 — 가격을 모르면 수량을 못 낸다.
        liquidation_only = True
        result.notes.append(f"{quality.reason} — 청산만 허용")

    # 3. 서킷 브레이커
    breaker = guards.check_circuit_breaker(store, as_of=as_of, board=board)
    if not breaker:
        # 급락일에도 청산은 허용한다. 못 빠져나오게 막는 안전장치는 위험하다.
        liquidation_only = True
        result.notes.append(f"{breaker.reason} — 청산만 허용")
    elif breaker.reason:
        result.notes.append(breaker.reason)

    # 4. defer 게이트 — 신규매수만 보류한다.
    if market_open is not None:
        defer = guards.check_defer(store, as_of=as_of, market_open=market_open)
        if not defer:
            liquidation_only = True
            result.notes.append(f"{defer.reason} — 신규매수 보류")

    # 5~6. 수량 변환과 상한
    sizing_params = SizingParams.from_store(store, as_of=as_of)
    # ``cash`` 는 주문가능금액이다. NAV 로 대신하면 미결제 대금까지 쓸 수 있게
    # 되고, 그 길로 이 저장소는 레버리지 2.83배까지 갔다 (accounting.md §1).
    sized, skipped = size_orders(
        targets=targets,
        holdings=holdings,
        equity=equity,
        params=sizing_params,
        cash=cash,
    )
    if liquidation_only:
        held_back = [item for item in sized if item.side is Side.BUY]
        for item in held_back:
            skipped.append(Skipped(item.entity_id, item.target_weight, "신규매수 차단"))
        sized = [item for item in sized if item.side is Side.SELL]
    result.skipped = tuple(skipped)

    # 7. 분할 집행
    slice_params = SliceParams.from_store(store, as_of=as_of)
    planned: list[PlannedOrder] = []
    for item in sized:
        planned.extend(
            orders_module.plan_slices(
                entity_id=item.entity_id,
                side=item.side,
                quantity=item.quantity,
                reference_price=item.price,
                target_weight=item.target_weight,
                session=session,
                params=slice_params,
                # 시장가는 청산과 킬스위치 발동 때만.
                market_order=liquidation_only and item.side is Side.SELL,
                # 호가단위표를 가른다 — 안 넘기면 미장 주문이 원화 표로
                # 반올림된다(2026-08-15, ticks.py 참고).
                market=market,
            )
        )
    result.planned = tuple(planned)

    record_orders(store, clock, planned=planned, as_of=as_of, market=market)
    result.acks = tuple(
        submit_orders(
            store, clock, active_broker, planned=planned, as_of=as_of, market=market
        )
    )
    # 8. **절대 생략 금지.**
    record_realized_weights(
        store, clock, sized=sized, targets=targets, as_of=as_of,
        market=market, session=session,
    )
    return result


def record_orders(
    store: Store,
    clock: Clock,
    *,
    planned: list[PlannedOrder],
    as_of: datetime,
    market: str,
) -> int:
    """주문 기록. **같은 세션을 두 번 돌려도 한 번만 남는다.**

    자연키가 (entity_id, session_id, slice_seq) 라 재시작 후 같은 주문을 다시
    만들어도 창고가 중복을 거부한다 — 멱등성의 마지막 방어선이다.
    """
    if not planned:
        return 0
    run_id = f"orders-{planned[0].session_id}"
    if store.ingest_run_recorded(ORDERS, run_id):
        return 0
    observed_at = clock.now()
    return store.append(
        ORDERS,
        [
            item.row(as_of=as_of, observed_at=observed_at, market=market, status="planned")
            for item in planned
        ],
        ingest_run_id=run_id,
        source=SOURCE,
    )


def submit_orders(
    store: Store,
    clock: Clock,
    broker: Broker,
    *,
    planned: list[PlannedOrder],
    as_of: datetime,
    market: str,
) -> list[Ack]:
    """계획된 주문을 ``Broker`` 로 실제 전송한다. 이 함수가 **전송의 유일한
    자리**다 — 백테스트·shadow·실전이 여기를 같이 탄다(불변식 5).

    ## 적고 나서 보낸다

    주문 하나마다 전송을 시도하기 **전에** 창고에 "제출 시도" 를 먼저 적는다
    (``STATUS_SUBMITTING``). 그 기록의 존재 자체(``ingest_run_recorded``)가
    다음 실행의 판단 근거다:

    - 기록이 없다 → 아직 시도한 적 없다 → 적고 나서 보낸다.
    - 기록이 있다 → 이미 시도했다 → **다시 보내지 않는다.** 그 시도가 실제로
      나갔는지, 나가기 전에 죽었는지는 이 함수가 알 수 없고, 몰라도 상관없다
      — 둘 다 "다시 보내면 안 된다" 로 같은 결론이 난다. 반대 순서(보내고
      나서 적기)로 하면 보낸 뒤 죽었을 때 기록이 없어 다음 실행이 또
      보낸다 — 중복 주문은 되돌릴 수 없다.

    ``client_order_id`` 가 (session_id, entity_id, slice_seq) 로 결정론적이라
    (``orders.py``) 재시작 후 다시 만든 ``planned`` 도 같은 order_id 를
    낸다 — 그래서 이 기록이 재시작을 가로질러도 같은 주문을 알아본다.

    ## BrokerError 뒤에는 재전송하지 않는다

    ``BrokerError`` 는 "나갔는지 모른다" 는 뜻이다(``broker/__init__.py``).
    잡아서 다음 주문으로 넘어가되, 상태를 확정 짓지 않는다 — 이미 적어 둔
    ``STATUS_SUBMITTING`` 을 그대로 최종 상태로 남긴다. "됐다/안 됐다" 로
    덮어쓰면 거짓 확신이 된다. 체결 조회(``broker/fills.py``)로 사실을
    확인하는 것은 이 함수의 몫이 아니다 — 세션마다 한 번 도는 이 함수는
    그 조회를 반복할 자리가 아니다.

    ``RejectedOrder`` (확실히 안 나갔다)만 결과를 구분해 ``STATUS_REJECTED``
    로 남긴다. 다시 낼 수 있는 주문이라는 뜻이지만, 자동 재시도는 이 함수의
    책임이 아니다 — 여기서 즉시 다시 부르면 같은 이유로 또 거부될 뿐이고,
    가격을 바꿔 쫓아가는 재시도는 ``lifecycle.py`` 가 맡는다.
    """
    if not planned:
        return []

    acks: list[Ack] = []
    for item in planned:
        submit_run_id = f"submit-{item.order_id}"
        if store.ingest_run_recorded(ORDERS, submit_run_id):
            # 이미 이 주문에 전송을 시도한 기록이 있다 — 다시 보내지 않는다.
            continue

        # 1. 적는다 — 전송 시도 전에 먼저. revision 을 올려 record_orders 의
        #    "planned" 행보다 항상 나중 상태로 읽히게 한다("적고 나서
        #    보낸다"의 기록 자체는 시각이 아니라 이 순서로 지킨다 — Clock 은
        #    리플레이에서 멈춰 있을 수 있어 observed_at 만으로는 최신 행을
        #    가리지 못한다).
        submitting_row = item.row(
            as_of=as_of, observed_at=clock.now(), market=market, status=STATUS_SUBMITTING
        )
        submitting_row["revision"] = 1
        store.append(
            ORDERS, [submitting_row], ingest_run_id=submit_run_id, source=SOURCE
        )

        # 2. 보낸다.
        try:
            ack = broker.submit(item, as_of=as_of)
        except RejectedOrder as error:
            acks.append(
                Ack(order_id=item.order_id, accepted=False, sent=False, rsp_msg=str(error))
            )
            _record_submit_result(
                store, clock, item, as_of=as_of, market=market, status=STATUS_REJECTED
            )
            continue
        except BrokerError:
            # 나갔는지 모른다 — 재전송 금지. "submitting" 을 최종 상태로 둔다.
            continue

        acks.append(ack)
        _record_submit_result(
            store, clock, item, as_of=as_of, market=market,
            status=STATUS_SENT if ack.sent else STATUS_PAPER,
        )
    return acks


def _record_submit_result(
    store: Store,
    clock: Clock,
    item: PlannedOrder,
    *,
    as_of: datetime,
    market: str,
    status: str,
) -> None:
    """전송 결과를 "submitting" 위 revision 으로 남긴다. 이 기록의 존재
    여부는 재전송 판단에 쓰지 않는다 — 그건 이미 ``submit_run_id`` 로
    끝났다. 이건 순전히 감사·대시보드를 위한 최종 상태 표시다."""
    run_id = f"submit-result-{item.order_id}"
    if store.ingest_run_recorded(ORDERS, run_id):
        return
    row = item.row(as_of=as_of, observed_at=clock.now(), market=market, status=status)
    row["revision"] = 2
    store.append(ORDERS, [row], ingest_run_id=run_id, source=SOURCE)


def record_realized_weights(
    store: Store,
    clock: Clock,
    *,
    sized: list[Sized],
    targets: list[Target],
    as_of: datetime,
    market: str,
    session: str,
) -> int:
    """8단계. 목표 비중과 **실제 집행된 비중**을 나란히 남긴다 (불변식 7).

    주문을 못 낸 종목도 남긴다 — 목표 5% 였는데 라운딩으로 0주가 된 사실이
    기록에 없으면, Allocator 는 자기가 5% 를 샀다고 믿는다.
    """
    realized = {item.entity_id: item.realized_weight for item in sized}
    run_id = f"realized-{session}"
    if store.ingest_run_recorded(REALIZED_WEIGHTS, run_id):
        return 0
    observed_at = clock.now()
    rows = [
        {
            "entity_id": target.entity_id,
            "valid_from": as_of,
            "observed_at": observed_at,
            "source": SOURCE,
            "market": market,
            "session_id": session,
            "target_weight": target.weight,
            "realized_weight": realized.get(target.entity_id, 0.0),
        }
        for target in targets
    ]
    if not rows:
        return 0
    return store.append(REALIZED_WEIGHTS, rows, ingest_run_id=run_id, source=SOURCE)


def action_reflection_rate(store: Store, *, as_of: datetime, lookback: int = 30) -> float:
    """**액션 반영률** — RL 이 낸 결정 중 실제로 집행된 비율.

    선행 프로젝트가 룰로 전락한 유력 원인은 안전장치가 RL 출력을 덮어쓴
    것이다. **30% 미만이면 그건 RL 이 아니라 룰 시스템이다** (CLAUDE.md).
    M4 전에도 계산해 둔다 — 룰 베이스라인에서도 같은 방식으로 덮이기 때문이다.
    """
    frame = store.get(REALIZED_WEIGHTS, as_of=as_of, lookback=lookback)
    if frame.empty:
        return 0.0
    target = frame["target_weight"].abs().sum()
    if target <= 0:
        return 0.0
    matched = (
        1.0 - (frame["target_weight"] - frame["realized_weight"]).abs().sum() / target
    )
    return max(0.0, min(1.0, float(matched)))
