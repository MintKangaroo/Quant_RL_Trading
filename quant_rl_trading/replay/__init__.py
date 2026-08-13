"""Clock, 이벤트 로그, 에이전트 캐시, 체결 시뮬레이터.

백테스트와 라이브는 같은 코드를 쓴다. Clock 만 바꿔 낀다 (불변식 5).

레포에서 벽시계를 읽는 지점은 ``clock.py`` 의 ``LiveClock.now`` 한 줄뿐이고,
그 라인에는 ``# invariant-allow: wallclock`` 이 붙어 있다 (불변식 2).
"""

from quant_rl_trading.replay.cache import AgentCache, CacheKey, features_hash
from quant_rl_trading.replay.clock import Clock, LiveClock, ReplayClock, require_aware
from quant_rl_trading.replay.events import EventLog, canonical_json, payload_hash
from quant_rl_trading.replay.fills import (
    Fill,
    FillParams,
    FillStatus,
    MarketState,
    impact_bps,
    max_position_for_liquidation,
    simulate_fill,
)
from quant_rl_trading.replay.session import SessionResult, run_session

__all__ = [
    "AgentCache",
    "CacheKey",
    "Clock",
    "EventLog",
    "Fill",
    "FillParams",
    "FillStatus",
    "LiveClock",
    "MarketState",
    "ReplayClock",
    "SessionResult",
    "canonical_json",
    "features_hash",
    "impact_bps",
    "max_position_for_liquidation",
    "payload_hash",
    "require_aware",
    "run_session",
    "simulate_fill",
]
