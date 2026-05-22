"""Performance metrics for backtest results.

Implemented from scratch in plain numpy / pandas — no scipy dependency,
no quantstats. Keeps the install lean.

All return-based metrics work off the daily NAV series produced by the
engine, so they're consistent regardless of signal source.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import math


@dataclass
class Metrics:
    """Summary metrics produced after a backtest finishes."""

    # Returns
    total_return_pct: float          # cumulative return over the full window
    annualised_return_pct: float     # CAGR-style annualised
    benchmark_return_pct: Optional[float]  # buy-and-hold the benchmark
    alpha_pct: Optional[float]       # total_return - benchmark_return

    # Risk
    max_drawdown_pct: float          # worst peak-to-trough loss
    volatility_pct: float            # annualised daily-return stdev (%)
    sharpe: float                    # mean / stdev of daily returns × sqrt(252)
    sortino: float                   # like Sharpe but penalises downside only
    calmar: float                    # annualised_return / max_drawdown

    # Trade stats
    n_trades: int                    # total fills (buy + sell)
    n_round_trips: int               # paired buy/sell rounds
    win_rate_pct: float              # share of profitable round trips
    avg_win_pct: float               # mean pct return of winners
    avg_loss_pct: float              # mean pct return of losers (negative)
    profit_factor: float             # sum(wins) / |sum(losses)|


_TRADING_DAYS_PER_YEAR = 252


def _daily_returns(nav_values: list[float]) -> list[float]:
    """Simple daily returns from a NAV series. First entry is the seed."""
    rets: list[float] = []
    for i in range(1, len(nav_values)):
        prev = nav_values[i - 1]
        if prev <= 0:
            rets.append(0.0)
            continue
        rets.append((nav_values[i] - prev) / prev)
    return rets


def _max_drawdown(nav_values: list[float]) -> float:
    """Worst peak-to-trough drawdown as a positive fraction."""
    if not nav_values:
        return 0.0
    peak = nav_values[0]
    max_dd = 0.0
    for v in nav_values:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (peak - v) / peak
            if dd > max_dd:
                max_dd = dd
    return max_dd


def _annualise_return(total_ret: float, n_days: int) -> float:
    """Convert a total return + duration to an annualised CAGR.

    ``n_days`` is calendar-day duration, not trading days, to keep the
    annualisation consistent with the "1-year backtest = 365 days" common
    expectation. Returns 0 for sub-day windows.
    """
    if n_days <= 0:
        return 0.0
    years = n_days / 365.0
    if years <= 0:
        return 0.0
    base = 1 + total_ret
    if base <= 0:
        # Total loss — annualisation is undefined; cap at -100%.
        return -1.0
    return base ** (1 / years) - 1


def _sharpe(rets: list[float]) -> float:
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    stdev = math.sqrt(var)
    if stdev <= 0:
        return 0.0
    return (mean / stdev) * math.sqrt(_TRADING_DAYS_PER_YEAR)


def _sortino(rets: list[float]) -> float:
    """Sharpe variant penalising only downside deviation."""
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    downside = [r for r in rets if r < 0]
    if not downside:
        return float("inf") if mean > 0 else 0.0
    downside_var = sum(r ** 2 for r in downside) / len(rets)
    downside_dev = math.sqrt(downside_var)
    if downside_dev <= 0:
        return 0.0
    return (mean / downside_dev) * math.sqrt(_TRADING_DAYS_PER_YEAR)


def _volatility(rets: list[float]) -> float:
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(_TRADING_DAYS_PER_YEAR)


def compute_metrics(
    *,
    nav_dates: list[datetime],
    nav_values: list[float],
    initial_cash: float,
    benchmark_values: Optional[list[float]],
    round_trips: list[tuple],  # list of (buy_fill, sell_fill, pct_pnl)
    n_trades: int,
) -> Metrics:
    """Compute the full metrics summary."""
    if not nav_values:
        return _empty_metrics()

    total_ret = (nav_values[-1] - initial_cash) / initial_cash if initial_cash > 0 else 0.0
    if len(nav_dates) >= 2:
        span_days = (nav_dates[-1] - nav_dates[0]).days
    else:
        span_days = 0
    ann_ret = _annualise_return(total_ret, span_days)
    max_dd = _max_drawdown(nav_values)
    rets = _daily_returns(nav_values)
    vol = _volatility(rets)
    sharpe = _sharpe(rets)
    sortino = _sortino(rets)
    calmar = (ann_ret / max_dd) if max_dd > 0 else 0.0

    bench_ret: Optional[float] = None
    alpha: Optional[float] = None
    if benchmark_values and len(benchmark_values) >= 2 and benchmark_values[0] > 0:
        bench_ret = (benchmark_values[-1] - benchmark_values[0]) / benchmark_values[0]
        alpha = total_ret - bench_ret

    # Trade-level stats from round trips.
    n_round_trips = len(round_trips)
    pct_pnls = [pct for _, _, pct in round_trips]
    wins = [p for p in pct_pnls if p > 0]
    losses = [p for p in pct_pnls if p <= 0]
    win_rate = (len(wins) / n_round_trips * 100) if n_round_trips else 0.0
    avg_win = (sum(wins) / len(wins)) if wins else 0.0
    avg_loss = (sum(losses) / len(losses)) if losses else 0.0
    sum_wins = sum(wins)
    sum_losses_abs = abs(sum(losses))
    profit_factor = (sum_wins / sum_losses_abs) if sum_losses_abs > 0 else (
        float("inf") if sum_wins > 0 else 0.0
    )

    return Metrics(
        total_return_pct=total_ret * 100,
        annualised_return_pct=ann_ret * 100,
        benchmark_return_pct=(bench_ret * 100) if bench_ret is not None else None,
        alpha_pct=(alpha * 100) if alpha is not None else None,
        max_drawdown_pct=max_dd * 100,
        volatility_pct=vol * 100,
        sharpe=sharpe,
        sortino=sortino,
        calmar=calmar,
        n_trades=n_trades,
        n_round_trips=n_round_trips,
        win_rate_pct=win_rate,
        avg_win_pct=avg_win,
        avg_loss_pct=avg_loss,
        profit_factor=profit_factor,
    )


def _empty_metrics() -> Metrics:
    return Metrics(
        total_return_pct=0.0, annualised_return_pct=0.0,
        benchmark_return_pct=None, alpha_pct=None,
        max_drawdown_pct=0.0, volatility_pct=0.0,
        sharpe=0.0, sortino=0.0, calmar=0.0,
        n_trades=0, n_round_trips=0,
        win_rate_pct=0.0, avg_win_pct=0.0, avg_loss_pct=0.0,
        profit_factor=0.0,
    )
