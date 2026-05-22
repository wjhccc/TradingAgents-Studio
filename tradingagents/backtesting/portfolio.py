"""Portfolio bookkeeping: cash, positions, daily NAV snapshots.

A ``PortfolioBook`` tracks the state of one virtual account through the
backtest. The broker calls into it to execute fills; the engine calls
into it to mark-to-market at end-of-day and append a NAV snapshot.

Kept deliberately small. No order book, no margin, no shorting — the
backtest is for evaluating Agent signal quality, not microstructure.
Cash, long positions, average cost basis, that's it. If we need shorting
later, this is the place to add it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Position:
    """One open position in the portfolio.

    Cost basis is tracked as a weighted average across all buy fills, so
    a sell at a profit reduces shares without changing avg_cost (until
    you flatten and re-enter, at which point it resets on the new buy).
    """

    ticker: str
    shares: float
    avg_cost: float

    @property
    def market_value(self) -> float:
        # Placeholder — real MTM is computed in PortfolioBook with prices.
        return self.shares * self.avg_cost

    def unrealised_pnl(self, last_price: float) -> float:
        return self.shares * (last_price - self.avg_cost)


@dataclass
class FillRecord:
    """A single executed trade. Engine writes these to BacktestResult.trades."""

    timestamp: datetime
    ticker: str
    action: str  # 'buy' | 'sell'
    shares: float
    price: float
    fee: float
    realised_pnl: float = 0.0
    metadata: dict = field(default_factory=dict)


@dataclass
class NavSnapshot:
    """End-of-day account snapshot.

    Stored after every bar advance so the engine can compute returns,
    drawdown, and Sharpe against the snapshot series.
    """

    snapshot_date: datetime
    cash: float
    positions_value: float

    @property
    def total_value(self) -> float:
        return self.cash + self.positions_value


class PortfolioBook:
    """Virtual account: cash + positions + ordered trade / nav log.

    All methods that change state log to ``trades`` / ``nav_snapshots``
    so the engine can reconstruct the run after it finishes. The engine
    is the only caller; signal sources never touch the book directly.
    """

    def __init__(self, initial_cash: float):
        self.initial_cash: float = initial_cash
        self.cash: float = initial_cash
        self.positions: dict[str, Position] = {}
        self.trades: list[FillRecord] = []
        self.nav_snapshots: list[NavSnapshot] = []
        # Track realised P&L cumulatively for hit-rate / win-rate metrics.
        self._cum_realised_pnl: float = 0.0

    # ---------------------------------------------------------------------
    # Execution
    # ---------------------------------------------------------------------

    def buy(
        self,
        *,
        timestamp: datetime,
        ticker: str,
        shares: float,
        price: float,
        fee: float = 0.0,
        metadata: Optional[dict] = None,
    ) -> Optional[FillRecord]:
        """Execute a buy. Returns the FillRecord or None on insufficient cash."""
        cost = shares * price + fee
        if cost > self.cash + 1e-9:
            return None
        self.cash -= cost
        pos = self.positions.get(ticker)
        if pos:
            total_shares = pos.shares + shares
            new_cost = (pos.shares * pos.avg_cost + shares * price) / total_shares
            pos.shares = total_shares
            pos.avg_cost = new_cost
        else:
            self.positions[ticker] = Position(
                ticker=ticker, shares=shares, avg_cost=price,
            )
        record = FillRecord(
            timestamp=timestamp, ticker=ticker, action="buy",
            shares=shares, price=price, fee=fee,
            realised_pnl=0.0, metadata=dict(metadata or {}),
        )
        self.trades.append(record)
        return record

    def sell(
        self,
        *,
        timestamp: datetime,
        ticker: str,
        shares: float,
        price: float,
        fee: float = 0.0,
        metadata: Optional[dict] = None,
    ) -> Optional[FillRecord]:
        """Execute a sell. Returns the FillRecord or None on insufficient shares."""
        pos = self.positions.get(ticker)
        if not pos or pos.shares < shares - 1e-9:
            return None
        proceeds = shares * price - fee
        # Realised P&L on this slice = proceeds - cost basis of slice.
        cost_basis = shares * pos.avg_cost
        realised = proceeds - cost_basis
        self._cum_realised_pnl += realised
        self.cash += proceeds
        new_shares = pos.shares - shares
        if new_shares <= 1e-9:
            self.positions.pop(ticker, None)
        else:
            pos.shares = new_shares
            # avg_cost stays the same on a partial sell.
        record = FillRecord(
            timestamp=timestamp, ticker=ticker, action="sell",
            shares=shares, price=price, fee=fee,
            realised_pnl=realised, metadata=dict(metadata or {}),
        )
        self.trades.append(record)
        return record

    # ---------------------------------------------------------------------
    # Mark-to-market
    # ---------------------------------------------------------------------

    def positions_value(self, prices: dict[str, float]) -> float:
        """Sum of all open positions valued at the supplied prices.

        Positions without a price quote fall back to their cost basis,
        so a stale ticker doesn't zero out NAV. The engine ensures the
        price dict is fresh for every ticker traded.
        """
        total = 0.0
        for ticker, pos in self.positions.items():
            px = prices.get(ticker)
            if px is None or px <= 0:
                total += pos.shares * pos.avg_cost
            else:
                total += pos.shares * px
        return total

    def snapshot(self, snapshot_date: datetime, prices: dict[str, float]) -> NavSnapshot:
        """Append an EOD NAV snapshot. Engine calls this once per bar tick."""
        pv = self.positions_value(prices)
        snap = NavSnapshot(snapshot_date=snapshot_date, cash=self.cash, positions_value=pv)
        self.nav_snapshots.append(snap)
        return snap

    # ---------------------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------------------

    @property
    def cum_realised_pnl(self) -> float:
        return self._cum_realised_pnl

    def closed_round_trips(self) -> list[tuple[FillRecord, FillRecord, float]]:
        """Pair buy fills with subsequent sells (FIFO) per ticker.

        Returns ``[(buy, sell, realised_pnl_pct), ...]``. Used by metrics
        for hit rate / average win-loss. Not a perfectly accurate FIFO
        when partial fills are interleaved across many trades, but good
        enough for backtest summary stats — and unambiguous when each
        signal source flattens before reversing direction.
        """
        per_ticker: dict[str, list[FillRecord]] = {}
        for t in self.trades:
            per_ticker.setdefault(t.ticker, []).append(t)
        results: list[tuple[FillRecord, FillRecord, float]] = []
        for ticker, fills in per_ticker.items():
            buy_queue: list[FillRecord] = []
            for f in fills:
                if f.action == "buy":
                    buy_queue.append(f)
                else:  # sell
                    if not buy_queue:
                        continue
                    buy = buy_queue.pop(0)
                    # Use the sell's share count as the slice — caps the
                    # pairing at the actual sold quantity.
                    sold_shares = f.shares
                    if buy.shares <= sold_shares + 1e-9:
                        remaining = sold_shares - buy.shares
                        pct = (f.price - buy.price) / buy.price * 100 if buy.price else 0.0
                        results.append((buy, f, pct))
                        if remaining > 1e-9 and buy_queue:
                            # Continue eating into the next buy.
                            next_buy = buy_queue.pop(0)
                            pct2 = (f.price - next_buy.price) / next_buy.price * 100 if next_buy.price else 0.0
                            results.append((next_buy, f, pct2))
                    else:
                        pct = (f.price - buy.price) / buy.price * 100 if buy.price else 0.0
                        results.append((buy, f, pct))
                        # Put the rest of the buy back at the head for next sell.
                        buy_queue.insert(0, FillRecord(
                            timestamp=buy.timestamp, ticker=buy.ticker, action="buy",
                            shares=buy.shares - sold_shares, price=buy.price, fee=0.0,
                            realised_pnl=0.0, metadata=buy.metadata,
                        ))
        return results
