"""Reject known contaminated inputs before producing new research evidence."""

from datetime import datetime

from quant_rl_trading.store import Store


def require_causal_universe(store: Store, *, as_of: datetime, market: str) -> None:
    if market != "US":
        return
    frame = store.get(
        "universe",
        as_of=as_of,
        market=market,
        columns=["source", "is_listed", "delisted_on"],
    )
    if frame.empty:
        return
    contaminated = frame["source"].eq("ls_us_derived") & (
        ~frame["is_listed"].fillna(True).astype(bool) | frame["delisted_on"].notna()
    )
    if contaminated.any():
        raise ValueError(
            "Legacy US inferred delistings contain backdated future information. "
            "Rebuild a separate universe dataset from point-in-time evidence before "
            "training or backtesting; existing artifacts are not repaired automatically."
        )
