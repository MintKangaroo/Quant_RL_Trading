"""목업 트레이딩 엔드포인트.

**경로가 ``/api/mock`` 인 것이 설계다.** 실제 트레이딩 API 는 M3 에서
``/api/trading`` 으로 붙는다 — 두 경로가 겹치지 않으므로, 목업이 남아 있어도
실제 화면이 목업을 부를 일이 없다. M3 에서 이 파일은 통째로 지운다.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint

from lattice.dashboard.api.common import envelope, scope
from lattice.dashboard.services import mock_trading

bp = Blueprint("mock_api", __name__, url_prefix="/api/mock")


@bp.get("/trading")
def trading() -> Any:
    current = scope()
    return envelope(current, mock_trading.payload(current.as_of))
