"""Real-time-ish quote endpoints used by the K-line chart UI.

Hits the same ``route_to_vendor("get_stock_data", ...)`` chain that
holdings and paper trading use for daily bars, plus AKShare's
``stock_zh_a_hist_min_em`` for intraday 1/5/15/30/60-min bars (A-share
only — free, ~1-minute lag).

Data freshness reminder: AKShare returns the most recent intraday close
during market hours (delayed ~15s) and the day's close after hours;
yfinance is 15-min delayed on the free tier. The frontend surfaces this
caveat so users don't expect tick-level streaming.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import threading
import time
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/quote", tags=["quote"])


# AKShare's stock_zh_a_hist_min_em accepts these period strings.
_VALID_INTRADAY_PERIODS = {"1", "5", "15", "30", "60"}
# Map klinecharts-friendly interval IDs to the AKShare period string.
_INTERVAL_TO_AK_PERIOD = {
    "1min": "1", "5min": "5", "15min": "15", "30min": "30", "60min": "60",
}


# ---------------------------------------------------------------------------
# Cache + retry layer
# ---------------------------------------------------------------------------
# Why this exists: eastmoney's push2his endpoint silently drops repeated
# requests from the same IP inside a few-second window — typical symptom is
# a stream of ``Connection aborted / RemoteDisconnected`` errors when the UI
# fires multiple OHLC requests in quick succession (tab clicks, refresh
# spam, multiple components mounting).
#
# Two coordinated fixes:
#   1. In-process cache (60 s TTL) keyed by (ticker, interval, days) so
#      repeat hits within the throttle window don't touch the network at all.
#   2. Single retry with a short jittered backoff on the first failure, so
#      a one-off RST gets recovered transparently.
#
# Both are intentionally small and stateless — no Redis dependency, just a
# dict guarded by a lock. Survives across requests within one server
# process; resets on restart, which is fine for a quote cache.

_QUOTE_CACHE: dict[tuple, tuple[float, list[dict]]] = {}
_QUOTE_CACHE_TTL_SEC = 60.0
_QUOTE_CACHE_LOCK = threading.Lock()


def _cache_get(key: tuple) -> Optional[list[dict]]:
    with _QUOTE_CACHE_LOCK:
        entry = _QUOTE_CACHE.get(key)
        if not entry:
            return None
        expires_at, bars = entry
        if time.monotonic() > expires_at:
            _QUOTE_CACHE.pop(key, None)
            return None
        return bars


def _cache_put(key: tuple, bars: list[dict]) -> None:
    with _QUOTE_CACHE_LOCK:
        _QUOTE_CACHE[key] = (time.monotonic() + _QUOTE_CACHE_TTL_SEC, bars)


def _with_retry(fn, *, retries: int = 1, backoff: float = 0.6):
    """Run fn() with one retry after a short pause on transient failure.

    Eastmoney's RST on rapid repeat hits is exactly the case this guards
    against — the second attempt almost always succeeds when the cause
    is per-IP throttling rather than genuine outage.
    """
    last_exc = None
    for attempt in range(retries + 1):
        try:
            result = fn()
            if result is not None:
                return result
        except Exception as e:
            last_exc = e
        if attempt < retries:
            time.sleep(backoff)
    if last_exc is not None:
        # Re-raise so the caller's existing logging path catches it.
        raise last_exc
    return None


def _fetch_ohlc_sync(ticker: str, days: int) -> Optional[list[dict]]:
    """Pull `days` of daily bars and return a klinecharts-shaped list.

    Returns ``None`` if the vendor chain has no data at all. Otherwise:

        [
          {"timestamp": 1734739200000, "open": 100, "high": 110,
           "low": 95, "close": 105, "volume": 12345},
          ...
        ]

    Timestamps are ms-since-epoch at the **market's local midnight**, so
    klinecharts in a CST browser renders A-share bars at the correct
    date (not UTC midnight = 08:00 CST). A-share rows additionally pull a
    real-time spot quote during trading hours so today's bar reflects the
    current price, not the delayed historical close.

    Bars are chronological so the chart renders left-to-right.
    """
    from tradingagents.dataflows.interface import route_to_vendor
    from tradingagents.dataflows import akshare_stock as _akshare

    end_dt = datetime.now()
    # AKShare/yfinance return calendar days; pull extra buffer so a
    # 60-bar request doesn't come up short after weekends/holidays.
    start_dt = end_dt - timedelta(days=int(days * 1.7) + 14)
    try:
        csv_str = route_to_vendor(
            "get_stock_data", ticker,
            start_dt.strftime("%Y-%m-%d"),
            end_dt.strftime("%Y-%m-%d"),
        )
    except Exception as e:
        logger.warning("OHLC fetch failed for %s: %s", ticker, e)
        return None
    if not isinstance(csv_str, str):
        return None
    # The vendor CSV starts with comment lines; skip past the blank line
    # that separates them from the data.
    header_end = csv_str.find("\n\n")
    data_section = csv_str[header_end + 2:] if header_end != -1 else csv_str
    if "No " in csv_str[:200] and "data" in csv_str[:200]:
        return None
    try:
        df = pd.read_csv(io.StringIO(data_section))
    except Exception as e:
        logger.warning("OHLC parse failed for %s: %s", ticker, e)
        return None
    if df.empty:
        return None
    # Index column name varies: "Date" on most vendors, sometimes blank.
    date_col = None
    for cand in ("Date", "date", "Unnamed: 0"):
        if cand in df.columns:
            date_col = cand
            break
    if date_col is None:
        df = df.reset_index().rename(columns={"index": "Date"})
        date_col = "Date"
    needed = {"Open", "High", "Low", "Close"}
    if not needed.issubset(df.columns):
        return None

    # Pick a tz so the bar lands on the correct calendar date in the user's
    # browser. A-share data is keyed by Beijing dates; other markets we
    # leave naive (yfinance dates are already the local market date).
    is_a_share = _akshare._is_a_share(ticker)
    tz = "Asia/Shanghai" if is_a_share else None

    bars: list[dict] = []
    for _, row in df.tail(days).iterrows():
        raw_date = row[date_col]
        try:
            ts = pd.to_datetime(raw_date)
        except Exception:
            continue
        if tz and ts.tzinfo is None:
            ts = ts.tz_localize(tz)
        try:
            volume = float(row["Volume"]) if "Volume" in df.columns else 0.0
        except (ValueError, TypeError):
            volume = 0.0
        bars.append({
            "timestamp": int(ts.timestamp() * 1000),
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "volume": volume,
        })
    bars.sort(key=lambda b: b["timestamp"])

    # During A-share trading hours, AKShare's daily endpoint lags ~5-15 min
    # while ``stock_bid_ask_em`` is ~3 sec. Splice the live quote into the
    # rightmost bar (or append a new bar if today's row is missing).
    if is_a_share and _is_a_share_trading_now():
        spot = _fetch_a_share_spot(ticker)
        if spot:
            _merge_spot_into_today(bars, spot, tz=tz)

    return bars


def _is_a_share_trading_now(*, now: Optional[datetime] = None) -> bool:
    """A-share trading session check, evaluated in Asia/Shanghai.

    Sessions: 09:30-11:30 and 13:00-15:00 on weekdays. Public holidays
    aren't filtered out — if the spot endpoint returns stale data on a
    holiday the merge is a no-op anyway.
    """
    from datetime import timezone, timedelta as _td
    cst = timezone(_td(hours=8))
    now = now or datetime.now(cst)
    if now.tzinfo is None:
        now = now.replace(tzinfo=cst)
    else:
        now = now.astimezone(cst)
    if now.weekday() >= 5:
        return False
    hm = now.hour * 60 + now.minute
    # Slight buffer on either end — bar can have just-pre-open data.
    return (9 * 60 + 25 <= hm <= 11 * 60 + 32) or (12 * 60 + 58 <= hm <= 15 * 60 + 5)


def _fetch_a_share_spot(ticker: str) -> Optional[dict]:
    """Latest live quote for one A-share ticker via AKShare's bid/ask endpoint.

    Returns a dict with open/high/low/close (current price)/volume, or
    ``None`` if the endpoint isn't available.
    """
    try:
        import akshare as ak
    except ImportError:
        return None
    # Strip any prefix/suffix to get the 6-digit code.
    code = "".join(ch for ch in ticker if ch.isdigit())[-6:]
    if len(code) != 6:
        return None
    try:
        df = ak.stock_bid_ask_em(symbol=code)
    except Exception as e:
        logger.warning("Spot quote failed for %s: %s", ticker, e)
        return None
    if df is None or df.empty:
        return None
    # AKShare returns a 2-column long-format frame: 'item' / 'value'.
    try:
        kv = dict(zip(df["item"], df["value"]))
    except Exception:
        return None

    def _num(key: str) -> Optional[float]:
        v = kv.get(key)
        try:
            return float(v) if v is not None else None
        except (ValueError, TypeError):
            return None

    last = _num("最新")
    if last is None or last <= 0:
        return None
    return {
        "open": _num("今开") or last,
        "high": _num("最高") or last,
        "low": _num("最低") or last,
        "close": last,
        "volume": _num("总手") or 0.0,
    }


def _merge_spot_into_today(bars: list[dict], spot: dict, *, tz: Optional[str]) -> None:
    """Update the rightmost bar with live spot data, or append a new bar.

    Decides "is today's row already in ``bars``?" by comparing the local
    date of the last bar's timestamp to today (Asia/Shanghai if tz set,
    otherwise system local).
    """
    today = datetime.now().date()
    if not bars:
        # Empty history during trading is unusual; synthesize a bar from spot.
        ts_today = pd.Timestamp(today)
        if tz:
            ts_today = ts_today.tz_localize(tz)
        bars.append({
            "timestamp": int(ts_today.timestamp() * 1000),
            **{k: spot[k] for k in ("open", "high", "low", "close", "volume")},
        })
        return
    last = bars[-1]
    # pd.Timestamp(ms, unit="ms") returns a naive Timestamp; explicitly
    # localize to UTC first so tz_convert works (otherwise it raises).
    last_ts = pd.Timestamp(last["timestamp"], unit="ms", tz="UTC")
    if tz:
        last_ts = last_ts.tz_convert(tz)
    if last_ts.date() == today:
        # Same calendar day — overwrite the OHLC/volume with the more recent
        # spot values. Open stays whatever the historical row had so we
        # don't flip-flop if spot doesn't return 今开.
        last["high"] = max(float(last["high"]), spot["high"])
        last["low"] = min(float(last["low"]), spot["low"])
        last["close"] = spot["close"]
        if spot.get("volume"):
            last["volume"] = spot["volume"]
    else:
        # Today's bar is missing (AKShare hasn't published it yet) — append
        # one synthesized from spot.
        ts_today = pd.Timestamp(today)
        if tz:
            ts_today = ts_today.tz_localize(tz)
        bars.append({
            "timestamp": int(ts_today.timestamp() * 1000),
            "open": spot["open"],
            "high": spot["high"],
            "low": spot["low"],
            "close": spot["close"],
            "volume": spot["volume"],
        })


@router.get("/{ticker}/ohlc")
async def ohlc(ticker: str, days: int = 60, interval: str = "daily"):
    """Daily or intraday OHLC bars for klinecharts.

    ``interval``:
      - ``daily`` (default) — routed through ``get_stock_data``. Works for
        A-share, US, HK, anything in the vendor chain.
      - ``1min`` / ``5min`` / ``15min`` / ``30min`` / ``60min`` — **A-share
        only** via AKShare's ``stock_zh_a_hist_min_em``. ``days`` becomes
        "approximately how many recent bars" (since intraday history is
        limited to ~5 trading days on the free endpoint).
    """
    if days <= 0 or days > 500:
        raise HTTPException(status_code=400, detail="days must be 1..500")

    # Cache lookup — keyed by the request shape. Spares eastmoney's
    # throttle window when the UI fires repeat requests in quick succession.
    cache_key = (ticker.upper(), interval, days)
    cached = _cache_get(cache_key)
    if cached is not None:
        return {
            "ticker": ticker.upper(),
            "interval": interval,
            "days": days,
            "bars": cached,
            "cached": True,
        }

    loop = asyncio.get_running_loop()
    if interval == "daily":
        bars = await loop.run_in_executor(
            None,
            lambda: _with_retry(lambda: _fetch_ohlc_sync(ticker, days)),
        )
    elif interval in _INTERVAL_TO_AK_PERIOD:
        bars = await loop.run_in_executor(
            None,
            lambda: _with_retry(lambda: _fetch_intraday_sync(ticker, interval, days)),
        )
    else:
        raise HTTPException(
            status_code=400,
            detail=f"interval must be one of daily/1min/5min/15min/30min/60min",
        )

    if bars is None:
        proxy_set = any(
            os.environ.get(k) for k in
            ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy")
        )
        if interval != "daily":
            detail = (
                "无法获取分钟线数据。分钟线仅 A 股可用,且依赖 AKShare 的"
                " stock_zh_a_hist_min_em 接口。请稍候 30 秒再试(东方财富对密集"
                "请求有短时限频),或确认网络能直连 push2his.eastmoney.com。"
            )
        elif proxy_set:
            detail = (
                "无法获取行情:检测到本机配置了 HTTP 代理(Clash/V2Ray 等)。"
                "本项目会自动把国内财经域名加入 NO_PROXY,但仅在导入 dataflows 时生效。"
                "请重启后端进程让 NO_PROXY 生效;或在启动前手动设置 "
                "NO_PROXY=eastmoney.com,sina.com.cn,tushare.pro,xueqiu.com,cninfo.com.cn"
            )
        else:
            detail = (
                "无法获取行情数据。可能原因:1) 代码错误或停牌;2) 东方财富对密集"
                "请求短时限频(请稍候 30 秒再试);3) 网络问题。"
            )
        raise HTTPException(status_code=404, detail=detail)

    # Cache successful responses.
    _cache_put(cache_key, bars)
    return {
        "ticker": ticker.upper(),
        "interval": interval,
        "days": days,
        "bars": bars,
        "cached": False,
    }


def _fetch_intraday_sync(ticker: str, interval: str, days: int) -> Optional[list[dict]]:
    """A-share intraday bars via AKShare's stock_zh_a_hist_min_em.

    The endpoint requires a 6-digit code (no .SZ/.SH suffix) and a period
    string of "1"/"5"/"15"/"30"/"60". Returns the most recent few trading
    days; we tail it to roughly the requested bar count.

    Times are returned by AKShare as naive Asia/Shanghai datetimes (HH:MM
    on the date). We localize to CST so klinecharts in a CST browser
    renders bars at the correct minute. Returns None on failure (non-A-
    share ticker, akshare not installed, endpoint outage).
    """
    from tradingagents.dataflows import akshare_stock as _akshare

    if not _akshare._is_a_share(ticker):
        return None
    period = _INTERVAL_TO_AK_PERIOD.get(interval)
    if period not in _VALID_INTRADAY_PERIODS:
        return None
    try:
        import akshare as ak
    except ImportError:
        return None
    code, _ = _akshare._split_ticker(ticker)
    # The endpoint's date arguments accept "YYYY-MM-DD HH:MM:SS"; we want
    # the most recent ~5 trading days regardless of bar size — the
    # endpoint internally caps the depth, so just request a wide window.
    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=12)
    try:
        df = ak.stock_zh_a_hist_min_em(
            symbol=code,
            period=period,
            start_date=start_dt.strftime("%Y-%m-%d %H:%M:%S"),
            end_date=end_dt.strftime("%Y-%m-%d %H:%M:%S"),
            adjust="qfq",
        )
    except Exception as e:
        logger.warning("Intraday fetch failed for %s (%s): %s", ticker, interval, e)
        return None
    if df is None or df.empty:
        return None

    # Normalise column names — AKShare returns Chinese headers.
    rename = {
        "时间": "datetime",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
    }
    df = df.rename(columns=rename)
    needed = {"datetime", "open", "high", "low", "close"}
    if not needed.issubset(df.columns):
        return None

    bars: list[dict] = []
    for _, row in df.tail(days).iterrows():
        try:
            ts = pd.to_datetime(row["datetime"])
            if ts.tzinfo is None:
                ts = ts.tz_localize("Asia/Shanghai")
        except Exception:
            continue
        try:
            volume = float(row["volume"]) if "volume" in df.columns else 0.0
        except (ValueError, TypeError):
            volume = 0.0
        bars.append({
            "timestamp": int(ts.timestamp() * 1000),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": volume,
        })
    bars.sort(key=lambda b: b["timestamp"])
    return bars


@router.get("/_diagnose")
async def diagnose():
    """Diagnostic endpoint: shows what the Python process sees for proxy
    state and whether it can actually reach eastmoney directly.

    Visit ``GET /api/quote/_diagnose`` in a browser when A-share data
    fetches fail. The response tells you whether the problem is a proxy
    env var, a TUN-mode proxy intercepting traffic, or genuine connectivity.
    """
    import socket
    import urllib.request

    env_proxies = {
        k: os.environ.get(k) for k in (
            "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
            "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy",
        ) if os.environ.get(k)
    }

    # What does requests think the effective proxy is for an eastmoney URL?
    import requests
    test_url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    effective = requests.utils.get_environ_proxies(test_url)
    # Same question for sina.
    effective_sina = requests.utils.get_environ_proxies("https://hq.sinajs.cn/list=sh000001")

    # Can we resolve and reach eastmoney over the network at all?
    dns_ok, dns_msg = False, ""
    try:
        addr = socket.gethostbyname("push2his.eastmoney.com")
        dns_ok = True
        dns_msg = f"resolved to {addr}"
    except Exception as e:
        dns_msg = f"FAILED: {e!r}"

    # Direct HTTPS round-trip with NO proxy and a short timeout. This is
    # the most direct evidence: if this fails too, the issue is system-
    # level (TUN mode / firewall / no internet).
    direct_ok, direct_msg = False, ""
    try:
        # Bypass any proxy at the requests session level.
        session = requests.Session()
        session.trust_env = False  # ignore env_proxies entirely
        r = session.get(test_url, timeout=5)
        direct_ok = r.status_code < 500
        direct_msg = f"HTTP {r.status_code}, {len(r.content)} bytes"
    except Exception as e:
        direct_msg = f"FAILED: {e!r}"

    # And finally, what does AKShare itself return?
    akshare_ok, akshare_msg = False, ""
    try:
        import akshare as ak
        df = ak.stock_zh_a_hist(symbol="600519", period="daily",
                                start_date="20260515", end_date="20260522",
                                adjust="qfq")
        akshare_ok = df is not None and not df.empty
        akshare_msg = f"got {len(df) if df is not None else 0} rows"
    except Exception as e:
        akshare_msg = f"FAILED: {e!r}"

    return {
        "env_proxies": env_proxies,
        "requests_effective_proxy_for_eastmoney": effective,
        "requests_effective_proxy_for_sina": effective_sina,
        "dns_eastmoney": {"ok": dns_ok, "msg": dns_msg},
        "direct_https_eastmoney": {"ok": direct_ok, "msg": direct_msg},
        "akshare_smoke_test": {"ok": akshare_ok, "msg": akshare_msg},
        "interpretation": _interpret_diag(
            env_proxies, effective, dns_ok, direct_ok, akshare_ok,
        ),
    }


def _interpret_diag(env_proxies, effective, dns_ok, direct_ok, akshare_ok) -> str:
    if akshare_ok:
        return "AKShare 直接拉数据成功 — 一切正常。如果 UI 还报错,刷新前端再试。"
    if not dns_ok:
        return "DNS 解析失败 — 系统级网络问题(没联网 / DNS 设置异常)。与 Studio 无关。"
    if direct_ok and not akshare_ok:
        return ("直接 HTTPS 能通,但 AKShare 失败 — 多半是 AKShare/urllib3 版本不匹配 "
                "(看启动日志里的 RequestsDependencyWarning)。试试 "
                "`pip install -U akshare requests urllib3`。")
    if not direct_ok:
        if env_proxies:
            return ("HTTP_PROXY 还存在 → NO_PROXY 没追上,或者代理是 TUN 模式没走 env。"
                    "用 `unset HTTP_PROXY HTTPS_PROXY` 后重启 Studio 再测;还不行就关掉代理软件本身。")
        return ("没有 HTTP_PROXY 但直连仍失败 — 代理是 TUN/系统级模式,Python 绕不开。"
                "请在你的代理软件里把 eastmoney.com / sina.com.cn / tushare.pro 加到"
                "直连(DIRECT)规则,或临时彻底退出代理软件再试。")
    return "未知问题,把这份诊断结果发出来排查。"
