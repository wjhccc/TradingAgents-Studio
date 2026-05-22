from typing import Annotated

# Import from vendor-specific modules
from .y_finance import (
    get_YFin_data_online,
    get_stock_stats_indicators_window,
    get_fundamentals as get_yfinance_fundamentals,
    get_balance_sheet as get_yfinance_balance_sheet,
    get_cashflow as get_yfinance_cashflow,
    get_income_statement as get_yfinance_income_statement,
    get_insider_transactions as get_yfinance_insider_transactions,
)
from .yfinance_news import get_news_yfinance, get_global_news_yfinance
from .alpha_vantage import (
    get_stock as get_alpha_vantage_stock,
    get_indicator as get_alpha_vantage_indicator,
    get_fundamentals as get_alpha_vantage_fundamentals,
    get_balance_sheet as get_alpha_vantage_balance_sheet,
    get_cashflow as get_alpha_vantage_cashflow,
    get_income_statement as get_alpha_vantage_income_statement,
    get_insider_transactions as get_alpha_vantage_insider_transactions,
    get_news as get_alpha_vantage_news,
    get_global_news as get_alpha_vantage_global_news,
)
from .alpha_vantage_common import AlphaVantageRateLimitError
from . import akshare_stock as _akshare
from . import tushare_stock as _tushare

# Configuration and routing logic
from .config import get_config

# Tools organized by category
TOOLS_CATEGORIES = {
    "core_stock_apis": {
        "description": "OHLCV stock price data",
        "tools": [
            "get_stock_data"
        ]
    },
    "technical_indicators": {
        "description": "Technical analysis indicators",
        "tools": [
            "get_indicators"
        ]
    },
    "fundamental_data": {
        "description": "Company fundamentals",
        "tools": [
            "get_fundamentals",
            "get_balance_sheet",
            "get_cashflow",
            "get_income_statement"
        ]
    },
    "news_data": {
        "description": "News and insider data",
        "tools": [
            "get_news",
            "get_global_news",
            "get_insider_transactions",
        ]
    }
}

VENDOR_LIST = [
    "yfinance",
    "alpha_vantage",
    "akshare",
    "tushare",
]

# Mapping of methods to their vendor-specific implementations
VENDOR_METHODS = {
    # core_stock_apis
    "get_stock_data": {
        "alpha_vantage": get_alpha_vantage_stock,
        "yfinance": get_YFin_data_online,
        "akshare": _akshare.get_stock,
        "tushare": _tushare.get_stock,
    },
    # technical_indicators
    "get_indicators": {
        "alpha_vantage": get_alpha_vantage_indicator,
        "yfinance": get_stock_stats_indicators_window,
        "akshare": _akshare.get_indicator,
    },
    # fundamental_data
    "get_fundamentals": {
        "alpha_vantage": get_alpha_vantage_fundamentals,
        "yfinance": get_yfinance_fundamentals,
        "akshare": _akshare.get_fundamentals,
        "tushare": _tushare.get_fundamentals,
    },
    "get_balance_sheet": {
        "alpha_vantage": get_alpha_vantage_balance_sheet,
        "yfinance": get_yfinance_balance_sheet,
        "akshare": _akshare.get_balance_sheet,
        "tushare": _tushare.get_balance_sheet,
    },
    "get_cashflow": {
        "alpha_vantage": get_alpha_vantage_cashflow,
        "yfinance": get_yfinance_cashflow,
        "akshare": _akshare.get_cashflow,
        "tushare": _tushare.get_cashflow,
    },
    "get_income_statement": {
        "alpha_vantage": get_alpha_vantage_income_statement,
        "yfinance": get_yfinance_income_statement,
        "akshare": _akshare.get_income_statement,
        "tushare": _tushare.get_income_statement,
    },
    # news_data
    "get_news": {
        "alpha_vantage": get_alpha_vantage_news,
        "yfinance": get_news_yfinance,
        "akshare": _akshare.get_news,
    },
    "get_global_news": {
        "yfinance": get_global_news_yfinance,
        "alpha_vantage": get_alpha_vantage_global_news,
    },
    "get_insider_transactions": {
        "alpha_vantage": get_alpha_vantage_insider_transactions,
        "yfinance": get_yfinance_insider_transactions,
    },
}

