"""체결로 확인된 비중. 결정 당시 가격/NAV를 고정해 가격 변화와 집행을 구분한다.

초기 보유 + 같은 세션 주문의 실제 체결만 반영한다. 전송 승인과 계획 수량은
체결이 아니다. 옛 계획 기반 행은 reference_*가 없으므로 측정으로 승격하지 않는다.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from math import isfinite
from typing import TYPE_CHECKING

import pandas as pd

from quant_rl_trading.executor.orders import client_order_id

if TYPE_CHECKING:
    from quant_rl_trading.replay.clock import Clock
    from quant_rl_trading.store import Store

SOURCE = "executor-fills-v1"
TABLE = "realized_weights"


def measured(frame: pd.DataFrame) -> pd.DataFrame:
    """계획으로 계산한 구버전 행은 미측정이다."""
    if frame.empty:
        return frame
    return frame[frame["source"] == SOURCE]


def refresh(store: Store, clock: Clock, *, as_of: datetime) -> int:
    """최근 세션의 체결을 차분 없이 다시 접는다. 재시작·중복 조회에도 같은 결과다."""
    frame = measured(store.get(TABLE, as_of=as_of, lookback=7))
    if frame.empty:
        return 0
    orders = store.get("orders", as_of=as_of, lookback=7)
    trades = store.get(
        "trades", as_of=as_of, entity=frame["entity_id"].unique().tolist(), lookback=7
    )
    if orders.empty or trades.empty:
        return 0
    # 브로커의 order#누적수량과 replay의 session|entity|side를 같은 합산으로 읽는다.
    totals: dict[str, float] = {}
    for trade in trades.to_dict("records"):
        if trade["valid_from"] > pd.Timestamp(as_of):
            continue
        key = str(trade["order_id"]).split("#", 1)[0]
        signed = float(trade["quantity"]) * (1 if trade["side"] == "buy" else -1)
        totals[key] = totals.get(key, 0.0) + signed
    rows = []
    for record in frame.to_dict("records"):
        session, entity = str(record["session_id"]), str(record["entity_id"])
        selected = orders[(orders["session_id"] == session) & (orders["entity_id"] == entity)]
        keys = {
            client_order_id(session=session, entity_id=entity, slice_seq=int(seq))
            for seq in selected["slice_seq"]
        }
        keys.update(f"{session}|{entity}|{side}" for side in selected["side"].unique())
        quantity = float(record["initial_quantity"]) + sum(totals.get(key, 0.0) for key in keys)
        price, equity = float(record["reference_price"]), float(record["reference_equity"])
        weight = quantity * price / equity if equity > 0 and price > 0 else None
        if quantity < 0 or (weight is not None and not isfinite(weight)):
            raise ValueError(f"{session} {entity}: 실현 비중 대사 실패")
        old = record["realized_weight"]
        if (weight is None and pd.isna(old)) or weight == old:
            continue
        rows.append(
            {
                key: record[key]
                for key in (
                    "entity_id",
                    "valid_from",
                    "market",
                    "session_id",
                    "target_weight",
                    "initial_quantity",
                    "reference_price",
                    "reference_equity",
                )
            }
            | {
                "observed_at": clock.now(),
                "source": SOURCE,
                "revision": int(record["revision"]) + 1,
                "realized_weight": weight,
            }
        )
    if not rows:
        return 0
    digest = hashlib.sha256(
        json.dumps(
            [
                [row["session_id"], row["entity_id"], row["realized_weight"], row["revision"]]
                for row in rows
            ],
            sort_keys=True,
        ).encode()
    ).hexdigest()[:24]
    run_id = f"realized-fills-{digest}"
    if store.ingest_run_recorded(TABLE, run_id):
        return 0
    return int(store.append(TABLE, rows, ingest_run_id=run_id, source=SOURCE))
