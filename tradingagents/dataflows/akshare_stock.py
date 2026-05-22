"""AKShare adapter — A-share data flows.

Returns CSV strings with the same shape as the yfinance / alpha_vantage
adapters so the vendor router can swap them interchangeably. AKShare is
fully free and the project's recommended default for A-share tickers.

A-share ticker conventions supported here:
- 6-digit numeric code:        "600519"     (auto-resolved to SH/SZ via prefix)
- yfinance-style suffix:       "600519.SS"  / "300750.SZ"
- AKShare-style prefix:        "sh600519"   / "sz300750"
- 8-digit market+code form:    "sh.600519"

All variants are normalised by ``_split_ticker`` before any AKShare call.
"""

from __future__ import annotations

import io
import logging
from datetime import datetime
from typing import Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Ticker normalisation
# ---------------------------------------------------------------------------

def _split_ticker(symbol: str) -> Tuple[str, str]:
    """Return (six_digit_code, market) where market is 'sh' or 'sz'.

    Raises ValueError for non-A-share inputs so the vendor router can fall
    back to another provider.
    """
    if not symbol:
        raise ValueError("empty symbol")
    s = symbol.strip().lower()

    # Strip common A-share suffixes / prefixes
    for suf in (".ss", ".sh", ".sz"):
        if s.endswith(suf):
            code = s[: -len(suf)]
            market = "sh" if suf in (".ss", ".sh") else "sz"
            return _ensure_6digit(code), market

    if s.startswith(("sh", "sz")):
        market = s[:2]
        code = s[2:].lstrip(".")
        return _ensure_6digit(code), market

    if s.isdigit():
        code = _ensure_6digit(s)
        # 600xxx / 601xxx / 603xxx / 605xxx / 688xxx → SH; 000/001/002/003/300 → SZ
        if code[0] in ("6", "9"):
            return code, "sh"
        return code, "sz"

    raise ValueError(f"not an A-share ticker: {symbol!r}")


def _ensure_6digit(code: str) -> str:
    code = code.zfill(6)
    if not code.isdigit() or len(code) != 6:
        raise ValueError(f"bad A-share code: {code!r}")
    return code


def _akshare_symbol(symbol: str) -> str:
    """Return the AKShare-style symbol (e.g. 'sh600519')."""
    code, market = _split_ticker(symbol)
    return f"{market}{code}"


def _is_a_share(symbol: str) -> bool:
    """Cheap test used by the vendor router to decide whether to try AKShare."""
    try:
        _split_ticker(symbol)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Core APIs
# ---------------------------------------------------------------------------

def get_stock(symbol: str, start_date: str, end_date: str) -> str:
    """Daily OHLCV for an A-share ticker as CSV.

    Tries multiple AKShare endpoints in order of preference. Eastmoney's
    ``stock_zh_a_hist`` is the richest (前复权 + 换手率 + 振幅) but it
    aggressively RSTs repeat requests from the same IP and is the most
    common point of failure. We fall through to ``stock_zh_a_daily`` (新浪)
    and finally ``stock_zh_a_hist_tx`` (腾讯) — these three are wholly
    independent services, so a network/throttle issue with one rarely
    affects the others.

    Each adapter returns the same CSV shape so downstream agents don't
    need to know which source produced the data. The vendor used is
    recorded in the file header comment for auditability.
    """
    code, market = _split_ticker(symbol)
    start_fmt = start_date.replace("-", "")
    end_fmt = end_date.replace("-", "")

    # Fallback chain — each entry is (name, fn). The first one that
    # returns a non-empty DataFrame wins.
    chain = (
        ("eastmoney", lambda: _fetch_eastmoney(code, start_fmt, end_fmt)),
        ("sina", lambda: _fetch_sina(symbol, start_date, end_date)),
        ("tencent", lambda: _fetch_tencent(symbol, start_date, end_date)),
    )

    last_err: Exception | None = None
    for source_name, fetcher in chain:
        try:
            df = fetcher()
            if df is None or df.empty:
                continue
            return _format_as_csv(df, code, market, start_date, end_date, source_name)
        except ImportError:
            raise  # akshare not installed — bail out of A-share path entirely
        except Exception as e:
            last_err = e
            logger.info("AKShare %s failed for %s: %s", source_name, symbol, e)
            continue

    # Every endpoint failed. Raise so the vendor router can fall back to
    # the next vendor (e.g. yfinance) instead of returning a "no data" CSV
    # that downstream parsers would interpret as a successful empty result.
    raise ConnectionError(
        f"all A-share data sources failed for {symbol}: {last_err}"
    )


