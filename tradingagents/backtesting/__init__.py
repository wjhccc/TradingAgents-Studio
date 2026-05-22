"""Backtesting engine for TradingAgents-Studio.

A small, self-contained backtest framework written from scratch for
Studio. The design borrows the **architectural ideas** of event-driven
backtesters (bar-by-bar advance, broker / portfolio separation, pluggable
signal sources, metrics computed at the end) without depending on or
copying from external frameworks like vnpy, backtrader, zipline, or
AI-Trader.

The point of this engine is to answer questions Studio can uniquely
answer:

- "If I'd followed the Agents' Buy/Sell signals over the last 6 months,
  what would my net-worth curve look like?"
- "How does that compare to holding 沪深 300?"
- "What was the Agents' hit rate? Max drawdown? Sharpe?"

Three signal sources are supported (modules under ``signals/``):

- ``from_memory_log`` — replay Studio's persisted Agent decisions (free)
- ``from_rule`` — classic rule strategies for a baseline (free)
- ``from_live_agent`` — re-run the Agent against historical snapshots
  (expensive, gated)

The engine consumes a normalised ``Signal`` stream and is signal-source
agnostic.
"""

from .engine import BacktestEngine, BacktestConfig, BacktestResult
from .portfolio import PortfolioBook, Position
from .signals.base import Signal, SignalSource

__all__ = [
    "BacktestEngine",
    "BacktestConfig",
    "BacktestResult",
    "PortfolioBook",
    "Position",
    "Signal",
    "SignalSource",
]
