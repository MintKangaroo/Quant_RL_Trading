"""One in-process attempt per order ID, including an unknown transport result."""

from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Lock

from quant_rl_trading.broker import Ack, BrokerError, RejectedOrder


@dataclass
class SubmissionGuard:
    _sent: dict[str, Ack] = field(default_factory=dict)
    _unknown: set[str] = field(default_factory=set)
    _lock: Lock = field(default_factory=Lock)

    def run(self, order_id: str, submit: Callable[[], Ack]) -> Ack:
        # The adapter already serializes/rate-limits low-frequency orders. Holding
        # this lock across I/O also prevents two initial cache misses from sending.
        with self._lock:
            if order_id in self._sent:
                return self._sent[order_id]
            if order_id in self._unknown:
                raise BrokerError(f"{order_id}: previous submission unresolved; reconcile first")
            self._unknown.add(order_id)
            try:
                ack = submit()
            except RejectedOrder:
                self._unknown.discard(order_id)
                raise
            if ack.sent:
                self._sent[order_id] = ack
            self._unknown.discard(order_id)
            return ack
