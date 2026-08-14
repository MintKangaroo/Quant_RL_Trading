"""여러 날을 굴린다. **전략은 여기 없다.**

하루의 순서는 `docs/design/backtest.md` §1 그대로다.

    1. 체결   ← 어제 낸 주문을 오늘 종가에 맞춘다
    2. 신호   ← 그날 Analyst 점수
    3. 스냅샷 ← 체결이 반영된 장부로 NAV·낙폭을 남긴다
    4. 결정   ← 오늘 종가를 보고 내일 낼 주문을 만든다

전략(선정·배분·집행)은 ``session/daily.py`` 가 한다. 이 파일이 그 안을 알면
백테스트가 자기 전략을 채점하게 되고, 그때부터 성적은 검증이 아니라 자평이다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from quant_rl_trading.accounting import ledger as ledger_module
from quant_rl_trading.accounting import snapshot as snapshot_module
from quant_rl_trading.accounting.rates import Rates
from quant_rl_trading.backtest import execution as execution_module
from quant_rl_trading.backtest import stats as stats_module
from quant_rl_trading.collectors.market_hours import Market, trading_days
from quant_rl_trading.executor import orders as orders_module
from quant_rl_trading.executor import pipeline as executor_pipeline
from quant_rl_trading.replay.clock import ReplayClock
from quant_rl_trading.replay.events import payload_hash
from quant_rl_trading.session import daily as daily_module
from quant_rl_trading.session import signals as signals_module
from quant_rl_trading.store import DuplicateIngestRun

if TYPE_CHECKING:
    from quant_rl_trading.replay.clock import Clock
    from quant_rl_trading.store import Store

#: 하루가 끝날 때마다 부르는 것. 긴 구간을 돌릴 때 진행 상황을 보기 위한 자리다.
DayCallback = Callable[["DayResult"], None]

CAPITAL_FLOWS = "capital_flows"
SOURCE = "backtest"

SEOUL = ZoneInfo("Asia/Seoul")

#: 회계 스냅샷 시각의 기본값. 실제 값은 ``accounting.snapshot_time`` 에서 온다
#: (불변식 10) — 설정을 못 읽는 상황에서만 이 값으로 떨어진다.
DEFAULT_SNAPSHOT_TIME = time(15, 40)


@dataclass(frozen=True)
class DayResult:
    as_of: datetime
    nav: float
    index_value: float
    drawdown: float
    twr_return: float
    candidates: tuple[str, ...]
    planned_orders: int
    requested: int
    filled: int
    traded_value: float
    blocked_by: str
    notes: tuple[str, ...]
    #: 성적 집계에서 빠지는 날. 신호 이력을 쌓기 위해 돌린 구간이다.
    warmup: bool = False


@dataclass
class BacktestResult:
    market: str
    start: date
    end: date
    days: list[DayResult] = field(default_factory=list)
    performance: stats_module.Performance | None = None
    notes: list[str] = field(default_factory=list)

    def digest(self) -> str:
        """구간 지문. 같은 구간을 두 번 돌리면 같아야 한다 (backtest.md §7).

        벽시계는 넣지 않는다 — 리플레이마다 당연히 다르므로 넣으면 결정론
        검증이 영원히 실패한다.
        """
        return payload_hash(
            [
                {
                    "as_of": day.as_of.isoformat(),
                    "nav": round(day.nav, 4),
                    "index_value": round(day.index_value, 6),
                    "candidates": list(day.candidates),
                    "filled": day.filled,
                    "traded_value": round(day.traded_value, 4),
                }
                for day in self.days
            ]
        )


def snapshot_moment(store: Store, day: date, *, as_of: datetime) -> datetime:
    """그날의 회계 스냅샷 시각(한국시간). 하루의 기준 시각은 하나뿐이다.

    ``as_of`` 는 설정을 읽기 위한 시점이다 — 임계치도 이중시간이라 그날 유효한
    값을 봐야 한다.
    """
    try:
        raw = str(store.config("accounting.snapshot_time", as_of=as_of))
        moment = time.fromisoformat(raw)
    except Exception:  # 설정이 없거나 형식이 다르면 기본값. 조용히 0시로 가지 않는다.
        moment = DEFAULT_SNAPSHOT_TIME
    return datetime.combine(day, moment, tzinfo=SEOUL)


def seed_capital(
    store: Store,
    clock: Clock,
    *,
    amount: float,
    as_of: datetime,
    currency: str = "KRW",
) -> int:
    """초기 자본을 ``capital_flows`` 한 행으로 넣는다.

    NAV 를 인자로 주지 않는 이유는 TWR 때문이다 — 돈이 언제 들어왔는지
    기록에 없으면 입금일의 NAV 상승이 수익으로 잡힌다 (accounting.md §6).
    """
    run_id = f"backtest-seed-{as_of.date().isoformat()}"
    if store.ingest_run_recorded(CAPITAL_FLOWS, run_id):
        return 0
    try:
        return int(
            store.append(
                CAPITAL_FLOWS,
                [
                    {
                        "entity_id": ledger_module.ACCOUNT,
                        "valid_from": as_of,
                        "observed_at": clock.now(),
                        "source": SOURCE,
                        "currency": currency,
                        "amount": amount,
                        "kind": "deposit",
                    }
                ],
                ingest_run_id=run_id,
                source=SOURCE,
            )
        )
    except DuplicateIngestRun:
        return 0


def run(
    store: Store,
    *,
    start: date,
    end: date,
    market: str = "KR",
    capital: float = 0.0,
    board: str = "KOSPI",
    warmup_days: int = 0,
    produce_signals: bool = True,
    on_day: DayCallback | None = None,
) -> BacktestResult:
    """구간 백테스트.

    ``capital`` 이 0 보다 크면 첫 거래일 **전날**에 입금 한 행을 넣는다. 이미
    자본이 들어와 있는 창고(이어 돌리기)에서는 0 으로 두면 된다.

    ``produce_signals`` 를 끄면 Analyst 를 돌리지 않고 **창고에 이미 있는
    신호**를 쓴다. shadow 가 그렇게 돈다 — 라이브에서는 일일 실행기가 이미
    실전 창고에 신호를 쌓고 있고, shadow 가 자기 신호를 따로 만들면 confidence
    계산이 자기가 만든 짧은 이력만 보게 되어 실전과 다른 결정을 낸다.

    ``warmup_days`` 는 ``start`` 앞에서 **돌리되 성적에 넣지 않는** 거래일 수다.
    confidence 가 최근 60거래일 롤링 IC 라(analysts/ic.py), 신호 이력이 없는
    구간에서는 0 이고 **0 이면 합성 점수가 통째로 비어 아무것도 사지 않는다.**
    그 구간을 성적에 넣으면 평평한 앞부분이 MDD 를 실제보다 작게 만든다
    (backtest.md §1).
    """
    market_enum = Market(market)
    scored = trading_days(market_enum, start, end)
    sessions = list(scored)
    warmup: list[date] = []
    if warmup_days > 0 and scored:
        # 거래일 기준 창을 달력일로 넉넉히 열어 잡은 뒤 뒤에서 센다.
        span = timedelta(days=int(warmup_days * 7 / 5) + 14)
        earlier = trading_days(market_enum, start - span, start)
        warmup = [day for day in earlier if day < start][-warmup_days:]
        sessions = warmup + sessions
    result = BacktestResult(market=market, start=start, end=end)
    if not scored:
        result.notes.append(f"{start}~{end} 구간에 {market} 거래일이 없다")
        return result

    # 설정을 읽으려면 시점이 필요한데, 그 시점을 벽시계에서 가져오면 불변식 2 를
    # 어긴다. 첫 거래일의 기본 스냅샷 시각을 탐침으로 쓴다 — 설정은 이중시간이라
    # 그날 유효한 값이 나온다.
    probe = datetime.combine(sessions[0], DEFAULT_SNAPSHOT_TIME, tzinfo=SEOUL)
    first = snapshot_moment(store, sessions[0], as_of=probe)
    if capital > 0:
        opening = snapshot_moment(store, sessions[0], as_of=first) - timedelta(days=1)
        seed_capital(store, ReplayClock(opening), amount=capital, as_of=opening)

    warmup_set = set(warmup)
    index_values: list[float] = []
    returns: list[float] = []
    navs: list[float] = []
    traded_value = 0.0
    requested = 0
    filled = 0
    previous_session: str | None = None

    for day in sessions:
        as_of = snapshot_moment(store, day, as_of=first)
        clock = ReplayClock(as_of)

        # 1. 체결 — 어제 낸 주문을 오늘 봉에 맞춘다.
        executed = execution_module.ExecutionDay(session_id=previous_session or "")
        if previous_session:
            executed = execution_module.run(
                store, clock, as_of=as_of, market=market, session_id=previous_session
            )
            if day not in warmup_set:
                traded_value += executed.traded_value
                requested += executed.requested
                filled += executed.filled

        # 2. 신호 — 결정보다 먼저. 없으면 Selector 가 볼 것이 없다.
        scoring = (
            signals_module.produce(store, market=market_enum, as_of=as_of)
            if produce_signals
            else signals_module.ScoringResult()
        )

        # 3. 스냅샷 — 체결이 반영된 장부로. NAV 는 회계 한 곳에서만 나온다.
        rates = Rates.from_store(store, as_of=as_of)
        book = ledger_module.build_book(store, as_of=as_of, rates=rates)
        snapshot = snapshot_module.take(store, clock, as_of=as_of, book=book)
        snapshot_module.write(store, clock, snapshot=snapshot)

        # 4. 결정 — 주문을 만들고 기록한다. 보내지는 않는다.
        holdings = {
            entity: int(position.quantity)
            for entity, position in book.positions.items()
            if position.quantity > 0
        }
        session = daily_module.run(
            store,
            clock,
            as_of=as_of,
            market=market,
            holdings=holdings,
            board=board,
            wall_clock=clock,
        )
        previous_session = orders_module.session_id(as_of=as_of, market=market)

        notes = [
            *executed.notes[:3],
            *[f"신호 {name}" for name in scoring.warnings[:3]],
            *session.notes[:5],
        ]
        entry = DayResult(
            as_of=as_of,
            nav=snapshot.valuation.nav,
            index_value=snapshot.index_value,
            drawdown=snapshot.drawdown,
            twr_return=snapshot.twr_return,
            candidates=session.candidates,
            planned_orders=len(session.orders),
            requested=executed.requested,
            filled=executed.filled,
            traded_value=executed.traded_value,
            blocked_by=session.blocked_by,
            notes=tuple(notes),
            warmup=day in warmup_set,
        )
        result.days.append(entry)
        if day not in warmup_set:
            index_values.append(snapshot.index_value)
            returns.append(snapshot.twr_return)
            navs.append(snapshot.valuation.nav)
        if on_day is not None:
            on_day(entry)

    last = result.days[-1].as_of
    result.performance = stats_module.summarize(
        index_values=index_values,
        # 첫날 수익률은 0 이다(비교할 어제가 없다). 표본에 넣으면 변동성이
        # 인위적으로 낮아진다.
        returns=returns[1:],
        traded_value=traded_value,
        average_nav=sum(navs) / len(navs) if navs else 0.0,
        requested=requested,
        filled=filled,
        action_reflection=executor_pipeline.action_reflection_rate(
            store, as_of=last, lookback=(end - start).days + 1
        ),
    )
    return result
