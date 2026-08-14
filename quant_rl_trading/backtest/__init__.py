"""백테스트 — Session 하루를 여러 날 굴린다.

명세: docs/design/backtest.md
"""

from quant_rl_trading.backtest.loop import (
    BacktestResult,
    DayResult,
    run,
    seed_capital,
    snapshot_moment,
)
from quant_rl_trading.backtest.stats import Performance, max_drawdown, summarize

__all__ = [
    "BacktestResult",
    "DayResult",
    "Performance",
    "max_drawdown",
    "run",
    "seed_capital",
    "snapshot_moment",
    "summarize",
]
