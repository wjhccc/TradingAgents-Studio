import sqlite3
import json
import os
from datetime import datetime
from typing import Optional
from contextlib import contextmanager

_DB_PATH = os.getenv(
    "TRADINGAGENTS_WEB_DB",
    os.path.join(os.path.expanduser("~"), ".tradingagents", "web_state.db"),
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS analyses (
    id           TEXT PRIMARY KEY,
    ticker       TEXT NOT NULL,
    trade_date   TEXT NOT NULL,
    asset_type   TEXT DEFAULT 'stock',
    analysts     TEXT NOT NULL,
    config_json  TEXT NOT NULL,
    status       TEXT DEFAULT 'pending',
    signal       TEXT,
    confidence   REAL,
    final_decision TEXT,
    created_at   TEXT NOT NULL,
    completed_at TEXT,
    error_msg    TEXT
);

CREATE TABLE IF NOT EXISTS agent_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id  TEXT NOT NULL REFERENCES analyses(id),
    agent_name   TEXT NOT NULL,
    event_type   TEXT NOT NULL,
    content      TEXT,
    tokens_used  INTEGER,
    timestamp    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_reports (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id  TEXT NOT NULL REFERENCES analyses(id),
    agent_name   TEXT NOT NULL,
    report_type  TEXT NOT NULL,
    content      TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS holdings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker       TEXT NOT NULL,
    asset_type   TEXT NOT NULL DEFAULT 'stock',
    shares       REAL NOT NULL,
    cost_price   REAL NOT NULL,
    open_date    TEXT,
    notes        TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_holdings_ticker ON holdings(ticker);

CREATE TABLE IF NOT EXISTS schedules (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT,
    ticker           TEXT NOT NULL,
    asset_type       TEXT NOT NULL DEFAULT 'stock',
    schedule_type    TEXT NOT NULL,
    interval_minutes INTEGER,
    time_of_day      TEXT,
    day_of_week      INTEGER,
    analysts         TEXT NOT NULL,
    config_json      TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'active',
    fail_count       INTEGER NOT NULL DEFAULT 0,
    last_run_at      TEXT,
    last_analysis_id TEXT,
    next_run_at      TEXT NOT NULL,
    from_holding     INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_schedules_next ON schedules(next_run_at, status);
CREATE INDEX IF NOT EXISTS idx_schedules_ticker ON schedules(ticker);

CREATE TABLE IF NOT EXISTS paper_accounts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    initial_cash  REAL NOT NULL,
    cash          REAL NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_positions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id  INTEGER NOT NULL REFERENCES paper_accounts(id),
    ticker      TEXT NOT NULL,
    asset_type  TEXT NOT NULL DEFAULT 'stock',
    shares      REAL NOT NULL,
    avg_cost    REAL NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE (account_id, ticker)
);
CREATE INDEX IF NOT EXISTS idx_paper_positions_acct ON paper_positions(account_id);

CREATE TABLE IF NOT EXISTS paper_orders (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id          INTEGER NOT NULL REFERENCES paper_accounts(id),
    ticker              TEXT NOT NULL,
    asset_type          TEXT NOT NULL DEFAULT 'stock',
    action              TEXT NOT NULL,
    shares              REAL NOT NULL,
    price               REAL NOT NULL,
    source              TEXT NOT NULL DEFAULT 'manual',
    source_analysis_id  TEXT,
    notes               TEXT,
    filled_at           TEXT NOT NULL,
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_paper_orders_acct ON paper_orders(account_id, filled_at);

CREATE TABLE IF NOT EXISTS paper_nav (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id      INTEGER NOT NULL REFERENCES paper_accounts(id),
    snapshot_date   TEXT NOT NULL,
    cash            REAL NOT NULL,
    positions_value REAL NOT NULL,
    total_value     REAL NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE (account_id, snapshot_date)
);
CREATE INDEX IF NOT EXISTS idx_paper_nav_acct ON paper_nav(account_id, snapshot_date);

CREATE TABLE IF NOT EXISTS backtest_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    signal_source   TEXT NOT NULL,
    source_config   TEXT NOT NULL,
    tickers         TEXT,
    benchmark       TEXT,
    start_date      TEXT NOT NULL,
    end_date        TEXT NOT NULL,
    initial_cash    REAL NOT NULL,
    sizing_mode     TEXT NOT NULL,
    sizing_config   TEXT NOT NULL,
    confidence_floor REAL,
    status          TEXT NOT NULL DEFAULT 'pending',
    metrics_json    TEXT,
    warnings        TEXT,
    final_cash      REAL,
    final_total     REAL,
    error_msg       TEXT,
    created_at      TEXT NOT NULL,
    completed_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_backtest_runs_created ON backtest_runs(created_at DESC);

CREATE TABLE IF NOT EXISTS backtest_trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES backtest_runs(id) ON DELETE CASCADE,
    timestamp       TEXT NOT NULL,
    ticker          TEXT NOT NULL,
    action          TEXT NOT NULL,
    shares          REAL NOT NULL,
    price           REAL NOT NULL,
    fee             REAL NOT NULL,
    realised_pnl    REAL NOT NULL,
    source_analysis_id TEXT,
    metadata_json   TEXT
);
CREATE INDEX IF NOT EXISTS idx_backtest_trades_run ON backtest_trades(run_id, timestamp);

CREATE TABLE IF NOT EXISTS backtest_nav (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES backtest_runs(id) ON DELETE CASCADE,
    snapshot_date   TEXT NOT NULL,
    total_value     REAL NOT NULL,
    benchmark_value REAL
);
CREATE INDEX IF NOT EXISTS idx_backtest_nav_run ON backtest_nav(run_id, snapshot_date);

CREATE TABLE IF NOT EXISTS screen_runs (
    id              TEXT PRIMARY KEY,
    text            TEXT,
    strategy_json   TEXT,
    candidates_json TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    error_msg       TEXT,
    created_at      TEXT NOT NULL,
    completed_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_screen_runs_created ON screen_runs(created_at DESC);
"""


def _ensure_dir():
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)


@contextmanager
def get_db():
    _ensure_dir()
    conn = sqlite3.connect(_DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.executescript(_SCHEMA)


# --- Analyses CRUD ---

def create_analysis(id: str, ticker: str, trade_date: str, asset_type: str,
                    analysts: list, config: dict) -> dict:
    now = datetime.utcnow().isoformat() + "Z"
    with get_db() as conn:
        conn.execute(
            "INSERT INTO analyses (id, ticker, trade_date, asset_type, analysts, config_json, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
            (id, ticker, trade_date, asset_type, json.dumps(analysts), json.dumps(config), now),
        )
    return {"id": id, "status": "pending", "created_at": now}


def update_analysis_status(id: str, status: str, signal: Optional[str] = None,
                           confidence: Optional[float] = None,
                           final_decision: Optional[str] = None,
                           error_msg: Optional[str] = None):
    with get_db() as conn:
        fields = ["status = ?"]
        params = [status]
        if signal is not None:
            fields.append("signal = ?")
            params.append(signal)
        if confidence is not None:
            fields.append("confidence = ?")
            params.append(confidence)
        if final_decision is not None:
            fields.append("final_decision = ?")
            params.append(final_decision)
        if error_msg is not None:
            fields.append("error_msg = ?")
            params.append(error_msg)
        if status in ("complete", "failed"):
            fields.append("completed_at = ?")
            params.append(datetime.utcnow().isoformat() + "Z")
        params.append(id)
        conn.execute(f"UPDATE analyses SET {', '.join(fields)} WHERE id = ?", params)


def get_analysis(id: str) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM analyses WHERE id = ?", (id,)).fetchone()
        return dict(row) if row else None


def list_analyses(ticker: Optional[str] = None, signal: Optional[str] = None,
                  date_from: Optional[str] = None, date_to: Optional[str] = None,
                  page: int = 1, size: int = 20) -> dict:
    conditions = []
    params = []
    if ticker:
        conditions.append("ticker LIKE ?")
        params.append(f"%{ticker}%")
    if signal:
        conditions.append("signal = ?")
        params.append(signal)
    if date_from:
        conditions.append("trade_date >= ?")
        params.append(date_from)
    if date_to:
        conditions.append("trade_date <= ?")
        params.append(date_to)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    with get_db() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM analyses {where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM analyses {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params + [size, (page - 1) * size],
        ).fetchall()
    return {"total": total, "page": page, "size": size, "items": [dict(r) for r in rows]}


def delete_analysis(id: str):
    with get_db() as conn:
        conn.execute("DELETE FROM agent_events WHERE analysis_id = ?", (id,))
        conn.execute("DELETE FROM agent_reports WHERE analysis_id = ?", (id,))
        conn.execute("DELETE FROM analyses WHERE id = ?", (id,))


# --- Screen runs (选股) CRUD ---

def create_screen_run(id: str, text: str) -> dict:
    now = datetime.utcnow().isoformat() + "Z"
    with get_db() as conn:
        conn.execute(
            "INSERT INTO screen_runs (id, text, status, created_at) VALUES (?, ?, 'pending', ?)",
            (id, text, now),
        )
    return {"id": id, "text": text, "status": "pending", "created_at": now}


def update_screen_run(id: str, *, status: Optional[str] = None,
                      strategy: Optional[dict] = None,
                      candidates: Optional[list] = None,
                      error_msg: Optional[str] = None) -> Optional[dict]:
    sets, params = [], []
    if status is not None:
        sets.append("status = ?")
        params.append(status)
        if status in ("complete", "error"):
            sets.append("completed_at = ?")
            params.append(datetime.utcnow().isoformat() + "Z")
    if strategy is not None:
        sets.append("strategy_json = ?")
        params.append(json.dumps(strategy, ensure_ascii=False))
    if candidates is not None:
        sets.append("candidates_json = ?")
        params.append(json.dumps(candidates, ensure_ascii=False))
    if error_msg is not None:
        sets.append("error_msg = ?")
        params.append(error_msg)
    if not sets:
        return get_screen_run(id)
    params.append(id)
    with get_db() as conn:
        conn.execute(f"UPDATE screen_runs SET {', '.join(sets)} WHERE id = ?", params)
    return get_screen_run(id)


def get_screen_run(id: str) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM screen_runs WHERE id = ?", (id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["strategy"] = json.loads(d.pop("strategy_json") or "null")
    d["candidates"] = json.loads(d.pop("candidates_json") or "[]")
    return d


def list_screen_runs(limit: int = 50) -> list:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, text, status, created_at, completed_at FROM screen_runs "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_dashboard_stats() -> dict:
    with get_db() as conn:
        recent = conn.execute(
            "SELECT * FROM analyses ORDER BY created_at DESC LIMIT 5"
        ).fetchall()
        signal_dist = conn.execute(
            "SELECT signal, COUNT(*) as count FROM analyses WHERE signal IS NOT NULL GROUP BY signal"
        ).fetchall()
    return {
        "recent": [dict(r) for r in recent],
        "signal_distribution": {r["signal"]: r["count"] for r in signal_dist},
    }


def get_compare(tickers: list, days: int = 30) -> list:
    placeholders = ",".join("?" * len(tickers))
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT ticker, trade_date, signal, confidence, created_at FROM analyses "
            f"WHERE ticker IN ({placeholders}) AND signal IS NOT NULL "
            f"ORDER BY created_at DESC LIMIT ?",
            tickers + [days],
        ).fetchall()
    return [dict(r) for r in rows]


# --- Agent Events ---

def add_agent_event(analysis_id: str, agent_name: str, event_type: str,
                    content: Optional[str] = None, tokens_used: Optional[int] = None):
    now = datetime.utcnow().isoformat() + "Z"
    with get_db() as conn:
        conn.execute(
            "INSERT INTO agent_events (analysis_id, agent_name, event_type, content, tokens_used, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (analysis_id, agent_name, event_type, content, tokens_used, now),
        )


def get_agent_events(analysis_id: str) -> list:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM agent_events WHERE analysis_id = ? ORDER BY timestamp",
            (analysis_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# --- Agent Reports ---

def add_agent_report(analysis_id: str, agent_name: str, report_type: str, content: str):
    now = datetime.utcnow().isoformat() + "Z"
    with get_db() as conn:
        conn.execute(
            "INSERT INTO agent_reports (analysis_id, agent_name, report_type, content, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (analysis_id, agent_name, report_type, content, now),
        )


def get_agent_reports(analysis_id: str) -> list:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM agent_reports WHERE analysis_id = ? ORDER BY created_at",
            (analysis_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# --- Holdings ---

def create_holding(ticker: str, asset_type: str, shares: float, cost_price: float,
                   open_date: Optional[str] = None, notes: Optional[str] = None) -> dict:
    now = datetime.utcnow().isoformat() + "Z"
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO holdings (ticker, asset_type, shares, cost_price, open_date, notes, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (ticker, asset_type, shares, cost_price, open_date, notes, now, now),
        )
        return {"id": cur.lastrowid, "created_at": now}


def list_holdings() -> list:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM holdings ORDER BY ticker, created_at"
        ).fetchall()
    return [dict(r) for r in rows]


def get_holding(holding_id: int) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM holdings WHERE id = ?", (holding_id,)
        ).fetchone()
    return dict(row) if row else None


def update_holding(holding_id: int, **fields) -> Optional[dict]:
    if not fields:
        return get_holding(holding_id)
    cols, vals = [], []
    for k, v in fields.items():
        if v is None:
            continue
        if k not in ("shares", "cost_price", "open_date", "notes"):
            continue
        cols.append(f"{k} = ?")
        vals.append(v)
    if not cols:
        return get_holding(holding_id)
    cols.append("updated_at = ?")
    vals.append(datetime.utcnow().isoformat() + "Z")
    vals.append(holding_id)
    with get_db() as conn:
        conn.execute(
            f"UPDATE holdings SET {', '.join(cols)} WHERE id = ?", vals,
        )
    return get_holding(holding_id)


def delete_holding(holding_id: int):
    with get_db() as conn:
        conn.execute("DELETE FROM holdings WHERE id = ?", (holding_id,))


def latest_signal_for_ticker(ticker: str) -> Optional[dict]:
    """Most recent complete analysis for ``ticker``, used to annotate holdings."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, signal, confidence, trade_date, created_at "
            "FROM analyses WHERE ticker = ? AND status = 'complete' "
            "ORDER BY created_at DESC LIMIT 1",
            (ticker,),
        ).fetchone()
    return dict(row) if row else None


# --- Schedules ---

def create_schedule(
    *,
    name: Optional[str],
    ticker: str,
    asset_type: str,
    schedule_type: str,
    interval_minutes: Optional[int],
    time_of_day: Optional[str],
    day_of_week: Optional[int],
    analysts: list,
    config: dict,
    next_run_at: str,
    from_holding: bool = False,
) -> dict:
    now = datetime.utcnow().isoformat() + "Z"
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO schedules (name, ticker, asset_type, schedule_type, "
            "interval_minutes, time_of_day, day_of_week, analysts, config_json, "
            "next_run_at, from_holding, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (name, ticker, asset_type, schedule_type, interval_minutes, time_of_day,
             day_of_week, json.dumps(analysts), json.dumps(config), next_run_at,
             1 if from_holding else 0, now, now),
        )
        sid = cur.lastrowid
        row = conn.execute("SELECT * FROM schedules WHERE id = ?", (sid,)).fetchone()
    return dict(row)


def list_schedules(status: Optional[str] = None) -> list:
    with get_db() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM schedules WHERE status = ? ORDER BY next_run_at",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM schedules ORDER BY status, next_run_at"
            ).fetchall()
    return [dict(r) for r in rows]


