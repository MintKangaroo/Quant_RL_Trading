"""Policy activation settings, independent of optional ML runtime packages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from quant_rl_trading.store import Store
from quant_rl_trading.store.errors import ConfigNotFound


@dataclass(frozen=True)
class LiveParams:
    checkpoint: str
    modes: tuple[str, ...]

    @classmethod
    def from_store(cls, store: Store, *, as_of: datetime) -> LiveParams:
        try:
            checkpoint = str(store.config("allocator.rl.checkpoint", as_of=as_of) or "")
            raw = store.config("allocator.rl.modes", as_of=as_of)
        except ConfigNotFound:
            return cls(checkpoint="", modes=())
        modes = raw if isinstance(raw, list | tuple) else str(raw).split(",")
        return cls(
            checkpoint=checkpoint.strip(),
            modes=tuple(str(m).strip().lower() for m in modes if str(m).strip()),
        )

    def active_for(self, mode_code: str) -> bool:
        return bool(self.checkpoint) and mode_code.lower() in self.modes
