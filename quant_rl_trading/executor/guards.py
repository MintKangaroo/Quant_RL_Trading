"""안전장치 — **순서가 곧 안전이다** (agents.md §7).

    1. 킬스위치 latch      걸려 있으면 여기서 끝
    2. 데이터 품질 게이트
    3. 시장 서킷 브레이커
    4. defer 게이트        개장 후 30분 신규매수 보류

**Executor 안에는 AI 가 없다** (불변식 6). 마지막 안전장치는 예측 가능해야
한다 — 왜 막혔는지 사람이 읽고 30초 안에 판단할 수 있어야 한다.

## latch 인 이유

킬스위치가 자동으로 풀리면, 발동 원인이 남아 있는 채로 다시 매매한다.
낙폭 30% 로 걸렸는데 다음 날 반등해서 29% 가 되면 자동 해제되는 식이다.
그건 안전장치가 아니라 지연장치다. **사람이 푼다.**
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

from quant_rl_trading.store.prices import read_prices

if TYPE_CHECKING:
    from quant_rl_trading.store import Store

KILLSWITCH = "killswitch"
NAV_DAILY = "nav_daily"
INDICES = "indices"
ACCOUNT = "FUND"
SOURCE = "executor"


class KillswitchState(StrEnum):
    ENGAGED = "engaged"
    RELEASED = "released"


@dataclass(frozen=True)
class GateResult:
    """통과 여부와 **이유**. 이유 없는 차단은 운영할 수 없다."""

    passed: bool
    reason: str = ""
    #: 발동 시 **전 포지션을 강제 청산**할 것인가 (killswitch.liquidate_on_trigger).
    #: 매도 자체는 이것과 무관하게 언제나 허용된다 — 청산을 막는 안전장치는
    #: 빠져나올 길을 막는 것이라 안전장치가 아니다.
    force_liquidation: bool = False

    def __bool__(self) -> bool:
        return self.passed


def killswitch_state(store: Store, *, as_of: datetime) -> tuple[KillswitchState, str]:
    """현재 latch 상태. 마지막 기록이 곧 상태다."""
    frame = store.get(KILLSWITCH, as_of=as_of, entity=ACCOUNT)
    if frame.empty:
        return KillswitchState.RELEASED, ""
    latest = frame.sort_values(["valid_from", "observed_at"]).iloc[-1]
    return KillswitchState(str(latest["state"])), str(latest["reason"])


def engage(
    store: Store, *, as_of: datetime, observed_at: datetime, reason: str, by: str
) -> int:
    """킬스위치를 건다. 같은 이유로 두 번 걸어도 한 번만 기록된다."""
    run_id = f"ks-engage-{as_of.date().isoformat()}-{by}"
    if store.ingest_run_recorded(KILLSWITCH, run_id):
        return 0
    return store.append(
        KILLSWITCH,
        [{
            "entity_id": ACCOUNT,
            "valid_from": as_of,
            "observed_at": observed_at,
            "source": SOURCE,
            "state": str(KillswitchState.ENGAGED),
            "reason": reason,
            "triggered_by": by,
        }],
        ingest_run_id=run_id,
        source=SOURCE,
    )


def release(
    store: Store, *, as_of: datetime, observed_at: datetime, by: str, reason: str = ""
) -> int:
    """해제는 **사람만** 한다. 코드가 자동으로 부르는 경로는 없어야 한다."""
    run_id = f"ks-release-{as_of.isoformat()}-{by}"
    if store.ingest_run_recorded(KILLSWITCH, run_id):
        return 0
    return store.append(
        KILLSWITCH,
        [{
            "entity_id": ACCOUNT,
            "valid_from": as_of,
            "observed_at": observed_at,
            "source": SOURCE,
            "state": str(KillswitchState.RELEASED),
            "reason": reason or f"{by} 수동 해제",
            "triggered_by": by,
        }],
        ingest_run_id=run_id,
        source=SOURCE,
    )


# -- 1. 킬스위치 --------------------------------------------------------------------


def check_killswitch(store: Store, *, as_of: datetime) -> GateResult:
    state, reason = killswitch_state(store, as_of=as_of)
    if state is KillswitchState.ENGAGED:
        return GateResult(
            passed=False,
            reason=f"킬스위치 발동 중 — {reason}",
            # 기본(false)은 신규매수만 차단. true 면 전 포지션을 던진다.
            force_liquidation=bool(
                store.config("killswitch.liquidate_on_trigger", as_of=as_of)
            ),
        )
    return GateResult(passed=True)


def should_engage(store: Store, *, as_of: datetime) -> str | None:
    """발동 조건에 걸렸는가. 걸렸으면 이유를 돌려준다.

    낙폭은 **TWR 누적지수 기준**이다 (accounting.md §6). NAV 원금액으로 재면
    입금이 낙폭을 지워서 이 판정이 영영 안 걸린다.
    """
    trigger = float(store.config("killswitch.drawdown_trigger", as_of=as_of))
    frame = store.get(NAV_DAILY, as_of=as_of, entity=ACCOUNT)
    if frame.empty:
        return None
    latest = frame.sort_values("valid_from").iloc[-1]
    drawdown = float(latest["drawdown"])
    if drawdown <= -abs(trigger):
        return f"낙폭 {drawdown:.1%} 가 한계 {-abs(trigger):.0%} 를 넘었다"
    return None


# -- 2. 데이터 품질 -----------------------------------------------------------------


def check_data_quality(
    store: Store, *, as_of: datetime, market: str, entities: list[str]
) -> GateResult:
    """오늘 시세가 있는가. **없는 데이터로 주문을 만들지 않는다.**

    가격이 없으면 목표비중을 수량으로 바꿀 수 없다. 어제 가격으로 대신하면
    상한가·하한가에 걸린 종목을 어제 가격으로 사려 들고, 그 주문은 하루 종일
    체결되지 않는다.
    """
    if not entities:
        return GateResult(passed=True)
    # 휴장일 행에는 종가 0 이 들어 있다. 그대로 세면 "시세가 있다" 로 통과한
    # 뒤 0 원짜리 가격으로 주문 수량을 만들게 된다 — 행이 있다는 것과 가격이
    # 있다는 것은 다르다.
    prices = read_prices(store, as_of=as_of, entity=entities, lookback=5, market=market)
    if prices.empty:
        return GateResult(passed=False, reason="시세가 없다")

    latest_session = prices["valid_from"].max()
    covered = set(prices[prices["valid_from"] == latest_session]["entity_id"])
    missing = [entity for entity in entities if entity not in covered]
    threshold = float(store.config("data_quality.missing_warn", as_of=as_of))
    if len(missing) / len(entities) > threshold:
        return GateResult(
            passed=False,
            reason=f"최근 세션 시세 결측 {len(missing)}/{len(entities)}종목",
        )
    return GateResult(passed=True)


# -- 3. 서킷 브레이커 ---------------------------------------------------------------

#: 한국거래소 1단계 서킷브레이커. 지수가 전일 대비 이만큼 빠지면 발동한다.
#: **KOSPI·KOSDAQ 을 따로 본다** — 한쪽만 걸리는 날이 실제로 있다.
CIRCUIT_BREAKER_DROP = 0.08

BOARD_INDEX = {"KOSPI": "IDX:KOSPI", "KOSDAQ": "IDX:KOSDAQ"}


def check_circuit_breaker(store: Store, *, as_of: datetime, board: str) -> GateResult:
    """지수 급락일에는 신규매수를 멈춘다.

    지수 데이터가 없으면 **통과시킨다.** 없는 것을 발동으로 읽으면 지수 수집이
    하루 밀린 날 펀드가 통째로 멈춘다 — 안전장치가 스스로 사고를 만든다.
    다만 그 사실은 이유에 남긴다.
    """
    entity = BOARD_INDEX.get(board)
    if entity is None:
        return GateResult(passed=True, reason=f"{board}: 지수 매핑 없음")

    frame = store.get(INDICES, as_of=as_of, entity=entity, lookback=10)
    if len(frame) < 2:
        return GateResult(passed=True, reason=f"{board}: 지수 관측 부족 — 판정 없이 통과")

    ordered = frame.sort_values("valid_from")
    closes = ordered["close"].astype(float).tolist()
    change = closes[-1] / closes[-2] - 1.0 if closes[-2] else 0.0
    if change <= -CIRCUIT_BREAKER_DROP:
        return GateResult(passed=False, reason=f"{board} 서킷브레이커 — 지수 {change:.1%}")
    return GateResult(passed=True)


# -- 4. defer 게이트 ----------------------------------------------------------------


def check_defer(
    store: Store, *, as_of: datetime, market_open: datetime
) -> GateResult:
    """개장 후 N분은 신규매수를 보류한다.

    개장 직후는 호가가 얇고 변동성이 크다. 그때 시장가에 가까운 주문을 내면
    슬리피지가 백테스트 가정을 훨씬 넘는다 — 그 차이가 그대로 성과에서 빠진다.
    """
    minutes = int(store.config("execution.defer_minutes", as_of=as_of))
    edge = market_open + timedelta(minutes=minutes)
    if as_of < edge:
        return GateResult(
            passed=False,
            reason=f"개장 후 {minutes}분 보류 — {edge.isoformat()} 이후 집행",
        )
    return GateResult(passed=True)