def get_schedule(schedule_id: int) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM schedules WHERE id = ?", (schedule_id,)
        ).fetchone()
    return dict(row) if row else None


def update_schedule(schedule_id: int, **fields) -> Optional[dict]:
    """Update mutable fields on a schedule. Unknown/None keys are ignored."""
    allowed = {
        "name", "schedule_type", "interval_minutes", "time_of_day", "day_of_week",
        "analysts", "config_json", "status", "next_run_at", "last_run_at",
        "last_analysis_id", "fail_count",
    }
    cols, vals = [], []
    for k, v in fields.items():
        if k not in allowed or v is None:
            continue
        cols.append(f"{k} = ?")
        vals.append(json.dumps(v) if k in ("analysts",) and isinstance(v, list) else v)
    if not cols:
        return get_schedule(schedule_id)
    cols.append("updated_at = ?")
    vals.append(datetime.utcnow().isoformat() + "Z")
    vals.append(schedule_id)
    with get_db() as conn:
        conn.execute(
            f"UPDATE schedules SET {', '.join(cols)} WHERE id = ?", vals,
        )
    return get_schedule(schedule_id)


def delete_schedule(schedule_id: int):
    with get_db() as conn:
        conn.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))


def due_schedules(now_iso: str) -> list:
    """Return active schedules whose next_run_at is at or before ``now_iso``."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM schedules WHERE status = 'active' "
            "AND next_run_at <= ? ORDER BY next_run_at",
            (now_iso,),
        ).fetchall()
    return [dict(r) for r in rows]


def record_schedule_fire(
    schedule_id: int,
    *,
    success: bool,
    analysis_id: Optional[str],
    next_run_at: str,
    auto_disable_after: int = 3,
):
    """Update a schedule after a fire. On failure, increment fail_count and
    auto-disable when it reaches ``auto_disable_after``."""
    now = datetime.utcnow().isoformat() + "Z"
    with get_db() as conn:
        sched = conn.execute(
            "SELECT fail_count FROM schedules WHERE id = ?", (schedule_id,)
        ).fetchone()
        if not sched:
            return
        if success:
            new_fail = 0
            new_status = "active"
        else:
            new_fail = sched["fail_count"] + 1
            new_status = "disabled" if new_fail >= auto_disable_after else "active"
        conn.execute(
            "UPDATE schedules SET fail_count = ?, status = ?, last_run_at = ?, "
            "last_analysis_id = ?, next_run_at = ?, updated_at = ? WHERE id = ?",
            (new_fail, new_status, now, analysis_id, next_run_at, now, schedule_id),
        )


# --- Paper trading ---

def ensure_default_paper_account(initial_cash: float = 1_000_000.0) -> dict:
    """Create the default paper-trading account if none exists. Returns the
    sole account (creates one if the table is empty, otherwise returns the
    first one). Most users only need a single virtual account."""
    now = datetime.utcnow().isoformat() + "Z"
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM paper_accounts ORDER BY id LIMIT 1"
        ).fetchone()
        if row:
            return dict(row)
        cur = conn.execute(
            "INSERT INTO paper_accounts (name, initial_cash, cash, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("默认账户", initial_cash, initial_cash, now, now),
        )
        row = conn.execute(
            "SELECT * FROM paper_accounts WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
    return dict(row)


def get_paper_account(account_id: int) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM paper_accounts WHERE id = ?", (account_id,)
        ).fetchone()
    return dict(row) if row else None


def list_paper_accounts() -> list:
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM paper_accounts ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def reset_paper_account(account_id: int) -> Optional[dict]:
    """Wipe positions + orders + nav snapshots for an account and reset cash
    to initial_cash. Used when the user wants to start a fresh simulation."""
    now = datetime.utcnow().isoformat() + "Z"
    with get_db() as conn:
        row = conn.execute(
            "SELECT initial_cash FROM paper_accounts WHERE id = ?", (account_id,)
        ).fetchone()
        if not row:
            return None
        conn.execute("DELETE FROM paper_positions WHERE account_id = ?", (account_id,))
        conn.execute("DELETE FROM paper_orders WHERE account_id = ?", (account_id,))
        conn.execute("DELETE FROM paper_nav WHERE account_id = ?", (account_id,))
        conn.execute(
            "UPDATE paper_accounts SET cash = ?, updated_at = ? WHERE id = ?",
            (row["initial_cash"], now, account_id),
        )
    return get_paper_account(account_id)


def list_paper_positions(account_id: int) -> list:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM paper_positions WHERE account_id = ? AND shares > 0 "
            "ORDER BY ticker",
            (account_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def list_paper_orders(account_id: int, limit: int = 200) -> list:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM paper_orders WHERE account_id = ? "
            "ORDER BY filled_at DESC LIMIT ?",
            (account_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def list_paper_nav(account_id: int, limit: int = 365) -> list:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM paper_nav WHERE account_id = ? "
            "ORDER BY snapshot_date DESC LIMIT ?",
            (account_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def _is_a_share_ticker(ticker: str) -> bool:
    """Match A-share ticker shapes: 6-digit / .SH / .SS / .SZ / sh/sz prefix."""
    t = (ticker or "").upper().strip()
    if not t:
        return False
    digits = "".join(ch for ch in t if ch.isdigit())
    if len(digits) != 6:
        return False
    # Plain 6 digits (000001) or with SH/SS/SZ marker anywhere.
    if t == digits:
        return True
    return any(marker in t for marker in (".SH", ".SS", ".SZ", "SH.", "SZ."))


def _cst_date_of(iso_timestamp: str):
    """Parse an ISO timestamp (UTC or naive UTC) and return its CST calendar date.

    T+1 is defined on the **trading-day calendar**, which for A-share is
    Asia/Shanghai. We store timestamps as naive UTC ISO with a 'Z' marker;
    this helper converts back to CST so a buy at 22:00 CST followed by a
    sell at 09:00 CST next day correctly counts as 2 different days.
    """
    from datetime import timezone, timedelta as _td
    cst = timezone(_td(hours=8))
    dt = datetime.fromisoformat(iso_timestamp.rstrip("Z"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(cst).date()


def place_paper_order(
    *,
    account_id: int,
    ticker: str,
    asset_type: str,
    action: str,
    shares: float,
    price: float,
    source: str = "manual",
    source_analysis_id: Optional[str] = None,
    notes: Optional[str] = None,
) -> tuple[Optional[dict], Optional[str]]:
    """Atomically place a paper order, updating cash and positions.

    Returns ``(order_dict, None)`` on success or ``(None, error_msg)`` on a
    business-rule failure (insufficient cash for buy, insufficient shares
    for sell, A-share T+1 violation). Caller maps the error to a 400 response.

    A-share T+1: a sell on an A-share ticker is rejected if the most recent
    buy of that ticker happened on the same CST trading day. Non-A-share
    (US / HK) tickers skip this check.
    """
    now = datetime.utcnow().isoformat() + "Z"
    ticker = ticker.strip().upper()
    action = action.lower()
    if action not in ("buy", "sell"):
        return None, "action must be 'buy' or 'sell'"
    if shares <= 0 or price <= 0:
        return None, "shares and price must be > 0"

    # A-share T+1 check, runs before we touch any rows.
    if action == "sell" and _is_a_share_ticker(ticker):
        with get_db() as conn:
            row = conn.execute(
                "SELECT MAX(filled_at) FROM paper_orders "
                "WHERE account_id = ? AND ticker = ? AND action = 'buy'",
                (account_id, ticker),
            ).fetchone()
        last_buy = row[0] if row else None
        if last_buy:
            try:
                last_buy_date = _cst_date_of(last_buy)
                today_cst = _cst_date_of(now)
                if last_buy_date >= today_cst:
                    return None, (
                        f"A 股 T+1 限制:{ticker} 今日({last_buy_date})刚买入,"
                        f"按 A 股交易规则需到下一个交易日才能卖出。"
                    )
            except Exception:
                # Best-effort: if timestamp parsing fails, let it through
                # rather than blocking a legitimate sell.
                pass

    with get_db() as conn:
        acct = conn.execute(
            "SELECT * FROM paper_accounts WHERE id = ?", (account_id,)
        ).fetchone()
        if not acct:
            return None, "account not found"
        pos = conn.execute(
            "SELECT * FROM paper_positions WHERE account_id = ? AND ticker = ?",
            (account_id, ticker),
        ).fetchone()

        if action == "buy":
            cost = shares * price
            if cost > acct["cash"] + 1e-9:
                return None, f"现金不足: 需 {cost:.2f}, 可用 {acct['cash']:.2f}"
            # Update cash and position (upsert with weighted-avg cost basis).
            new_cash = acct["cash"] - cost
            conn.execute(
                "UPDATE paper_accounts SET cash = ?, updated_at = ? WHERE id = ?",
                (new_cash, now, account_id),
            )
            if pos:
                total_shares = pos["shares"] + shares
                new_cost = (pos["shares"] * pos["avg_cost"] + cost) / total_shares
                conn.execute(
                    "UPDATE paper_positions SET shares = ?, avg_cost = ?, updated_at = ? "
                    "WHERE id = ?",
                    (total_shares, new_cost, now, pos["id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO paper_positions (account_id, ticker, asset_type, "
                    "shares, avg_cost, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (account_id, ticker, asset_type, shares, price, now, now),
                )
        else:  # sell
            if not pos or pos["shares"] < shares - 1e-9:
                have = pos["shares"] if pos else 0
                return None, f"持仓不足: 需 {shares}, 持有 {have}"
            proceeds = shares * price
            new_cash = acct["cash"] + proceeds
            conn.execute(
                "UPDATE paper_accounts SET cash = ?, updated_at = ? WHERE id = ?",
                (new_cash, now, account_id),
            )
            new_shares = pos["shares"] - shares
            if new_shares <= 1e-9:
                conn.execute("DELETE FROM paper_positions WHERE id = ?", (pos["id"],))
            else:
                conn.execute(
                    "UPDATE paper_positions SET shares = ?, updated_at = ? WHERE id = ?",
                    (new_shares, now, pos["id"]),
                )

        cur = conn.execute(
            "INSERT INTO paper_orders (account_id, ticker, asset_type, action, shares, "
            "price, source, source_analysis_id, notes, filled_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (account_id, ticker, asset_type, action, shares, price, source,
             source_analysis_id, notes, now, now),
        )
        order = conn.execute(
            "SELECT * FROM paper_orders WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
    return dict(order), None


def upsert_paper_nav(
    account_id: int,
    snapshot_date: str,
    cash: float,
    positions_value: float,
):
    """Insert or replace the NAV snapshot for ``snapshot_date``."""
    now = datetime.utcnow().isoformat() + "Z"
    total = cash + positions_value
    with get_db() as conn:
        conn.execute(
            "INSERT INTO paper_nav (account_id, snapshot_date, cash, positions_value, "
            "total_value, created_at) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(account_id, snapshot_date) DO UPDATE SET "
            "cash = excluded.cash, positions_value = excluded.positions_value, "
            "total_value = excluded.total_value",
            (account_id, snapshot_date, cash, positions_value, total, now),
        )


# --- Backtesting ---

def create_backtest_run(
    *,
    name: str,
    signal_source: str,
    source_config: dict,
    tickers: Optional[list[str]],
    benchmark: Optional[str],
    start_date: str,
    end_date: str,
    initial_cash: float,
    sizing_mode: str,
    sizing_config: dict,
    confidence_floor: Optional[float],
) -> dict:
    """Insert a pending backtest run; returns the new row dict."""
    now = datetime.utcnow().isoformat() + "Z"
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO backtest_runs (name, signal_source, source_config, "
            "tickers, benchmark, start_date, end_date, initial_cash, sizing_mode, "
            "sizing_config, confidence_floor, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
            (name, signal_source, json.dumps(source_config),
             json.dumps(tickers) if tickers else None, benchmark,
             start_date, end_date, initial_cash, sizing_mode,
             json.dumps(sizing_config), confidence_floor, now),
        )
        rid = cur.lastrowid
        row = conn.execute("SELECT * FROM backtest_runs WHERE id = ?", (rid,)).fetchone()
    return dict(row)


def list_backtest_runs(limit: int = 50) -> list:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, name, signal_source, tickers, benchmark, start_date, "
            "end_date, initial_cash, status, metrics_json, final_total, "
            "created_at, completed_at FROM backtest_runs "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_backtest_run(run_id: int) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM backtest_runs WHERE id = ?", (run_id,)
        ).fetchone()
    return dict(row) if row else None


def update_backtest_status(
    run_id: int,
    *,
    status: str,
    metrics_json: Optional[str] = None,
    warnings_text: Optional[str] = None,
    final_cash: Optional[float] = None,
    final_total: Optional[float] = None,
    error_msg: Optional[str] = None,
):
    now = datetime.utcnow().isoformat() + "Z"
    fields = ["status = ?"]
    params: list = [status]
    if metrics_json is not None:
        fields.append("metrics_json = ?")
        params.append(metrics_json)
    if warnings_text is not None:
        fields.append("warnings = ?")
        params.append(warnings_text)
    if final_cash is not None:
        fields.append("final_cash = ?")
        params.append(final_cash)
    if final_total is not None:
        fields.append("final_total = ?")
        params.append(final_total)
    if error_msg is not None:
        fields.append("error_msg = ?")
        params.append(error_msg)
    if status in ("complete", "failed"):
        fields.append("completed_at = ?")
        params.append(now)
    params.append(run_id)
    with get_db() as conn:
        conn.execute(
            f"UPDATE backtest_runs SET {', '.join(fields)} WHERE id = ?", params,
        )


def insert_backtest_trades(run_id: int, trades: list[dict]):
    if not trades:
        return
    rows = [
        (
            run_id,
            t["timestamp"],
            t["ticker"],
            t["action"],
            t["shares"],
            t["price"],
            t["fee"],
            t["realised_pnl"],
            (t.get("metadata") or {}).get("analysis_id"),
            json.dumps(t.get("metadata") or {}),
        )
        for t in trades
    ]
    with get_db() as conn:
        conn.executemany(
            "INSERT INTO backtest_trades (run_id, timestamp, ticker, action, "
            "shares, price, fee, realised_pnl, source_analysis_id, metadata_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )


def insert_backtest_nav(run_id: int, nav_curve: list[tuple]):
    """``nav_curve`` is ``[(datetime, total_value, benchmark_value), ...]``."""
    if not nav_curve:
        return
    rows = [
        (run_id, d.isoformat(), total, bench)
        for d, total, bench in nav_curve
    ]
    with get_db() as conn:
        conn.executemany(
            "INSERT INTO backtest_nav (run_id, snapshot_date, total_value, "
            "benchmark_value) VALUES (?, ?, ?, ?)",
            rows,
        )


def get_backtest_nav(run_id: int) -> list:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT snapshot_date, total_value, benchmark_value "
            "FROM backtest_nav WHERE run_id = ? ORDER BY snapshot_date",
            (run_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_backtest_trades(run_id: int) -> list:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM backtest_trades WHERE run_id = ? ORDER BY timestamp",
            (run_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def delete_backtest_run(run_id: int):
    with get_db() as conn:
        # ON DELETE CASCADE on trades/nav handles the rest.
        conn.execute("DELETE FROM backtest_trades WHERE run_id = ?", (run_id,))
        conn.execute("DELETE FROM backtest_nav WHERE run_id = ?", (run_id,))
        conn.execute("DELETE FROM backtest_runs WHERE id = ?", (run_id,))
