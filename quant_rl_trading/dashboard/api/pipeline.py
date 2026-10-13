"""매매 과정 API — `/api/pipeline` (dashboard.md §7-2).

**모든 엔드포인트가 ``as_of`` 를 받는다** (불변식 9). 장부는 다른 탭과 같은 관례 — 주 장부(모의계좌), ``?ledger=shadow`` 면
shadow. 모델 파일·시행 대장·shadow 장부 짝은 연구 창고(``data``) 쪽에서 읽는다(학습 탭과 같은 곳).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from flask import Blueprint, request
from werkzeug.exceptions import BadRequest

from quant_rl_trading.dashboard.api.common import envelope, research_store, scope, store
from quant_rl_trading.dashboard.services import headlines as headlines_service
from quant_rl_trading.dashboard.services import pipeline_view as service

bp = Blueprint("pipeline_api", __name__, url_prefix="/api/pipeline")

MARKETS = ("KR", "US")


def _market() -> str:
    value = (request.args.get("market") or "KR").upper()
    if value not in MARKETS:
        # 종합(ALL)은 흐름이 시장마다 다르다 — 국장으로 보인다.
        if value == "ALL":
            return "KR"
        raise BadRequest(f"market 은 {MARKETS} 중 하나여야 한다: {value!r}")
    return value


@bp.get("")
def flow() -> Any:
    """흐름도 — 단계마다 들어온 수·나간 수·켜짐·종류·설정·탈락 예시 + 후보 칩."""
    current = scope()
    market = _market()
    research = research_store()
    root = Path(research.root)
    data = service.flow(store(), as_of=current.as_of, market=market, models_root=root)
    data["candidates"] = service.candidates(research, store(), as_of=current.as_of, market=market,
                                            root=root)["candidates"]
    return envelope(current, data)


@bp.get("/inside")
def inside() -> Any:
    """AI 속 들여다보기 — 한 종목(``entity``, 없으면 선정 명단 첫 종목)."""
    current = scope()
    entity = (request.args.get("entity") or "").strip() or None
    return envelope(current, service.inside(store(), as_of=current.as_of, market=_market(), entity_id=entity,
                                            models_root=Path(research_store().root)))


@bp.get("/blocks")
def blocks() -> Any:
    """③ 거르기 — 뉴스 매수 금지 명단(News·SNS 판정, 매수 금지만). 중요 시황 탭에서 옮겼다(2026-10-13 밤)."""
    current = scope()
    return envelope(current, headlines_service.blocks_payload(store(), as_of=current.as_of,
                                                                     lookback=current.lookback, market=_market()))
