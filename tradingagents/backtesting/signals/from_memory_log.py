"""Signal source: replay Studio's persisted Agent decisions.

Two persistence layers are queried; the web SQLite layer is preferred
because it has structured columns (signal, confidence, trade_date) and
the analysis_id needed for drill-back. The markdown memory log
(``trading_memory.md``) is a fallback for runs created via the CLI
before the web layer existed.

The signal stream is **idempotent** — running the same backtest twice
returns the same fills, because the source's data is a frozen history.
This is intentional and makes the Agent-decision backtest cheaper than
re-running the graph against snapshots.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from .base import Signal, SignalSource

logger = logging.getLogger(__name__)


# Map the web layer's signal labels (BUY / SELL / HOLD) to the engine's
# action vocabulary (buy / sell / hold).
_SIGNAL_TO_ACTION = {
    "BUY": "buy",
    "SELL": "sell",
    "HOLD": "hold",
}


class MemoryLogSignalSource(SignalSource):
    """Reads completed analyses from the web SQLite DB as a signal stream.

    Each completed analysis yields one signal at its ``trade_date`` (the
    user-specified analysis date, not when the run finished). Buys open
    a position, sells flatten it. Holds are yielded so the engine can
    still mark NAV snapshots on those days but produce no fill.

    Filtering:
      - ``tickers`` filters by symbol.
      - The engine then re-applies date-range and confidence filters.
    """

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            # Default lives at ~/.tradingagents/web_state.db — same path
            # web/backend/database.py uses.
            db_path = str(Path.home() / ".tradingagents" / "web_state.db")
        self.db_path = db_path

    def iter_signals(
        self,
        *,
        start_date: datetime,
        end_date: datetime,
        tickers: Optional[list[str]] = None,
    ) -> Iterable[Signal]:
        if not Path(self.db_path).exists():
            logger.warning("memory-log signal source: db not found at %s", self.db_path)
            return iter([])
        # Build SQL with the optional ticker whitelist.
        where_clauses = [
            "status = 'complete'",
            "signal IS NOT NULL",
            "trade_date >= ?",
            "trade_date <= ?",
        ]
        params: list = [
            start_date.strftime("%Y-%m-%d"),
            end_date.strftime("%Y-%m-%d"),
        ]
        if tickers:
            placeholders = ",".join("?" for _ in tickers)
            where_clauses.append(f"ticker IN ({placeholders})")
            params.extend(t.upper() for t in tickers)
        sql = (
            "SELECT id, ticker, trade_date, signal, confidence, created_at "
            "FROM analyses WHERE " + " AND ".join(where_clauses) +
            " ORDER BY trade_date ASC, created_at ASC"
        )
        out: list[Signal] = []
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, params).fetchall()
            conn.close()
        except sqlite3.Error as e:
            logger.warning("memory-log signal source: DB query failed: %s", e)
            return iter([])

        for row in rows:
            signal_label = (row["signal"] or "").upper()
            action = _SIGNAL_TO_ACTION.get(signal_label)
            if not action:
                continue
            try:
                ts = datetime.strptime(row["trade_date"], "%Y-%m-%d")
            except (ValueError, TypeError):
                continue
            confidence = row["confidence"]
            try:
                confidence = float(confidence) if confidence is not None else None
            except (ValueError, TypeError):
                confidence = None
            # Normalise confidence to 0..1 if it looks like a percentage
            # (some agents emit 0-100, others 0-1).
            if confidence is not None and confidence > 1:
                confidence = max(0.0, min(1.0, confidence / 100.0))
            out.append(Signal(
                timestamp=ts,
                ticker=row["ticker"].upper(),
                action=action,
                confidence=confidence,
                metadata={
                    "analysis_id": row["id"],
                    "rating": signal_label,
                    "source": "memory_log",
                    "created_at": row["created_at"],
                },
            ))
        return iter(out)


def discover_available_tickers(db_path: Optional[str] = None) -> list[str]:
    """Return tickers that have at least one completed analysis.

    Used by the UI to populate the "filter by ticker" dropdown without
    making the user type codes from memory.
    """
    if db_path is None:
        db_path = str(Path.home() / ".tradingagents" / "web_state.db")
    if not Path(db_path).exists():
        return []
    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT DISTINCT ticker FROM analyses "
            "WHERE status = 'complete' AND signal IS NOT NULL "
            "ORDER BY ticker"
        ).fetchall()
        conn.close()
        return [r[0] for r in rows]
    except sqlite3.Error:
        return []


def discover_date_range(db_path: Optional[str] = None) -> tuple[Optional[str], Optional[str]]:
    """Min/max trade_date among completed analyses, as YYYY-MM-DD strings."""
    if db_path is None:
        db_path = str(Path.home() / ".tradingagents" / "web_state.db")
    if not Path(db_path).exists():
        return None, None
    try:
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT MIN(trade_date), MAX(trade_date) FROM analyses "
            "WHERE status = 'complete' AND signal IS NOT NULL"
        ).fetchone()
        conn.close()
        return row[0], row[1]
    except sqlite3.Error:
        return None, None
