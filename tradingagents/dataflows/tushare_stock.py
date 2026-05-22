"""Tushare adapter — A-share data flows (Pro API).

Falls back to AKShare for technical indicators and news, since Tushare's
free tier doesn't expose those endpoints. Use as a paid-grade fallback
when AKShare's data quality is in doubt.

Requires ``TUSHARE_TOKEN`` env var. Without it, every call raises
``TushareNotConfigured`` which the vendor router treats as "skip this vendor".
"""

from __future__ import annotations

import io
import logging
import os
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Optional

from .akshare_stock import _split_ticker

logger = logging.getLogger(__name__)


class TushareNotConfigured(RuntimeError):
    """Raised when TUSHARE_TOKEN is missing or empty."""


@lru_cache(maxsize=1)
def _get_pro_api():
    """Return a cached Tushare Pro API client; raise if no token configured."""
    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if not token:
        raise TushareNotConfigured("TUSHARE_TOKEN not set")
    import tushare as ts  # local import: optional dependency
    ts.set_token(token)
    return ts.pro_api()


def _ts_symbol(symbol: str) -> str:
    """Convert any A-share ticker variant into Tushare's 'NNNNNN.SH/SZ' form."""
    code, market = _split_ticker(symbol)
    return f"{code}.{market.upper()}"


# ---------------------------------------------------------------------------
# OHLCV
# ---------------------------------------------------------------------------

def get_stock(symbol: str, start_date: str, end_date: str) -> str:
    """Daily OHLCV via Tushare's ``daily`` endpoint."""
    pro = _get_pro_api()
    ts_sym = _ts_symbol(symbol)
    df = pro.daily(
        ts_code=ts_sym,
        start_date=start_date.replace("-", ""),
        end_date=end_date.replace("-", ""),
    )
    if df is None or df.empty:
        return f"No Tushare data for {symbol} between {start_date} and {end_date}"

    # Tushare returns descending dates — sort ascending to match yfinance.
    df = df.sort_values("trade_date").rename(columns={
        "trade_date": "Date",
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "vol": "Volume",
        "amount": "Turnover",
        "pct_chg": "PctChange",
    })
    # Convert YYYYMMDD → YYYY-MM-DD for human readability.
    df["Date"] = df["Date"].astype(str).str.replace(
        r"(\d{4})(\d{2})(\d{2})", r"\1-\2-\3", regex=True,
    )
    df = df.set_index("Date")
    for col in ("Open", "High", "Low", "Close"):
        if col in df.columns:
            df[col] = df[col].round(2)

    out = io.StringIO()
    out.write(f"# Tushare A-share data for {ts_sym} from {start_date} to {end_date}\n")
    out.write(f"# Total records: {len(df)}\n")
    out.write(f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
    df.to_csv(out)
    return out.getvalue()


# ---------------------------------------------------------------------------
# Fundamentals — basic snapshot
# ---------------------------------------------------------------------------

def get_fundamentals(symbol: str, curr_date: str) -> str:
    """Daily basic fundamentals (PE/PB/turnover) from Tushare ``daily_basic``."""
    pro = _get_pro_api()
    ts_sym = _ts_symbol(symbol)
    target = curr_date.replace("-", "")

    df = pro.daily_basic(
        ts_code=ts_sym,
        trade_date=target,
        fields="ts_code,trade_date,pe,pe_ttm,pb,ps,dv_ratio,total_mv,circ_mv,turnover_rate",
    )
    if df is None or df.empty:
        # Try a few days back — curr_date might be a non-trading day.
        end_dt = datetime.strptime(curr_date, "%Y-%m-%d")
        start_dt = end_dt - timedelta(days=10)
        df = pro.daily_basic(
            ts_code=ts_sym,
            start_date=start_dt.strftime("%Y%m%d"),
            end_date=target,
            fields="ts_code,trade_date,pe,pe_ttm,pb,ps,dv_ratio,total_mv,circ_mv,turnover_rate",
        )
        if df is None or df.empty:
            return f"No Tushare fundamentals for {symbol} around {curr_date}"
        df = df.sort_values("trade_date", ascending=False).head(1)

    row = df.iloc[0]
    lines = [
        f"# Tushare fundamentals for {ts_sym} as of {curr_date}",
        "",
        f"- **PE**: {row.get('pe', 'N/A')}",
        f"- **PE (TTM)**: {row.get('pe_ttm', 'N/A')}",
        f"- **PB**: {row.get('pb', 'N/A')}",
        f"- **PS**: {row.get('ps', 'N/A')}",
        f"- **Dividend Yield**: {row.get('dv_ratio', 'N/A')}",
        f"- **Total Market Cap (万元)**: {row.get('total_mv', 'N/A')}",
        f"- **Circulating Market Cap (万元)**: {row.get('circ_mv', 'N/A')}",
        f"- **Turnover Rate**: {row.get('turnover_rate', 'N/A')}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Financial statements
# ---------------------------------------------------------------------------

def _fetch_statement(endpoint_name: str, symbol: str, curr_date: str, label: str) -> str:
    pro = _get_pro_api()
    ts_sym = _ts_symbol(symbol)
    end_period = curr_date.replace("-", "")
    # Pull last ~8 reports
    fn = getattr(pro, endpoint_name)
    df = fn(ts_code=ts_sym, end_date=end_period)
    if df is None or df.empty:
        return f"No Tushare {label} for {symbol} as of {curr_date}"
    df = df.sort_values("end_date", ascending=False).head(8)
    out = io.StringIO()
    out.write(f"# Tushare {label} for {ts_sym} (as of {curr_date})\n\n")
    df.to_csv(out, index=False)
    return out.getvalue()


def get_balance_sheet(symbol: str, curr_date: str) -> str:
    return _fetch_statement("balancesheet", symbol, curr_date, "Balance Sheet")


def get_cashflow(symbol: str, curr_date: str) -> str:
    return _fetch_statement("cashflow", symbol, curr_date, "Cash Flow Statement")


def get_income_statement(symbol: str, curr_date: str) -> str:
    return _fetch_statement("income", symbol, curr_date, "Income Statement")


# ---------------------------------------------------------------------------
# News (delegated to akshare)
# ---------------------------------------------------------------------------

def get_news(symbol: str, start_date: str, end_date: str) -> str:
    """Tushare's news API requires extra permission tiers; delegate to AKShare."""
    from . import akshare_stock as _ak
    return _ak.get_news(symbol, start_date, end_date)


__all__ = [
    "get_stock",
    "get_fundamentals",
    "get_balance_sheet",
    "get_cashflow",
    "get_income_statement",
    "get_news",
    "TushareNotConfigured",
]
