"""Capital-flow data fetchers for the 主力资金 analyst.

Pulls A-share institutional flow signals from AKShare's free endpoints:

- **Per-ticker fund flow** — main-force net inflow (主力净流入) for the
  most recent 10 trading days. Strong short-term signal for whether
  professional money is accumulating or distributing.
- **Northbound flow** — 沪深港通 net buy (foreign money via Hong Kong
  Connect). Index-level signal of foreign institutional sentiment.
- **Margin balance** — 两融余额. Rising margin balance ≈ rising leverage,
  often a momentum indicator but also a euphoria/risk warning at extremes.
- **Top stocks (龙虎榜)** — most recent day's billboard, surfaces stocks
  the seat-data (席位) shows being battled over by short-term traders.

All functions are defensive: AKShare endpoints occasionally change name
or return empty frames; the analyst still runs and reports "数据不可用"
rather than crashing the graph.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# Trim long DataFrames before injecting into the prompt — saves tokens.
_PER_TICKER_LOOKBACK_DAYS = 10
_NORTHBOUND_LOOKBACK_DAYS = 20
_MARGIN_LOOKBACK_DAYS = 20


def _try(call, label: str, default: Any = None):
    """Run an AKShare call, log warnings, return ``default`` on failure."""
    try:
        result = call()
        if result is None:
            return default
        # AKShare returns pandas frames; treat empty as failure.
        if hasattr(result, "empty") and result.empty:
            logger.info("Capital flow: %s returned empty frame", label)
            return default
        return result
    except Exception as e:
        logger.warning("Capital flow: %s failed: %s", label, e)
        return default


def _format_ticker_for_akshare(ticker: str) -> tuple[str, str]:
    """Return (code_6digit, market_prefix) for A-share AKShare calls.

    AKShare's per-ticker fund-flow function expects market="sh" / "sz"
    plus a 6-digit code. We accept "600519", "600519.SH", "SH600519",
    and "sh.600519" inputs.
    """
    s = ticker.strip().upper().replace(".SH", "").replace(".SS", "").replace(".SZ", "")
    s = s.replace("SH", "").replace("SZ", "").lstrip(".")
    code = "".join(ch for ch in s if ch.isdigit())[-6:]
    if not code or len(code) != 6:
        return ticker, ""
    # SH listings start with 6 (sh main + STAR market 688) and 9 (B-share).
    # SZ listings start with 0 (sz main), 3 (chinext), 200 (B), 4 (NEEQ).
    market = "sh" if code[0] in ("6", "9") else "sz"
    return code, market


def fetch_individual_fund_flow(ticker: str) -> str:
    """Recent 10-day main-force net flow for one ticker, formatted as markdown."""
    code, market = _format_ticker_for_akshare(ticker)
    if not market:
        return "<非 A 股代码，跳过个股资金流>"
    try:
        import akshare as ak
    except ImportError:
        return "<akshare 未安装>"

    df = _try(
        lambda: ak.stock_individual_fund_flow(stock=code, market=market),
        f"individual_fund_flow({code},{market})",
    )
    if df is None:
        return "<个股资金流数据不可用>"

    # Defensive: column names vary across AKShare versions.
    df = df.tail(_PER_TICKER_LOOKBACK_DAYS).copy()
    keep_candidates = [
        "日期", "收盘价", "涨跌幅",
        "主力净流入-净额", "主力净流入-净占比",
        "超大单净流入-净额", "大单净流入-净额",
    ]
    cols = [c for c in keep_candidates if c in df.columns]
    if not cols:
        return f"<列名未识别: {list(df.columns)[:6]}>"
    df = df[cols]
    return f"### 个股主力资金流(最近 {_PER_TICKER_LOOKBACK_DAYS} 个交易日)\n\n{df.to_markdown(index=False)}"


def fetch_northbound_flow() -> str:
    """Recent northbound (sh+sz connect) net buy series."""
    try:
        import akshare as ak
    except ImportError:
        return "<akshare 未安装>"

    df = _try(
        lambda: ak.stock_hsgt_hist_em(symbol="北向资金"),
        "stock_hsgt_hist_em(北向资金)",
    )
    if df is None:
        # Older AKShare name fallback.
        df = _try(
            lambda: ak.stock_hsgt_north_net_flow_in_em(symbol="北向资金"),
            "stock_hsgt_north_net_flow_in_em",
        )
    if df is None:
        return "<北向资金数据不可用>"

    df = df.tail(_NORTHBOUND_LOOKBACK_DAYS).copy()
    keep = [c for c in df.columns if c in ("日期", "当日资金流入", "当日成交净买额", "买入成交额", "卖出成交额", "领涨股", "领涨股涨跌幅", "上证指数", "深证指数")]
    if keep:
        df = df[keep]
    return f"### 北向资金(最近 {_NORTHBOUND_LOOKBACK_DAYS} 个交易日)\n\n{df.to_markdown(index=False)}"


def fetch_margin_balance() -> str:
    """Total A-share margin balance (融资融券余额) over the last 20 days."""
    try:
        import akshare as ak
    except ImportError:
        return "<akshare 未安装>"

    end = datetime.now()
    start = end - timedelta(days=60)  # buffer for non-trading days
    df = _try(
        lambda: ak.stock_margin_account_info(),
        "stock_margin_account_info",
    )
    if df is None:
        return "<两融数据不可用>"
    df = df.tail(_MARGIN_LOOKBACK_DAYS).copy()
    # Pick the most informative columns if present.
    candidates = ["日期", "融资融券余额", "融资余额", "融券余额", "融资买入额"]
    keep = [c for c in candidates if c in df.columns]
    if keep:
        df = df[keep]
    return f"### 融资融券余额(最近 {_MARGIN_LOOKBACK_DAYS} 个交易日)\n\n{df.to_markdown(index=False)}"


def fetch_lhb_recent() -> str:
    """Latest 龙虎榜 (top-of-the-day billboard) snapshot.

    The endpoint returns the most recent trading day's billboard. Useful as
    a snapshot — if the analysis ticker shows up, that's a strong short-term
    signal that游资 (short-term capital) is involved.
    """
    try:
        import akshare as ak
    except ImportError:
        return "<akshare 未安装>"

    today = datetime.now().strftime("%Y%m%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    df = _try(
        lambda: ak.stock_lhb_detail_em(start_date=yesterday, end_date=today),
        f"stock_lhb_detail_em({yesterday}..{today})",
    )
    if df is None:
        return "<龙虎榜数据不可用>"
    df = df.head(30).copy()
    keep_candidates = ["代码", "名称", "上榜原因", "涨跌幅", "成交额", "净买额", "买方营业部", "卖方营业部"]
    keep = [c for c in keep_candidates if c in df.columns]
    if keep:
        df = df[keep]
    return f"### 龙虎榜(最近 1 个交易日,前 30 条)\n\n{df.to_markdown(index=False)}"


def fetch_capital_flow_bundle(ticker: str) -> dict[str, str]:
    """One-shot fetch of all capital-flow signals for the analyst prompt.

    Each component is independent — a single failure doesn't cascade.
    """
    return {
        "individual": fetch_individual_fund_flow(ticker),
        "northbound": fetch_northbound_flow(),
        "margin": fetch_margin_balance(),
        "lhb": fetch_lhb_recent(),
    }
