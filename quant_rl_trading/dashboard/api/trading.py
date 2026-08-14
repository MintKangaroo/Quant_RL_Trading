"""트레이딩 API — `/api/trading`.

목업이 있던 ``/api/mock/trading`` 을 대체한다. 경로를 처음부터 갈라 둔 덕에
화면만 바꿔 끼우면 됐다.

**모든 엔드포인트가 ``as_of`` 를 받는다** (불변식 9). 차트 엔드포인트도
예외가 아니다 — 종목 하나짜리 조회라고 봐주면 그 하나가 미래를 본다.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, request
from werkzeug.exceptions import BadRequest

from quant_rl_trading.dashboard.api.common import clock, envelope, scope, store
from quant_rl_trading.dashboard.services import trading as service

bp = Blueprint("trading_api", __name__, url_prefix="/api/trading")

#: 화면이 다루는 시장. 창고에 없는 시장을 받아 빈 화면을 그리지 않는다.
MARKETS = ("KR", "US")


def _market() -> str:
    value = (request.args.get("market") or "KR").upper()
    if value not in MARKETS:
        raise BadRequest(f"market 은 {MARKETS} 중 하나여야 한다: {value!r}")
    return value


@bp.get("")
def overview() -> Any:
    current = scope()
    return envelope(
        current,
        service.payload(
            store(),
            clock(),
            as_of=current.as_of,
            market=_market(),
            lookback=current.lookback,
            entity_id=request.args.get("entity") or None,
        ),
    )


@bp.get("/chart")
def chart() -> Any:
    """한 종목의 봉과 우리 체결 흔적."""
    current = scope()
    entity = request.args.get("entity")
    if not entity:
        raise BadRequest("entity 가 필요하다")
    return envelope(
        current,
        service.candles(
            store(),
            as_of=current.as_of,
            entity_id=entity,
            market=_market(),
            lookback=current.lookback,
        ),
    )
