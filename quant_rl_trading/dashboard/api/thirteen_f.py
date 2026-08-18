"""13F API — `/api/thirteen-f`.

**모든 엔드포인트가 ``as_of`` 를 받는다** (불변식 9). 규약은 ``common.scope()``
가 지킨다.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, request

from quant_rl_trading.dashboard.api.common import envelope, scope, store
from quant_rl_trading.dashboard.services import thirteen_f as service

bp = Blueprint("thirteen_f_api", __name__, url_prefix="/api/thirteen-f")


@bp.get("/filers")
def filers() -> Any:
    current = scope()
    return envelope(current, {"filers": service.filers(store(), as_of=current.as_of)})


@bp.get("/holdings")
def holdings() -> Any:
    current = scope()
    cik = request.args.get("cik", "")
    if not cik:
        # 기본은 규모 1위. 빈 화면을 주는 것보다 낫고, 무엇을 골랐는지는
        # 응답에 filer_cik 로 실려 나가므로 화면이 헷갈리지 않는다.
        rows = service.filers(store(), as_of=current.as_of)
        if not rows:
            return envelope(current, {"rows": [], "note": "13F 를 아직 한 건도 안 받았다."})
        cik = rows[0]["filer_cik"]
    payload = service.holdings(store(), as_of=current.as_of, filer_cik=cik)
    payload["filer_cik"] = cik
    return envelope(current, payload)


@bp.get("/consensus")
def consensus() -> Any:
    current = scope()
    return envelope(current, {"rows": service.consensus(store(), as_of=current.as_of)})
