"""Event-driven backtest engine for Studio.

The engine is a simple bar-by-bar replay:

1. Build a unified trading-day calendar from all bars in the universe.
2. For each day in the calendar, in chronological order:
   - Look up the open price for every ticker traded today (signal fill).
   - Drain signals timestamped on or before that day, route through the
     broker / portfolio.
   - Mark to market at the day's close.
   - Append a NAV snapshot.
3. After all days are processed, compute metrics over the NAV series.

Fills happen at **next day's open** if the signal was generated after
market close, or at the **same day's open** if it was generated pre-
market. This is a simplifying assumption — it avoids look-ahead bias
(you can't trade at a close you don't know yet) without modelling the
intraday fill curve. Slippage + commission via ``CostModel`` give the
rest of the realism.

Data fetching is done by the engine itself via
``route_to_vendor("get_stock_data", ...)``, so the engine doesn't depend
on web-layer code — it can be exercised from a CLI or Python script too.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Optional

import pandas as pd

from .metrics import Metrics, compute_metrics
from .portfolio import PortfolioBook
from .signals.base import Signal, SignalSource
from .slippage import CostModel, pick_cost_model

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sizing modes
# ---------------------------------------------------------------------------
# How many shares a buy signal translates to. Sell signals always flatten
# the existing position (full close) for simplicity. Three modes:
#
# - ``equal_weight``  : target = total_value / N tickers
# - ``fixed_cash``    : target = fixed_cash_per_signal (defaults to 10% of
#                       initial cash)
# - ``signal_strength``: target = base_cash * confidence (confidence ∈ 0..1)
#
# All modes are best-effort: if cash is short, the engine fills what it can.

_VALID_SIZING_MODES = {"equal_weight", "fixed_cash", "signal_strength"}


@dataclass
class BacktestConfig:
    """All inputs to a backtest run."""

    start_date: datetime
    end_date: datetime
    initial_cash: float = 1_000_000.0
    tickers: Optional[list[str]] = None       # None = derive from signals
    benchmark: Optional[str] = None           # e.g. "000300.SH" or "SPY"
    sizing_mode: str = "equal_weight"
    fixed_cash_per_signal: Optional[float] = None
    cost_model: Optional[CostModel] = None    # None = pick per ticker
    # Flatten on Sell signals only? When False (default), the engine
    # also flattens on a 'hold' that follows an open buy — i.e. exit on
    # any signal that's not a buy. When True, only explicit 'sell'
    # signals close positions.
    strict_sell_only: bool = True
    # Allow filtering signals by confidence floor. None = no filter.
    confidence_floor: Optional[float] = None


@dataclass
class BacktestResult:
    """What a finished backtest hands back to the caller."""

    config: BacktestConfig
    metrics: Metrics
    nav_curve: list[tuple[datetime, float, float]]  # (date, total, benchmark)
    trades: list[dict]                              # serialisable fill rows
    # Cumulative book at end of run, for the UI summary card.
    final_cash: float
    final_positions_value: float
    final_total: float
    # Issues encountered (missing data for a date, signal skipped, etc.).
    warnings: list[str] = field(default_factory=list)


class BacktestEngine:
    """Bar-by-bar event-driven backtest.

    Use:
        cfg = BacktestConfig(start_date=..., end_date=...)
        engine = BacktestEngine(cfg, signal_source)
        result = engine.run()
    """

    def __init__(self, config: BacktestConfig, signal_source: SignalSource):
        self.config = config
        self.signal_source = signal_source
        self.book = PortfolioBook(initial_cash=config.initial_cash)
        self._bars: dict[str, pd.DataFrame] = {}
        self._bench_bars: Optional[pd.DataFrame] = None
        self.warnings: list[str] = []
        # Per-ticker most-recent buy day, for A-share T+1 enforcement.
        # Cleared on the matching sell. Only A-share tickers are stamped.
        self._acquired_day: dict[str, "datetime.date"] = {}

    # ---------------------------------------------------------------------
    # Data
    # ---------------------------------------------------------------------

    def _fetch_bars(self, tickers: list[str]) -> None:
        """Pull OHLC for every ticker in the universe (plus benchmark).

        Cached on the engine; ``run`` calls this once. We fetch per
        ticker because the vendor router fans out A-share vs US through
        different vendors anyway, so batching wouldn't save round-trips.
        """
        from tradingagents.dataflows.interface import route_to_vendor

        start = self.config.start_date.strftime("%Y-%m-%d")
        end = self.config.end_date.strftime("%Y-%m-%d")
        for ticker in tickers:
            df = self._fetch_one(ticker, start, end)
            if df is None or df.empty:
                self.warnings.append(f"no price data for {ticker} — skipped")
                continue
            self._bars[ticker] = df

        if self.config.benchmark:
            df = self._fetch_one(self.config.benchmark, start, end)
            if df is None or df.empty:
                self.warnings.append(
                    f"no benchmark data for {self.config.benchmark} — alpha will be N/A",
                )
            else:
                self._bench_bars = df

    @staticmethod
    def _fetch_one(ticker: str, start: str, end: str) -> Optional[pd.DataFrame]:
        """Return a DataFrame indexed by date with Open/High/Low/Close columns.

        Includes a single retry on transient errors — eastmoney's push2his
        endpoint silently RSTs rapid repeat requests from the same IP,
        and a brief pause + retry recovers most of the time.
        """
        from tradingagents.dataflows.interface import route_to_vendor
        import time

        csv_str = None
        last_err: Optional[Exception] = None
        for attempt in range(2):
            try:
                csv_str = route_to_vendor("get_stock_data", ticker, start, end)
                break
            except Exception as e:
                last_err = e
                if attempt == 0:
                    time.sleep(0.6)
                else:
                    logger.warning("backtest: fetch failed for %s: %s", ticker, e)
                    return None
        if csv_str is None or not isinstance(csv_str, str):
            return None
        # Skip the comment lines that vendor adapters prepend.
        header_end = csv_str.find("\n\n")
        data_section = csv_str[header_end + 2:] if header_end != -1 else csv_str
        if "No " in csv_str[:200] and "data" in csv_str[:200]:
            return None
        try:
            df = pd.read_csv(io.StringIO(data_section))
        except Exception as e:
            logger.warning("backtest: parse failed for %s: %s", ticker, e)
            return None
        if df.empty or "Close" not in df.columns:
            return None
        date_col = None
        for cand in ("Date", "date", "Unnamed: 0"):
            if cand in df.columns:
                date_col = cand
                break
        if date_col is None:
            df = df.reset_index().rename(columns={"index": "Date"})
            date_col = "Date"
        df[date_col] = pd.to_datetime(df[date_col])
        df = df.set_index(date_col).sort_index()
        # Keep only the OHLC columns we need.
        cols = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in df.columns]
        return df[cols].copy()

    # ---------------------------------------------------------------------
    # Run loop
    # ---------------------------------------------------------------------

    def run(self) -> BacktestResult:
        """Execute the backtest and return the result.

        The flow is:
          1. Pull signals up-front (cheap — a few SQLite queries).
          2. Derive the ticker universe from signals (unless explicitly set).
          3. Fetch bars for every ticker + benchmark.
          4. Build the unified trading calendar from intersected bar dates.
          5. Walk days; for each day, process any pending signals at open
             then mark to market at close.
          6. Compute metrics over the NAV series.
        """
        # Step 1: collect all signals (sorted by source).
        raw_signals = list(self.signal_source.iter_signals(
            start_date=self.config.start_date,
            end_date=self.config.end_date,
            tickers=self.config.tickers,
        ))
        signals = self._filter_signals(raw_signals)

        # Step 2: ticker universe.
        if self.config.tickers:
            universe = list(dict.fromkeys(self.config.tickers))
        else:
            universe = list(dict.fromkeys(s.ticker for s in signals))
        if not universe:
            self.warnings.append("no tickers in universe — nothing to backtest")
            return self._empty_result()

        # Step 3: bars.
        self._fetch_bars(universe)
        if not self._bars:
            self.warnings.append("no bars fetched for any ticker — aborting")
            return self._empty_result()

        # Step 4: calendar — union of all bar dates across the universe,
        # clipped to the configured window.
        calendar = self._build_calendar()
        if not calendar:
            self.warnings.append("calendar is empty — no overlap with signal window")
            return self._empty_result()

        # Step 5: signal queue. We pop signals off the front as the
        # calendar advances; each signal fills at the *next* trading day
        # at or after its timestamp.
        signal_queue = sorted(
            signals, key=lambda s: (s.timestamp, s.ticker),
        )

        # Need to know N for equal_weight sizing.
        sized_n = len(universe)

        for day_idx, current_day in enumerate(calendar):
            # Drain signals whose timestamp is on or before this day.
            while signal_queue and signal_queue[0].timestamp <= current_day:
                sig = signal_queue.pop(0)
                self._execute_signal(sig, current_day, sized_n)

            # Mark to market at close.
            close_prices = self._close_prices_on(current_day)
            self.book.snapshot(current_day, close_prices)

        # Step 6: metrics.
        return self._finalise(calendar)

    # ---------------------------------------------------------------------
    # Execution
    # ---------------------------------------------------------------------

    def _execute_signal(self, sig: Signal, fill_day: datetime, sized_n: int) -> None:
        """Route one signal through the broker at ``fill_day``'s open price."""
        action = (sig.action or "").lower()
        if action == "hold":
            return
        if action not in ("buy", "sell"):
            self.warnings.append(f"unknown action '{sig.action}' for {sig.ticker} on {fill_day.date()}")
            return

        open_price = self._open_price(sig.ticker, fill_day)
        if open_price is None or open_price <= 0:
            self.warnings.append(
                f"no open price for {sig.ticker} on {fill_day.date()} — skipped",
            )
            return
        cost = self.config.cost_model or pick_cost_model(sig.ticker)
        # A-share heuristic — keyed off the cost-model stamp-duty rate,
        # which is non-zero exactly for A-share. Used for both lot-sizing
        # rounding and the T+1 sell restriction.
        is_a_share = pick_cost_model(sig.ticker).stamp_duty_rate > 0
        metadata = dict(sig.metadata)
        metadata.setdefault("signal_timestamp", sig.timestamp.isoformat())

        if action == "buy":
            # Already-open? Best-effort skip — we don't pyramid.
            if sig.ticker in self.book.positions:
                return
            fill_price = cost.adjust_buy_price(open_price)
            shares = self._size_buy(fill_price, sized_n, sig.confidence)
            if shares <= 0:
                return
            # A-share lot: round down to multiples of 100. Skip for non-
            # A-share where fractional shares are fine (paper account).
            if is_a_share:
                shares = int(shares // 100) * 100
                if shares <= 0:
                    return
            fee = cost.buy_fee(shares, fill_price)
            record = self.book.buy(
                timestamp=fill_day, ticker=sig.ticker, shares=shares,
                price=fill_price, fee=fee, metadata=metadata,
            )
            # Stamp the position with its acquisition day so the sell side
            # can enforce A-share T+1. Engine-only book-keeping; doesn't
            # leak into Position's persisted shape.
            if record and is_a_share:
                self._acquired_day[sig.ticker] = fill_day.date()
        else:  # sell
            pos = self.book.positions.get(sig.ticker)
            if not pos:
                return  # nothing to sell — could be a sell signal on no position
            # A-share T+1: same-day sell after a buy is rejected. In real
            # life signals on consecutive trading days fill on consecutive
            # opens (T → T+1) so T+1 is naturally satisfied; this guard
            # only kicks in when two signals share the same fill day.
            if is_a_share:
                acquired = self._acquired_day.get(sig.ticker)
                if acquired == fill_day.date():
                    self.warnings.append(
                        f"T+1: {sig.ticker} sell on {fill_day.date()} skipped "
                        f"(bought same day; A-share rule)"
                    )
                    return
            fill_price = cost.adjust_sell_price(open_price)
            fee = cost.sell_fee(pos.shares, fill_price)
            self.book.sell(
                timestamp=fill_day, ticker=sig.ticker, shares=pos.shares,
                price=fill_price, fee=fee, metadata=metadata,
            )
            # Clear the acquired-day stamp so a subsequent buy can record
            # its own. Ignored for non-A-share since we never set it.
            self._acquired_day.pop(sig.ticker, None)

    def _size_buy(
        self, fill_price: float, sized_n: int, confidence: Optional[float],
    ) -> float:
        """Translate sizing config + current cash into a share count."""
        total_value = self.book.cash + sum(
            p.shares * fill_price for p in self.book.positions.values()
        )
        if self.config.sizing_mode == "equal_weight":
            target_cash = total_value / max(1, sized_n)
        elif self.config.sizing_mode == "fixed_cash":
            target_cash = (
                self.config.fixed_cash_per_signal
                or self.config.initial_cash * 0.1
            )
        elif self.config.sizing_mode == "signal_strength":
            base = self.config.fixed_cash_per_signal or self.config.initial_cash * 0.1
            target_cash = base * (confidence or 0.5)
        else:
            target_cash = self.book.cash * 0.1
        target_cash = min(target_cash, self.book.cash)
        if target_cash <= 0:
            return 0.0
        return target_cash / fill_price

    # ---------------------------------------------------------------------
    # Calendar / prices
    # ---------------------------------------------------------------------

    def _build_calendar(self) -> list[datetime]:
        """Union of all bar dates, sorted, clipped to config window."""
        dates: set[pd.Timestamp] = set()
        for df in self._bars.values():
            dates.update(df.index.tolist())
        start = pd.Timestamp(self.config.start_date)
        end = pd.Timestamp(self.config.end_date)
        clipped = sorted(d for d in dates if start <= d <= end)
        return [d.to_pydatetime() for d in clipped]

    def _open_price(self, ticker: str, day: datetime) -> Optional[float]:
        df = self._bars.get(ticker)
        if df is None:
            return None
        ts = pd.Timestamp(day)
        if ts not in df.index:
            # Pick the next available trading day for that ticker.
            future = df.index[df.index >= ts]
            if len(future) == 0:
                return None
            ts = future[0]
        try:
            return float(df.loc[ts, "Open"])
        except (KeyError, ValueError, TypeError):
            return None

    def _close_prices_on(self, day: datetime) -> dict[str, float]:
        """Latest-close dict for every ticker in the book + universe."""
        ts = pd.Timestamp(day)
        out: dict[str, float] = {}
        for ticker, df in self._bars.items():
            # If this day isn't a trading day for this ticker, use the
            # most recent bar before or on this day.
            past = df.index[df.index <= ts]
            if len(past) == 0:
                continue
            last_ts = past[-1]
            try:
                out[ticker] = float(df.loc[last_ts, "Close"])
            except (KeyError, ValueError, TypeError):
                continue
        return out

    # ---------------------------------------------------------------------
    # Filtering
    # ---------------------------------------------------------------------

    def _filter_signals(self, signals: list[Signal]) -> list[Signal]:
        out: list[Signal] = []
        floor = self.config.confidence_floor
        for s in signals:
            if s.timestamp < self.config.start_date or s.timestamp > self.config.end_date:
                continue
            if floor is not None and s.confidence is not None and s.confidence < floor:
                continue
            out.append(s)
        return out

    # ---------------------------------------------------------------------
    # Finalisation
    # ---------------------------------------------------------------------

    def _finalise(self, calendar: list[datetime]) -> BacktestResult:
        nav_dates = [s.snapshot_date for s in self.book.nav_snapshots]
        nav_values = [s.total_value for s in self.book.nav_snapshots]
        bench_values = self._benchmark_curve(calendar)
        round_trips = self.book.closed_round_trips()
        m = compute_metrics(
            nav_dates=nav_dates,
            nav_values=nav_values,
            initial_cash=self.book.initial_cash,
            benchmark_values=bench_values,
            round_trips=round_trips,
            n_trades=len(self.book.trades),
        )
        # Pair up the curve into one zipped series for the UI.
        nav_curve: list[tuple[datetime, float, float]] = []
        for i, d in enumerate(nav_dates):
            bench = bench_values[i] if bench_values and i < len(bench_values) else None
            # If benchmark curve is shorter, repeat the last known value
            # rather than NaN — keeps Chart.js's tooltip clean.
            if bench is None and bench_values:
                bench = bench_values[-1]
            nav_curve.append((d, nav_values[i], bench if bench is not None else 0.0))
        trades_serialised = [self._serialise_trade(t) for t in self.book.trades]
        final_total = nav_values[-1] if nav_values else self.book.initial_cash
        final_positions = self.book.positions_value(self._close_prices_on(calendar[-1]))
        return BacktestResult(
            config=self.config,
            metrics=m,
            nav_curve=nav_curve,
            trades=trades_serialised,
            final_cash=self.book.cash,
            final_positions_value=final_positions,
            final_total=final_total,
            warnings=self.warnings,
        )

    def _benchmark_curve(self, calendar: list[datetime]) -> Optional[list[float]]:
        """Buy-and-hold the benchmark, scaled to the same initial cash.

        Returns the value series aligned to ``calendar``. None if no
        benchmark configured or no data.
        """
        if self._bench_bars is None or self._bench_bars.empty:
            return None
        # Scale: hold ``initial_cash / first_open`` units of the benchmark.
        first_open = float(self._bench_bars.iloc[0]["Open"])
        if first_open <= 0:
            return None
        units = self.book.initial_cash / first_open
        out: list[float] = []
        for d in calendar:
            ts = pd.Timestamp(d)
            past = self._bench_bars.index[self._bench_bars.index <= ts]
            if len(past) == 0:
                out.append(self.book.initial_cash)
                continue
            last_ts = past[-1]
            try:
                close = float(self._bench_bars.loc[last_ts, "Close"])
            except (KeyError, ValueError, TypeError):
                close = first_open
            out.append(units * close)
        return out

    @staticmethod
    def _serialise_trade(t) -> dict:
        return {
            "timestamp": t.timestamp.isoformat(),
            "ticker": t.ticker,
            "action": t.action,
            "shares": round(t.shares, 4),
            "price": round(t.price, 4),
            "fee": round(t.fee, 4),
            "realised_pnl": round(t.realised_pnl, 4),
            "metadata": t.metadata,
        }

    def _empty_result(self) -> BacktestResult:
        return BacktestResult(
            config=self.config,
            metrics=compute_metrics(
                nav_dates=[], nav_values=[], initial_cash=self.book.initial_cash,
                benchmark_values=None, round_trips=[], n_trades=0,
            ),
            nav_curve=[],
            trades=[],
            final_cash=self.book.initial_cash,
            final_positions_value=0.0,
            final_total=self.book.initial_cash,
            warnings=self.warnings,
        )
