"""Refresh execution feedback from the fill ledger and the accounting valuation."""

from __future__ import annotations

import hashlib
import json
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
    valuation = snapshot.take(store, clock, as_of=as_of, book=book).valuation
    prices = snapshot.last_prices(store, as_of=as_of, entities=sorted(book.positions))
    rows = []
    for record in frame.to_dict(orient="records"):
        entity = str(record["entity_id"])
        position = book.positions.get(entity)
        weight = 0.0
        if position is not None and position.quantity:
            multiplier = valuation.fx_rate if position.currency == USD else 1.0
            weight = (
                position.quantity * prices[entity] * multiplier / valuation.nav
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
