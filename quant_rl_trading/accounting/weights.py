"""Refresh execution feedback from the fill ledger and the accounting valuation."""

from __future__ import annotations

import hashlib
import json
import logging
import math
from datetime import datetime
from typing import TYPE_CHECKING

from quant_rl_trading.accounting import ledger, snapshot
from quant_rl_trading.accounting.book import USD
from quant_rl_trading.accounting.rates import Rates
from quant_rl_trading.store import DuplicateIngestRun

if TYPE_CHECKING:
    from quant_rl_trading.replay.clock import Clock
    from quant_rl_trading.store import Store

TABLE = "realized_weights"
SOURCE = "accounting_fill_v2"

logger = logging.getLogger(__name__)


def refresh(
    store: Store,
    clock: Clock,
    *,
    as_of: datetime,
    sessions: set[str],
) -> int:
    if not sessions:
        return 0
    frame = store.get(TABLE, as_of=as_of)
    if frame.empty:
        return 0
    frame = frame[frame["session_id"].isin(sessions)]
    if frame.empty:
        return 0
    rates = Rates.from_store(store, as_of=as_of)
    book = ledger.build_book(store, as_of=as_of, rates=rates)
    try:
        valuation = snapshot.take(store, clock, as_of=as_of, book=book).valuation
    except KeyError as exc:
        # **시세 없는 보유 하나로 대사를 죽이지 않는다.** NAV 는 가격 없는
        # 보유에서 예외를 던진다(의도된 것 — `nav.value` 참조). 그런데 이
        # 함수는 `broker.fills.sync` 가 trades 를 적은 **뒤에** 불린다. 예전엔
        # 여기서 터지면 장부는 남고 주문 상태는 낡은 채 남아, 대사 도구가
        # 죽을 때마다 주문이 영원히 "전송됨" 으로 보였다.
        #
        # NAV 를 여기서 따로 계산하지는 않는다 (NAV 는 accounting 한 곳에서만).
        # 갱신을 **포기하고 그 사실을 크게 남긴다** — 조용히 0 으로 채우면
        # 그 종목을 안 들고 있다는 거짓이 되고 반영률이 조용히 낮아진다.
        logger.error(
            "실현비중 갱신 포기 — 평가가 시세 결손으로 실패했다: %s (세션 %s)",
            exc,
            ", ".join(sorted(sessions)),
        )
        return 0
    prices = snapshot.last_prices(store, as_of=as_of, entities=sorted(book.positions))
    rows = []
    unpriced: list[str] = []
    for record in frame.to_dict(orient="records"):
        entity = str(record["entity_id"])
        position = book.positions.get(entity)
        weight = 0.0
        if position is not None and position.quantity:
            # **시세가 없으면 이 행을 건너뛴다.** `last_prices` 는 창 안에 쓸 수
            # 있는 종가가 없는 종목을 아예 빼고 돌려주므로, 바로 꺼내면
            # KeyError 가 난다. 이 함수는 `broker.fills.sync` 가 trades 를 적은
            # **뒤에** 불리므로, 여기서 터지면 장부는 남고 대사 도구만 죽어
            # 주문 상태가 낡은 채로 남는다 (실제로 그렇게 죽었다).
            #
            # 0 으로 채우면 "그 종목을 안 들고 있다" 는 거짓이 되고, 반영률이
            # 조용히 낮아진다. 그래서 정정본을 아예 쓰지 않고(집행 쪽이 남긴
            # 원래 값·보통 None 이 그대로 남는다) 사실을 로그로 내보낸다 —
            # 반영률 계산(`executor.pipeline.action_reflection_detail`)이
            # 모름 행을 빼고 센다.
            price = prices.get(entity)
            if price is None or not math.isfinite(float(price)):
                unpriced.append(entity)
                continue
            multiplier = valuation.fx_rate if position.currency == USD else 1.0
            weight = (
                position.quantity * float(price) * multiplier / valuation.nav
                if valuation.nav > 0
                else None
            )
        if record["source"] == SOURCE and record["realized_weight"] == weight:
            continue
        rows.append(
            {
                "entity_id": entity,
                "valid_from": record["valid_from"],
                "observed_at": clock.now(),
                "source": SOURCE,
                "market": record["market"],
                "session_id": record["session_id"],
                "target_weight": record["target_weight"],
                "realized_weight": weight,
                "revision": int(record["revision"]) + 1,
            }
        )
    if unpriced:
        logger.warning(
            "실현비중 갱신: 시세가 없어 %d종목을 건너뛰었다 — %s (세션 %s)",
            len(set(unpriced)),
            ", ".join(sorted(set(unpriced))[:20]),
            ", ".join(sorted(sessions)),
        )
    if not rows:
        return 0
    # Revision distinguishes A→B→A from a duplicate refresh of the current A.
    signature = sorted(
        (r["session_id"], r["entity_id"], r["revision"], r["realized_weight"]) for r in rows
    )
    digest = hashlib.sha256(json.dumps(signature, allow_nan=False).encode()).hexdigest()[:24]
    run_id = f"realized-fills-{digest}"
    if store.ingest_run_recorded(TABLE, run_id):
        return 0
    try:
        return store.append(TABLE, rows, ingest_run_id=run_id, source=SOURCE)
    except DuplicateIngestRun:
        return 0