def get_category_for_method(method: str) -> str:
    """Get the category that contains the specified method."""
    for category, info in TOOLS_CATEGORIES.items():
        if method in info["tools"]:
            return category
    raise ValueError(f"Method '{method}' not found in any category")

def get_vendor(category: str, method: str = None) -> str:
    """Get the configured vendor for a data category or specific tool method.
    Tool-level configuration takes precedence over category-level.
    """
    config = get_config()

    # Check tool-level configuration first (if method provided)
    if method:
        tool_vendors = config.get("tool_vendors", {})
        if method in tool_vendors:
            return tool_vendors[method]

    # Fall back to category-level configuration
    return config.get("data_vendors", {}).get(category, "default")

def route_to_vendor(method: str, *args, **kwargs):
    """Route method calls to appropriate vendor implementation with fallback support.

    Adds A-share auto-routing: when the first positional argument looks like
    an A-share ticker (6-digit code, ``.SS``/``.SH``/``.SZ`` suffix, or
    ``sh``/``sz`` prefix), the per-config ``cn_data_vendors`` chain is
    inserted in front of whatever the user configured. This lets the same
    ``get_stock_data("600519")`` call hit AKShare first without the user
    having to flip a config switch for A-share tickers.
    """
    category = get_category_for_method(method)
    vendor_config = get_vendor(category, method)
    primary_vendors = [v.strip() for v in vendor_config.split(',')]

    if method not in VENDOR_METHODS:
        raise ValueError(f"Method '{method}' not supported")

    # Auto-prepend A-share vendors when the symbol is clearly A-share.
    symbol = args[0] if args else kwargs.get("symbol") or kwargs.get("ticker")
    if isinstance(symbol, str) and _akshare._is_a_share(symbol):
        config = get_config()
        cn_chain = config.get("cn_data_vendors", ["akshare", "tushare"])
        primary_vendors = [v for v in cn_chain if v] + [
            v for v in primary_vendors if v not in cn_chain
        ]

    # Build fallback chain: primary vendors first, then remaining available vendors
    all_available_vendors = list(VENDOR_METHODS[method].keys())
    fallback_vendors = primary_vendors.copy()
    for vendor in all_available_vendors:
        if vendor not in fallback_vendors:
            fallback_vendors.append(vendor)

    last_error: Exception | None = None
    for vendor in fallback_vendors:
        if vendor not in VENDOR_METHODS[method]:
            continue

        vendor_impl = VENDOR_METHODS[method][vendor]
        impl_func = vendor_impl[0] if isinstance(vendor_impl, list) else vendor_impl

        try:
            return impl_func(*args, **kwargs)
        except AlphaVantageRateLimitError as e:
            last_error = e
            continue  # rate limits trigger fallback
        except _tushare.TushareNotConfigured as e:
            # Tushare requires TUSHARE_TOKEN; just skip if not configured.
            last_error = e
            continue
        except (ValueError, ImportError) as e:
            # ValueError: A-share parser rejecting non-A-share symbols.
            # ImportError: optional vendor lib (akshare/tushare) not installed.
            last_error = e
            continue
        except (ConnectionError, TimeoutError, OSError) as e:
            # Network-level failures (eastmoney RST, DNS, socket timeout)
            # should fall through to the next vendor instead of bubbling
            # out as a 500. Most commonly this is the eastmoney rate
            # limiter cutting the connection — yfinance (next in line)
            # has independent infrastructure and usually still works.
            last_error = e
            import logging
            logging.getLogger(__name__).info(
                "vendor %r network error for %r — falling back: %s",
                vendor, method, e,
            )
            continue
        except Exception as e:
            # Catch-all for vendor-specific exceptions we don't know about
            # (akshare's varied internal errors, e.g.). Falling back is
            # always safer than raising — if all vendors fail we still
            # raise the final RuntimeError below.
            last_error = e
            import logging
            logging.getLogger(__name__).info(
                "vendor %r raised %s for %r — falling back: %s",
                vendor, type(e).__name__, method, e,
            )
            continue

    raise RuntimeError(
        f"No available vendor for '{method}'"
        + (f": last error was {last_error}" if last_error else "")
    )