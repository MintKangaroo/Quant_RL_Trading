"""마켓 API — `/api/market`.

**모든 엔드포인트가 ``as_of`` 를 받는다** (불변식 9). 규약은 ``common.scope()``
가 지키고, 여기서는 다시 적지 않는다.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, current_app

from quant_rl_trading.dashboard.api.common import envelope, scope, store
from quant_rl_trading.dashboard.services import market as service

bp = Blueprint("market_api", __name__, url_prefix="/api/market")


@bp.get("")
def overview() -> Any:
    current = scope()
    return envelope(
        current,
        service.payload(
            store(),
            as_of=current.as_of,
            lookback=current.lookback,
            # 트레이딩 탭과 **같은 캐시 객체**다. 두 탭이 각자 만들면 토큰을
            # 두 번 발급받고 TTL 도 따로 돈다 — 실측으로 그 둘이 응답을
            # 2.7초에서 9.7초로 만든 원인이었다.
            live_quotes=current_app.config.get("QUANT_RL_LIVE_QUOTES"),
        ),
    )