def _fetch_eastmoney(code: str, start_fmt: str, end_fmt: str):
    """AKShare's primary A-share daily endpoint (eastmoney push2his)."""
    import akshare as ak
    return ak.stock_zh_a_hist(
        symbol=code,
        period="daily",
        start_date=start_fmt,
        end_date=end_fmt,
        adjust="qfq",
    )


def _fetch_sina(symbol: str, start_date: str, end_date: str):
    """AKShare's sina-backed A-share daily endpoint.

    Different server (``hq.sinajs.cn``), different request signature,
    different throttling rules — so an eastmoney outage / RST storm
    typically does not affect it. Sina returns un-adjusted prices by
    default, so we use ``adjust="qfq"`` for consistency with eastmoney.
    """
    import akshare as ak
    import pandas as pd

    sina_symbol = _akshare_symbol(symbol)
    df = ak.stock_zh_a_daily(symbol=sina_symbol, adjust="qfq")
    if df is None or df.empty:
        return df
    # Sina returns the full history; trim to the requested window so the
    # downstream CSV matches eastmoney's shape.
    date_col = "date" if "date" in df.columns else df.columns[0]
    df[date_col] = pd.to_datetime(df[date_col])
    start_ts = pd.to_datetime(start_date)
    end_ts = pd.to_datetime(end_date)
    df = df[(df[date_col] >= start_ts) & (df[date_col] <= end_ts)]
    return df.rename(columns={
        date_col: "日期",
        "open": "开盘",
        "high": "最高",
        "low": "最低",
        "close": "收盘",
        "volume": "成交量",
    })


def _fetch_tencent(symbol: str, start_date: str, end_date: str):
    """AKShare's tencent-backed A-share daily endpoint.

    Final fallback. Tencent's adjustment defaults to "qfq" too, so the
    output is comparable.
    """
    import akshare as ak
    import pandas as pd

    tencent_symbol = _akshare_symbol(symbol)
    # AKShare's tencent endpoint expects YYYY-MM-DD strings.
    df = ak.stock_zh_a_hist_tx(
        symbol=tencent_symbol,
        start_date=start_date,
        end_date=end_date,
        adjust="qfq",
    )
    if df is None or df.empty:
        return df
    rename_map = {
        "date": "日期",
        "open": "开盘",
        "high": "最高",
        "low": "最低",
        "close": "收盘",
        "amount": "成交量",
    }
    return df.rename(columns=rename_map)


