"""Signal source abstractions for the backtest engine.

A signal source emits an ordered stream of ``Signal`` objects keyed by
``timestamp``. The engine consumes them in chronological order, looks up
the corresponding bar price from its OHLC data, and routes the signal
through the broker / portfolio.

This keeps the engine itself signal-source-agnostic: we can plug in
Agent historical decisions, classical rule strategies, or live Agent
re-runs without touching the engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Optional


@dataclass(frozen=True)
class Signal:
    """A single trading signal emitted by a signal source.

    Attributes
    ----------
    timestamp : datetime
        When the signal was generated. The engine matches this against
        the bar series and fills at that bar's open (with optional
        slippage applied by the broker).
    ticker : str
        Instrument identifier (e.g. "600519", "AAPL").
    action : str
        One of ``buy``, ``sell``, ``hold``. ``hold`` is a no-op but
        included so signal sources can emit them for completeness
        (helps if you want the engine to mark NAV snapshots on signal
        days, even on hold days).
    confidence : float | None
        Optional 0..1 confidence score. Used by sizing modes that scale
        position size by signal strength.
    weight : float | None
        Optional 0..1 weight (e.g. portfolio target weight after this
        signal). Mutually exclusive with confidence on most sizing
        modes.
    metadata : dict
        Source-specific extras — e.g. ``{"analysis_id": "...",
        "rating": "Buy"}`` for memory-log signals. Surfaced in
        ``BacktestResult.trades`` so users can drill back into the
        decision that produced a trade.
    """

    timestamp: datetime
    ticker: str
    action: str
    confidence: Optional[float] = None
    weight: Optional[float] = None
    metadata: dict = field(default_factory=dict)


class SignalSource:
    """Base class. Concrete sources subclass and implement ``iter_signals``."""

    def iter_signals(
        self,
        *,
        start_date: datetime,
        end_date: datetime,
        tickers: Optional[list[str]] = None,
    ) -> Iterable[Signal]:
        """Yield ``Signal`` objects in chronological order.

        Sources MUST yield signals sorted by timestamp ascending. The
        engine relies on this to advance its clock monotonically without
        re-sorting after each tick.

        Parameters
        ----------
        start_date / end_date
            Inclusive bounds; signals outside this window must be
            filtered by the source.
        tickers
            Optional whitelist. ``None`` means "all available tickers".
        """
        raise NotImplementedError
