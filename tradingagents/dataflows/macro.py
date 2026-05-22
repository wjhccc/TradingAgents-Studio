"""Macro indicators for the 宏观分析师.

Pulls a small basket of macro signals via AKShare's free endpoints:

- CPI / PPI / M2 — Chinese inflation + money supply
- PMI (manufacturing + non-manufacturing) — leading economic activity
- LPR (1Y/5Y loan prime rate) — central bank policy anchor
- USD/CNY central parity — global capital cost + cross-border flow proxy
- US 10y treasury yield via yfinance — global risk-free rate

The pattern matches capital_flow: each component is defensive, missing
data degrades to "<数据不可用>" rather than crashing the analyst.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

_RECENT_MONTHS = 6
_RECENT_DAYS = 20


def _try(call, label: str, default: Any = None):
    try:
        result = call()
        if result is None:
            return default
        if hasattr(result, "empty") and result.empty:
            logger.info("Macro: %s returned empty frame", label)
            return default
        return result
    except Exception as e:
        logger.warning("Macro: %s failed: %s", label, e)
        return default


def fetch_cpi() -> str:
    try:
        import akshare as ak
    except ImportError:
        return "<akshare 未安装>"
    df = _try(lambda: ak.macro_china_cpi(), "macro_china_cpi")
    if df is None:
        return "<CPI 数据不可用>"
    return f"### 中国 CPI(最近 {_RECENT_MONTHS} 个月)\n\n{df.tail(_RECENT_MONTHS).to_markdown(index=False)}"


def fetch_ppi() -> str:
    try:
        import akshare as ak
    except ImportError:
        return "<akshare 未安装>"
    df = _try(lambda: ak.macro_china_ppi(), "macro_china_ppi")
    if df is None:
        return "<PPI 数据不可用>"
    return f"### 中国 PPI(最近 {_RECENT_MONTHS} 个月)\n\n{df.tail(_RECENT_MONTHS).to_markdown(index=False)}"


def fetch_m2() -> str:
    try:
        import akshare as ak
    except ImportError:
        return "<akshare 未安装>"
    df = _try(lambda: ak.macro_china_money_supply(), "macro_china_money_supply")
    if df is None:
        return "<货币供应数据不可用>"
    return f"### 货币供应(M0/M1/M2 最近 {_RECENT_MONTHS} 个月)\n\n{df.tail(_RECENT_MONTHS).to_markdown(index=False)}"


def fetch_pmi() -> str:
    try:
        import akshare as ak
    except ImportError:
        return "<akshare 未安装>"
    # Manufacturing PMI
    pmi_mfg = _try(lambda: ak.macro_china_pmi(), "macro_china_pmi")
    pmi_nbs = _try(lambda: ak.macro_china_non_man_pmi(), "macro_china_non_man_pmi")
    parts: list[str] = []
    if pmi_mfg is not None:
        parts.append(f"#### 制造业 PMI(最近 {_RECENT_MONTHS} 个月)\n\n{pmi_mfg.tail(_RECENT_MONTHS).to_markdown(index=False)}")
    if pmi_nbs is not None:
        parts.append(f"#### 非制造业 PMI(最近 {_RECENT_MONTHS} 个月)\n\n{pmi_nbs.tail(_RECENT_MONTHS).to_markdown(index=False)}")
    if not parts:
        return "<PMI 数据不可用>"
    return "### PMI\n\n" + "\n\n".join(parts)


def fetch_lpr() -> str:
    try:
        import akshare as ak
    except ImportError:
        return "<akshare 未安装>"
    df = _try(lambda: ak.macro_china_lpr(), "macro_china_lpr")
    if df is None:
        return "<LPR 数据不可用>"
    return f"### LPR 利率(最近 12 期)\n\n{df.tail(12).to_markdown(index=False)}"


def fetch_usdcny() -> str:
    try:
        import akshare as ak
    except ImportError:
        return "<akshare 未安装>"
    df = _try(lambda: ak.macro_china_fx_gold(), "macro_china_fx_gold")
    if df is None:
        return "<USD/CNY 数据不可用>"
    return f"### 美元/人民币中间价(最近 {_RECENT_DAYS} 个交易日)\n\n{df.tail(_RECENT_DAYS).to_markdown(index=False)}"


def fetch_us_10y_yield() -> str:
    """US 10-year treasury yield via yfinance (^TNX). Global risk-free anchor."""
    try:
        import yfinance as yf
    except ImportError:
        return "<yfinance 未安装>"
    try:
        end = datetime.now()
        start = end - timedelta(days=45)
        df = yf.Ticker("^TNX").history(start=start, end=end)
        if df is None or df.empty:
            return "<10Y 美债收益率数据不可用>"
        # Yahoo's ^TNX is yield × 10 (i.e. 42 means 4.2%); render the raw column.
        recent = df["Close"].tail(_RECENT_DAYS)
        lines = ["### 美国 10 年期国债收益率(yfinance ^TNX, 单位需 ÷10 解读)\n"]
        lines.append("| 日期 | 收益率指数 |")
        lines.append("| --- | --- |")
        for ts, val in recent.items():
            lines.append(f"| {ts.strftime('%Y-%m-%d')} | {val:.2f} |")
        return "\n".join(lines)
    except Exception as e:
        logger.warning("Macro: us_10y_yield failed: %s", e)
        return "<10Y 美债收益率数据不可用>"


def fetch_macro_bundle() -> dict[str, str]:
    """One-shot fetch of all macro signals for the analyst prompt."""
    return {
        "cpi": fetch_cpi(),
        "ppi": fetch_ppi(),
        "m2": fetch_m2(),
        "pmi": fetch_pmi(),
        "lpr": fetch_lpr(),
        "usdcny": fetch_usdcny(),
        "us_10y": fetch_us_10y_yield(),
    }