def _format_as_csv(df, code: str, market: str, start_date: str, end_date: str,
                   source: str) -> str:
    """Normalise the AKShare-shaped frame into the project's CSV format."""
    # Normalise columns to match the yfinance CSV shape (Date,Open,High,Low,Close,Volume...)
    df = df.rename(columns={
        "日期": "Date",
        "开盘": "Open",
        "收盘": "Close",
        "最高": "High",
        "最低": "Low",
        "成交量": "Volume",
        "成交额": "Turnover",
        "振幅": "Amplitude",
        "涨跌幅": "PctChange",
        "涨跌额": "Change",
        "换手率": "TurnoverRate",
    })
    if "Date" not in df.columns:
        # First column is the date in some sina/tencent variants.
        df = df.rename(columns={df.columns[0]: "Date"})
    df = df.set_index("Date")
    for col in ("Open", "High", "Low", "Close"):
        if col in df.columns:
            df[col] = df[col].astype(float).round(2)

    out = io.StringIO()
    out.write(f"# A-share data for {market.upper()}{code} from {start_date} to {end_date}\n")
    out.write(f"# Total records: {len(df)}\n")
    out.write(f"# Source: {source} (via AKShare)\n")
    out.write(f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
    df.to_csv(out)
    return out.getvalue()


def get_indicator(symbol: str, indicator: str, curr_date: str, look_back_days: int) -> str:
    """Technical indicator window for an A-share ticker.

    AKShare doesn't expose pre-computed indicators directly, so we pull
    OHLCV and run stockstats on it locally — same library used by the
    yfinance path so the indicator vocabulary stays identical.
    """
    from datetime import timedelta
    import pandas as pd
    from stockstats import wrap
    from dateutil.relativedelta import relativedelta

    end_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    # Pull extra calendar days so stockstats has warmup data for long MAs.
    start_dt = end_dt - timedelta(days=look_back_days + 220)
    csv_str = get_stock(symbol, start_dt.strftime("%Y-%m-%d"), curr_date)

    # Re-parse the CSV we just wrote so stockstats can chew on a DataFrame.
    # Skip the leading "# ..." header lines.
    header_end = csv_str.find("\n\n")
    data_section = csv_str[header_end + 2 :] if header_end != -1 else csv_str
    df = pd.read_csv(io.StringIO(data_section), parse_dates=["Date"])
    if df.empty:
        return f"No A-share OHLCV available for {symbol}; cannot compute {indicator}"

    # stockstats expects lowercase columns and a 'date' column for indexing.
    df = df.rename(columns={c: c.lower() for c in df.columns})
    sdf = wrap(df)
    sdf[indicator]  # trigger computation

    sdf["date"] = pd.to_datetime(sdf["date"]).dt.strftime("%Y-%m-%d")
    end_str = end_dt.strftime("%Y-%m-%d")
    before_str = (end_dt - relativedelta(days=look_back_days)).strftime("%Y-%m-%d")
    window = sdf[(sdf["date"] >= before_str) & (sdf["date"] <= end_str)]

    if window.empty:
        return f"No trading days in window {before_str}..{end_str} for {symbol}"

    lines = [f"## {indicator} values for {symbol} from {before_str} to {end_str}:", ""]
    for _, row in window.iterrows():
        v = row[indicator]
        v_str = "N/A" if pd.isna(v) else str(v)
        lines.append(f"{row['date']}: {v_str}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Fundamentals
# ---------------------------------------------------------------------------

def _fetch_financial_report(code: str, statement: str):
    """Wrapper around AKShare's per-statement endpoints.

    ``statement`` ∈ {"benefit", "debt", "cash"} maps to income statement,
    balance sheet, cash flow respectively. Each returns a wide DataFrame
    with reporting periods as columns.
    """
    import akshare as ak
    func_map = {
        "benefit": ak.stock_financial_report_sina,
        "debt": ak.stock_financial_report_sina,
        "cash": ak.stock_financial_report_sina,
    }
    if statement not in func_map:
        raise ValueError(f"unknown statement: {statement}")
    # stock_financial_report_sina expects symbol like 'sh600519' and a
    # 'symbol' / 'symbol_type' style — newer AKShare API:
    #   stock_financial_report_sina(stock="sh600519", symbol="资产负债表"|"利润表"|"现金流量表")
    cn_name = {
        "benefit": "利润表",
        "debt": "资产负债表",
        "cash": "现金流量表",
    }[statement]
    return ak.stock_financial_report_sina(stock=f"sh{code}" if code[0] in "69" else f"sz{code}", symbol=cn_name)


def get_fundamentals(symbol: str, curr_date: str) -> str:
    """Company-level fundamentals snapshot (PE/PB, industry, market cap)."""
    import akshare as ak
    code, _ = _split_ticker(symbol)

    try:
        # Real-time spot quote includes PE/PB/market cap/turnover etc.
        spot = ak.stock_individual_info_em(symbol=code)
        if spot is None or spot.empty:
            return f"No fundamentals data for {symbol}"
        lines = [f"# Fundamentals for {symbol} as of {curr_date}", ""]
        for _, row in spot.iterrows():
            item = row.get("item", "")
            value = row.get("value", "")
            if item:
                lines.append(f"- **{item}**: {value}")
        return "\n".join(lines)
    except Exception as e:
        logger.warning("akshare fundamentals failed for %s: %s", symbol, e)
        return f"Failed to fetch A-share fundamentals for {symbol}: {e}"


def get_balance_sheet(symbol: str, curr_date: str) -> str:
    return _financial_statement_as_text(symbol, "debt", curr_date, "Balance Sheet")


def get_cashflow(symbol: str, curr_date: str) -> str:
    return _financial_statement_as_text(symbol, "cash", curr_date, "Cash Flow Statement")


def get_income_statement(symbol: str, curr_date: str) -> str:
    return _financial_statement_as_text(symbol, "benefit", curr_date, "Income Statement")


def _financial_statement_as_text(symbol: str, statement: str, curr_date: str, label: str) -> str:
    try:
        code, _ = _split_ticker(symbol)
        df = _fetch_financial_report(code, statement)
        if df is None or df.empty:
            return f"No {label} data for {symbol}"

        # Reports are wide-form with reporting periods as columns. Keep only
        # columns up to curr_date so we don't leak future data into the LLM.
        period_cols = [
            c for c in df.columns
            if isinstance(c, str) and len(c) == 8 and c.isdigit() and c <= curr_date.replace("-", "")
        ]
        if not period_cols:
            # If columns aren't yyyymmdd strings, fall back to whole frame.
            period_cols = list(df.columns)
        # Trim to most recent 8 reporting periods to keep prompt size sane.
        period_cols = sorted(period_cols, reverse=True)[:8]
        keep_cols = ["报告日"] if "报告日" in df.columns else []
        keep_cols += period_cols

        out = io.StringIO()
        out.write(f"# {label} for {symbol} (as of {curr_date})\n\n")
        df[keep_cols if all(c in df.columns for c in keep_cols) else df.columns].to_csv(out, index=False)
        return out.getvalue()
    except Exception as e:
        logger.warning("akshare %s failed for %s: %s", statement, symbol, e)
        return f"Failed to fetch A-share {label} for {symbol}: {e}"


# ---------------------------------------------------------------------------
# News
# ---------------------------------------------------------------------------

def get_news(symbol: str, start_date: str, end_date: str) -> str:
    """Per-ticker news from EastMoney via AKShare."""
    import akshare as ak
    try:
        code, _ = _split_ticker(symbol)
        # 个股新闻 — EastMoney
        df = ak.stock_news_em(symbol=code)
        if df is None or df.empty:
            return f"No A-share news found for {symbol}"

        # Filter to date range. AKShare's column name is usually "发布时间".
        date_col = None
        for c in ("发布时间", "datetime", "date"):
            if c in df.columns:
                date_col = c
                break
        if date_col:
            df[date_col] = df[date_col].astype(str).str[:10]
            df = df[(df[date_col] >= start_date) & (df[date_col] <= end_date)]
        if df.empty:
            return f"No A-share news for {symbol} between {start_date} and {end_date}"

        lines = [f"# A-share news for {symbol} ({start_date} → {end_date})", ""]
        for _, row in df.head(20).iterrows():
            title = row.get("新闻标题") or row.get("title") or ""
            ts = row.get(date_col) if date_col else ""
            url = row.get("新闻链接") or row.get("url") or ""
            content = row.get("新闻内容") or row.get("content") or ""
            lines.append(f"## {title}")
            lines.append(f"*{ts}* {url}")
            if content:
                lines.append(content[:500])
            lines.append("")
        return "\n".join(lines)
    except Exception as e:
        logger.warning("akshare news failed for %s: %s", symbol, e)
        return f"Failed to fetch A-share news for {symbol}: {e}"


__all__ = [
    "get_stock",
    "get_indicator",
    "get_fundamentals",
    "get_balance_sheet",
    "get_cashflow",
    "get_income_statement",
    "get_news",
    "_is_a_share",
    "_split_ticker",
]
