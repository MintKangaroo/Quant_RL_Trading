"""트레이딩 API — `/api/trading`.

목업이 있던 ``/api/mock/trading`` 을 대체한다. 경로를 처음부터 갈라 둔 덕에
화면만 바꿔 끼우면 됐다.

**모든 엔드포인트가 ``as_of`` 를 받는다** (불변식 9). 차트 엔드포인트도
예외가 아니다 — 종목 하나짜리 조회라고 봐주면 그 하나가 미래를 본다.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, current_app, request
from werkzeug.exceptions import BadRequest

from quant_rl_trading.dashboard.api.common import clock, envelope, scope, store
from quant_rl_trading.dashboard.services import trading as service
from quant_rl_trading.executor import guards

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
            live_quotes=current_app.config.get("QUANT_RL_LIVE_QUOTES"),
        ),
    )


@bp.get("/calendar")
def calendar() -> Any:
    """수익률 캘린더만. **장부를 접지 않는다.**

    달력은 ``nav_daily`` 하나만 있으면 그려진다. 별도 창이 ``/api/trading`` 을
    부르면 보유·후보·스냅샷까지 전부 다시 계산하게 되는데, 그 값은 이 화면에
    한 글자도 안 나온다.
    """
    current = scope()
    return envelope(
        current,
        service.calendar_payload(store(), as_of=current.as_of, lookback=current.lookback),
    )


@bp.post("/killswitch")
def killswitch() -> Any:
    """EMERGENCY STOP — 화면에서 매매를 멈춘다.

    **읽기 전용 대시보드에서 유일한 쓰기다.** 그 예외를 두는 이유는, 멈추는
    수단이 터미널에만 있으면 정작 멈춰야 할 순간에 못 멈추기 때문이다
    (runbook.md 킬스위치).

    발동은 ``guards.engage`` 를 그대로 부른다. 화면이 자기 로직을 갖지 않는다 —
    Executor 가 보는 상태와 화면이 미는 상태가 갈라지면 안전장치가 아니다.

    **해제도 여기서 가능하지만 이유를 요구한다.** 이유 없는 해제는 운영
    기록에서 "왜 풀었나" 를 영영 답할 수 없게 만든다.
    """
    current = scope()
    if not current.live:
        # 과거 시점으로 되감은 채 발동하면 기록의 valid_from 이 과거가 된다.
        raise BadRequest("킬스위치는 라이브에서만 조작한다 (as_of 를 비울 것)")

    payload = request.get_json(silent=True) or {}
    action = str(payload.get("action") or "").lower()
    reason = str(payload.get("reason") or "").strip()
    by = str(payload.get("by") or "dashboard")
    if action not in {"engage", "release"}:
        raise BadRequest("action 은 engage 또는 release 여야 한다")
    if not reason:
        raise BadRequest("reason 이 필요하다. 이유 없는 발동·해제는 운영할 수 없다")

    now = clock().now()
    if action == "engage":
        written = guards.engage(
            store(), as_of=now, observed_at=now, reason=reason, by=by
        )
    else:
        written = guards.release(
            store(), as_of=now, observed_at=now, by=by, reason=reason
        )
    state, current_reason = guards.killswitch_state(store(), as_of=now)
    return envelope(
        current,
        {"action": action, "written": written, "state": str(state), "reason": current_reason},
    )


#: 화면 세그먼트 버튼과 같은 값. "1D" 는 지금까지의 유일한 값이었다 —
#: 기존 호출부(쿼리에 interval 을 안 주는 호출)가 그대로 일봉을 받도록
#: 기본값으로 둔다(하위호환).
DAILY_INTERVAL = "1D"


@bp.get("/chart")
def chart() -> Any:
    """한 종목의 봉과 우리 체결 흔적.

    ``interval`` 을 안 주거나 ``1D`` 면 지금까지의 일봉 응답 그대로다.
    분봉 다섯 개(1m/5m/15m/1H/4H) 는 창고에 ``prices_intraday`` 가 있을
    때만 뜻이 있다 — 그래서 응답에 ``available_intervals`` 를 **항상**
    같이 실어서, 화면이 별도 호출 없이 그 값으로 버튼을 켜고 끈다
    (``templates/trading.html`` 의 disabled 버튼들, "없는 봉을 그리지
    마라" 원칙의 반대쪽).
    """
    current = scope()
    entity = request.args.get("entity")
    if not entity:
        raise BadRequest("entity 가 필요하다")
    market = _market()
    interval = request.args.get("interval") or DAILY_INTERVAL

    available = service.available_intraday_intervals(
        store(), as_of=current.as_of, entity_id=entity, market=market
    )

    if interval == DAILY_INTERVAL:
        data = service.candles(
            store(),
            as_of=current.as_of,
            entity_id=entity,
            market=market,
            lookback=current.lookback,
        )
    elif interval in service.INTRADAY_INTERVALS:
        data = service.intraday_candles(
            store(), as_of=current.as_of, entity_id=entity, market=market, interval=interval
        )
    else:
        raise BadRequest(
            f"interval 은 {DAILY_INTERVAL!r} 또는 {service.INTRADAY_INTERVALS} 중 하나여야 한다: "
            f"{interval!r}"
        )

    data["available_intervals"] = available
    return envelope(current, data)
