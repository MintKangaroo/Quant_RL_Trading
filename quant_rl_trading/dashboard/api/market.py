"""마켓 API — `/api/market`.

**모든 엔드포인트가 ``as_of`` 를 받는다** (불변식 9). 규약은 ``common.scope()``
가 지키고, 여기서는 다시 적지 않는다.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, current_app, request
from werkzeug.exceptions import BadRequest

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
            live_index=current_app.config.get("QUANT_RL_LIVE_INDEX"),
        ),
    )


@bp.get("/chart")
def chart() -> Any:
    """패널 하나를 다른 봉으로 다시 그린 것.

    첫 화면은 이 경로를 부르지 않는다 — 일봉은 ``/api/market`` 응답에 이미
    실려 온다. 여기는 **버튼을 눌렀을 때만** 열리는 문이고, 그래서 첫 로드에
    요청이 여섯 개 더 나가지 않는다.

    ``interval`` 을 안 주면 일봉이다(하위호환). 창고에 없는 구간을 물어도
    500 이 아니라 빈 응답에 ``reason`` 이 실려 온다 — 화면이 "왜 비었나" 를
    말할 수 있어야 하기 때문이다.
    """
    current = scope()
    entity = request.args.get("entity")
    if not entity:
        raise BadRequest("entity 가 필요하다")
    market = (request.args.get("market") or "KR").upper()
    if market not in service.MARKETS:
        raise BadRequest(f"market 은 {service.MARKETS} 중 하나여야 한다: {market!r}")
    interval = request.args.get("interval") or service.DAILY_INTERVAL
    if interval not in service.PANEL_INTERVALS:
        raise BadRequest(
            f"interval 은 {service.PANEL_INTERVALS} 중 하나여야 한다: {interval!r}"
        )
    return envelope(
        current,
        service.panel_candles(
            store(),
            as_of=current.as_of,
            lookback=current.lookback,
            market=market,
            entity_id=entity,
            interval=interval,
        ),
    )
