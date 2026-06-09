"""Paper-trading REST endpoints.

A single virtual account with cash + positions + order history + daily
NAV snapshots. Orders fill instantly at the supplied price (or latest
close), no commission or slippage modelling — the point is to track what
would have happened if you'd followed the analyses, not to simulate
microstructure.

The "from-decision" endpoint reads a completed analysis's decision card
markdown and creates an order from its Action + Entry Price fields. This
closes the loop between analysis → trade and lets the user see whether
the agents would have made money over time.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import threading
import time
import urllib.request
from datetime import datetime, timedelta
from typing import Any, Optional

import pandas as pd
from fastapi import APIRouter, HTTPException

from .. import database as db
from ..models import PaperOrderRequest, PaperOrderFromDecision, PaperAccountReset

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/paper", tags=["paper"])


# In-process cache for spot prices. Same rationale as quote.py: when the
# Paper / Holdings pages load they fetch quotes for every position, and
# the same ticker can be hit twice within a few seconds (once for
# positions, once for holdings, once for orders). Eastmoney drops the
# 2nd request with RemoteDisconnected. A 20-second TTL keeps intraday prices
# fresh enough that the Paper page's auto-refresh visibly moves P&L, while
# still collapsing the burst of per-row quote requests one page render fires.
# (The live single-quote endpoints we hit aren't rate-limited, so a short TTL
# is safe — see _fetch_spot_price.)
_PRICE_CACHE: dict[str, tuple[float, Optional[float]]] = {}
_PRICE_CACHE_TTL_SEC = 20.0
_PRICE_CACHE_LOCK = threading.Lock()

# Hard ceiling on the priced /positions path. When every upstream A-share feed
# is down (eastmoney RST → sina → tencent → yahoo all fail+retry per ticker),
# decorating N positions can take tens of seconds. We'd rather return whatever
# quotes came back within the budget and leave the rest null (frontend shows
# "—") than make the price-refresh spinner hang. The table is already painted
# by the with_prices=false call, so this only bounds the enrichment step.
_PRICE_PATH_BUDGET_SEC = 8.0


def _price_cache_get(ticker: str) -> tuple[bool, Optional[float]]:
    """Returns (hit, price). ``hit=False`` means caller should fetch."""
    with _PRICE_CACHE_LOCK:
        entry = _PRICE_CACHE.get(ticker.upper())
        if not entry:
            return False, None
        expires_at, price = entry
        if time.monotonic() > expires_at:
            _PRICE_CACHE.pop(ticker.upper(), None)
            return False, None
        return True, price


def _price_cache_put(ticker: str, price: Optional[float]) -> None:
    with _PRICE_CACHE_LOCK:
        _PRICE_CACHE[ticker.upper()] = (time.monotonic() + _PRICE_CACHE_TTL_SEC, price)


# --- A-share code → name map -------------------------------------------------
# The whole A-share name list comes from one lightweight AKShare call
# (``stock_info_a_code_name``: ~5500 rows of code+name, no quotes), cached for
# hours since listings rename rarely. Positions/orders only ever need a dict
# lookup off this map, so we never fan out per-ticker name fetches.
_NAME_MAP: dict[str, str] = {}
_NAME_MAP_EXPIRES = 0.0
_NAME_MAP_LOCK = threading.Lock()
_NAME_MAP_TTL_SEC = 6 * 3600.0


def _name_map() -> dict[str, str]:
    """Cached ``{6-digit code: name}`` for the whole A-share market.

    Best-effort: on fetch failure returns whatever is cached (possibly empty)
    so the name column just degrades to blank rather than failing the page.
    """
    global _NAME_MAP_EXPIRES
    now = time.monotonic()
    with _NAME_MAP_LOCK:
        if _NAME_MAP and now < _NAME_MAP_EXPIRES:
            return _NAME_MAP
    try:
        import tradingagents.dataflows  # noqa: F401 — NO_PROXY bootstrap
        import akshare as ak
        df = ak.stock_info_a_code_name()
        fresh = {str(c).zfill(6): str(n) for c, n in zip(df["code"], df["name"])}
    except Exception as e:  # noqa: BLE001 — name lookup is non-essential
        logger.warning("stock name map fetch failed (names degrade to blank): %s", e)
        fresh = {}
    with _NAME_MAP_LOCK:
        if fresh:
            _NAME_MAP.clear()
            _NAME_MAP.update(fresh)
            _NAME_MAP_EXPIRES = now + _NAME_MAP_TTL_SEC
        return _NAME_MAP


def _name_map_cached_only() -> dict[str, str]:
    """Return the name map ONLY if already cached — never fetches.

    Used by the instant (``with_prices=false``) path so a cold name-map cache
    can't block the fast first paint on a slow upstream. Names then fill in on
    the subsequent priced fetch, which does warm the map.
    """
    with _NAME_MAP_LOCK:
        return dict(_NAME_MAP) if _NAME_MAP else {}


def _resolve_name(ticker: str) -> Optional[str]:
    """Best-effort display name for a ticker; None for non-A-share / unknown."""
    digits = re.sub(r"\D", "", ticker or "")
    if len(digits) >= 6:
        return _name_map().get(digits[-6:])
    return None


# --- helpers ---

_ACTION_RE = re.compile(r"\*\*Action\*\*\s*:\s*([A-Za-z]+)", re.IGNORECASE)
_ENTRY_RE = re.compile(r"\*\*Entry Price\*\*\s*:\s*([0-9.,]+)", re.IGNORECASE)
_TARGET_RE = re.compile(r"\*\*Target Price\*\*\s*:\s*([0-9.,]+)", re.IGNORECASE)
_STOP_RE = re.compile(r"\*\*Stop Loss\*\*\s*:\s*([0-9.,]+)", re.IGNORECASE)
_RATING_RE = re.compile(r"\*\*Rating\*\*\s*:\s*([A-Za-z]+)", re.IGNORECASE)
_PRICE_TARGET_RE = re.compile(r"\*\*Price Target\*\*\s*:\s*([0-9.,]+)", re.IGNORECASE)


def _parse_float(s: Optional[str]) -> Optional[float]:
    if not s:
        return None
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def _action_from_rating(rating: str) -> Optional[str]:
    r = (rating or "").strip().lower()
    if r in ("buy", "overweight"):
        return "buy"
    if r in ("sell", "underweight"):
        return "sell"
    return None  # 'hold' yields no order


def _parse_decision_card(reports: list, final_decision: str) -> dict[str, Any]:
    """Pull the structured fields out of trader_proposal + final_decision.

    Both markdowns can contribute. Trader proposal usually has the
    concrete Entry/Target/Stop levels; final_decision has the Portfolio
    Manager's Rating. We merge with trader taking precedence on price
    levels and PM Rating taking precedence on the directional decision.
    """
    parsed: dict[str, Any] = {
        "action": None,
        "entry_price": None,
        "target_price": None,
        "stop_loss": None,
    }
    trader_md = ""
    for r in reports:
        if r["report_type"] == "trader_proposal":
            trader_md = r["content"]
            break
    pm_md = final_decision or ""

    if trader_md:
        m = _ACTION_RE.search(trader_md)
        if m:
            parsed["action"] = m.group(1).strip().lower()
        for key, regex in (
            ("entry_price", _ENTRY_RE),
            ("target_price", _TARGET_RE),
            ("stop_loss", _STOP_RE),
        ):
            m = regex.search(trader_md)
            if m:
                parsed[key] = _parse_float(m.group(1))

    if pm_md:
        rm = _RATING_RE.search(pm_md)
        if rm:
            action_from_rating = _action_from_rating(rm.group(1))
            if action_from_rating:
                parsed["action"] = action_from_rating
        if parsed["target_price"] is None:
            ptm = _PRICE_TARGET_RE.search(pm_md)
            if ptm:
                parsed["target_price"] = _parse_float(ptm.group(1))
        if parsed["stop_loss"] is None:
            stm = _STOP_RE.search(pm_md)
            if stm:
                parsed["stop_loss"] = _parse_float(stm.group(1))

    return parsed


def _fetch_last_price_sync(ticker: str) -> Optional[float]:
    """Latest price for a ticker (live intraday quote, kline close as fallback).

    Wrapped with the in-process price cache (see ``_PRICE_CACHE_TTL_SEC``) so
    rapid repeat hits — the Paper / Holdings pages fan out per-row quote
    requests, plus refresh-on-focus — collapse onto one upstream call. A None
    result is cached too, so a delisted / suspended ticker isn't retried every
    render.
    """
    hit, cached = _price_cache_get(ticker)
    if hit:
        return cached
    price = _fetch_last_price_uncached(ticker)
    _price_cache_put(ticker, price)
    return price


def _spot_secid(ticker: str) -> Optional[tuple[str, str, str]]:
    """Map a 6-digit A-share code to (sina_sym, tencent_sym, em_secid).

    Prefix → exchange: 6/9 → 沪 (sh / em market 1); else → 深 (sz / em market 0).
    Returns None for non-6-digit (e.g. US) tickers, which fall back to klines.
    """
    digits = re.sub(r"\D", "", ticker or "")
    if len(digits) < 6:
        return None
    code = digits[-6:]
    if code[0] in ("6", "9"):
        return f"sh{code}", f"sh{code}", f"1.{code}"
    return f"sz{code}", f"sz{code}", f"0.{code}"


def _fetch_spot_price(ticker: str) -> Optional[float]:
    """Live intraday last price via the lightweight single-quote endpoints.

    These vendor quote endpoints (tencent qt.gtimg.cn, sina hq.sinajs.cn,
    eastmoney push2) return one ticker in a few KB and — unlike the bulk
    spot/kline endpoints AKShare scrapes — are NOT aggressively rate-limited,
    so they stay fast and reliable when ``stock_zh_a_spot_em`` gets RST-throttled.
    Using a live quote (not yesterday's / today's kline close) also means
    intraday P&L reflects the current price instead of sticking near cost.
    Tries tencent → sina → eastmoney; returns None if all fail (caller then
    falls back to the kline path).
    """
    syms = _spot_secid(ticker)
    if not syms:
        return None
    sina_sym, tx_sym, em_secid = syms

    def _get(url: str, referer: str) -> Optional[str]:
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0", "Referer": referer})
            return urllib.request.urlopen(req, timeout=4).read().decode("gbk", "ignore")
        except Exception:  # noqa: BLE001 — try the next vendor
            return None

    # 1) Tencent: v_sz300686="51~名称~code~<last>~<prev_close>~..."
    body = _get(f"https://qt.gtimg.cn/q={tx_sym}", "https://gu.qq.com/")
    if body and "~" in body:
        try:
            parts = body.split('"')[1].split("~")
            price = float(parts[3])
            if price > 0:
                return round(price, 3)
        except (IndexError, ValueError):
            pass

    # 2) Sina: var hq_str_sz300686="名称,open,prev_close,<last>,high,low,..."
    body = _get(f"https://hq.sinajs.cn/list={sina_sym}", "https://finance.sina.com.cn/")
    if body and '"' in body:
        try:
            parts = body.split('"')[1].split(",")
            price = float(parts[3])
            if price > 0:
                return round(price, 3)
        except (IndexError, ValueError):
            pass

    # 3) Eastmoney: {"data":{"f43":<last*100>,...}} — f43 is price in 分.
    body = _get(
        f"https://push2.eastmoney.com/api/qt/stock/get?secid={em_secid}&fields=f43",
        "https://quote.eastmoney.com/")
    if body and '"f43"' in body:
        try:
            import json
            f43 = json.loads(body).get("data", {}).get("f43")
            if f43 and f43 > 0:
                return round(f43 / 100.0, 3)
        except (ValueError, TypeError, KeyError):
            pass

    return None


def _fetch_last_price_uncached(ticker: str) -> Optional[float]:
    # Prefer the fast, un-throttled live quote; only fall back to the heavier
    # kline path (route_to_vendor) when all spot endpoints fail.
    spot = _fetch_spot_price(ticker)
    if spot is not None:
        return spot

    from tradingagents.dataflows.interface import route_to_vendor

    end_dt = datetime.utcnow()
    start_dt = end_dt - timedelta(days=10)
    last_err: Optional[Exception] = None
    # One short retry on transient RST — eastmoney throttling often
    # recovers on the second try after a brief pause.
    for attempt in range(2):
        try:
            csv_str = route_to_vendor(
                "get_stock_data", ticker,
                start_dt.strftime("%Y-%m-%d"),
                end_dt.strftime("%Y-%m-%d"),
            )
            break
        except Exception as e:
            last_err = e
            if attempt == 0:
                time.sleep(0.6)
            else:
                logger.warning("Quote fetch failed for %s: %s", ticker, e)
                return None
    else:
        return None
    if not isinstance(csv_str, str):
        return None
    header_end = csv_str.find("\n\n")
    data_section = csv_str[header_end + 2:] if header_end != -1 else csv_str
    if "No " in csv_str[:200] and "data" in csv_str[:200]:
        return None
    try:
        df = pd.read_csv(io.StringIO(data_section))
    except Exception:
        return None
    if df.empty or "Close" not in df.columns:
        return None
    closes = df["Close"].dropna().tolist()
    if not closes:
        return None
    return float(closes[-1])


# ---------------------------------------------------------------------------
# Market-state snapshot (for auto-trade suspension / limit-up-down checks)
# ---------------------------------------------------------------------------
# Unlike ``_fetch_last_price_*`` (which only needs the latest price for P&L
# display), the auto-trade hook needs more context: the previous close (to
# compute the day's % move for a limit check) and the most recent bar date
# (to detect a suspended / stale ticker). This is a separate, heavier path
# called at most once per auto-trade decision, so it doesn't share the hot
# UI quote cache.


def _spot_snapshot(ticker: str) -> Optional[dict]:
    """Live snapshot via tencent/sina single-quote endpoints.

    Returns ``{last, prev_close, high, low}`` (any field may be None) or None
    if the ticker isn't an A-share code or all vendors fail. Reuses the same
    endpoints + parsing shape as ``_fetch_spot_price``; tencent and sina both
    carry prev_close, sina additionally carries today's high/low.
    """
    syms = _spot_secid(ticker)
    if not syms:
        return None
    sina_sym, tx_sym, _ = syms

    def _get(url: str, referer: str) -> Optional[str]:
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0", "Referer": referer})
            return urllib.request.urlopen(req, timeout=4).read().decode("gbk", "ignore")
        except Exception:  # noqa: BLE001
            return None

    # Sina is richest: "名称,open,prev_close,last,high,low,..." — try it first.
    body = _get(f"https://hq.sinajs.cn/list={sina_sym}", "https://finance.sina.com.cn/")
    if body and '"' in body:
        try:
            parts = body.split('"')[1].split(",")
            last = float(parts[3])
            prev = float(parts[2])
            high = float(parts[4])
            low = float(parts[5])
            if last > 0 and prev > 0:
                return {"last": round(last, 3), "prev_close": round(prev, 3),
                        "high": round(high, 3) if high > 0 else None,
                        "low": round(low, 3) if low > 0 else None}
        except (IndexError, ValueError):
            pass

    # Tencent fallback: "51~名称~code~last~prev_close~..." (no clean high/low here).
    body = _get(f"https://qt.gtimg.cn/q={tx_sym}", "https://gu.qq.com/")
    if body and "~" in body:
        try:
            parts = body.split('"')[1].split("~")
            last = float(parts[3])
            prev = float(parts[4])
            if last > 0 and prev > 0:
                return {"last": round(last, 3), "prev_close": round(prev, 3),
                        "high": None, "low": None}
        except (IndexError, ValueError):
            pass
    return None


def _kline_snapshot(ticker: str) -> Optional[dict]:
    """Kline-based snapshot: last two closes + the most recent bar date.

    The bar date is what staleness/suspension detection keys off — a non-
    today last bar (allowing for weekends) means the ticker likely isn't
    trading. Returns ``{last, prev_close, high, low, bar_date}`` or None.
    """
    from tradingagents.dataflows.interface import route_to_vendor

    end_dt = datetime.utcnow()
    start_dt = end_dt - timedelta(days=15)
    try:
        csv_str = route_to_vendor(
            "get_stock_data", ticker,
            start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d"),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("snapshot kline fetch failed for %s: %s", ticker, e)
        return None
    if not isinstance(csv_str, str):
        return None
    header_end = csv_str.find("\n\n")
    data_section = csv_str[header_end + 2:] if header_end != -1 else csv_str
    if "No " in csv_str[:200] and "data" in csv_str[:200]:
        return None
    try:
        df = pd.read_csv(io.StringIO(data_section))
    except Exception:  # noqa: BLE001
        return None
    if df.empty or "Close" not in df.columns:
        return None
    # Resolve the date column (Date / date / first unnamed col / index).
    date_col = next((c for c in ("Date", "date", "Unnamed: 0") if c in df.columns), None)
    bar_date = None
    if date_col is not None:
        try:
            bar_date = pd.to_datetime(df[date_col]).dt.date.iloc[-1]
        except Exception:  # noqa: BLE001
            bar_date = None
    closes = df["Close"].dropna().tolist()
    if not closes:
        return None
    last_row = df.iloc[-1]
    return {
        "last": float(closes[-1]),
        "prev_close": float(closes[-2]) if len(closes) >= 2 else None,
        "high": float(last_row["High"]) if "High" in df.columns and pd.notna(last_row.get("High")) else None,
        "low": float(last_row["Low"]) if "Low" in df.columns and pd.notna(last_row.get("Low")) else None,
        "bar_date": bar_date,
    }


def _fetch_quote_snapshot(ticker: str) -> Optional[dict]:
    """Best-effort market snapshot for auto-trade gating.

    Merges the live spot quote (fresh last/prev/high/low, but no bar date)
    with the kline snapshot (carries bar_date for staleness). Prefers live
    values where present. Returns a dict with keys
    ``last, prev_close, high, low, bar_date`` (any may be None) or None.
    """
    spot = _spot_snapshot(ticker)
    kline = _kline_snapshot(ticker)
    if spot is None and kline is None:
        return None
    snap = dict(kline or {})
    if spot:
        for k in ("last", "prev_close", "high", "low"):
            if spot.get(k) is not None:
                snap[k] = spot[k]
    snap.setdefault("bar_date", None)
    return snap


# --- endpoints ---

@router.get("/account")
async def get_account():
    """Return the default account, creating it if missing."""
    loop = asyncio.get_running_loop()
    acct = await loop.run_in_executor(None, db.ensure_default_paper_account)
    return acct


@router.post("/account/reset")
async def reset(req: PaperAccountReset):
    if not req.confirm:
        raise HTTPException(
            status_code=400,
            detail="set confirm=true to wipe positions/orders/nav and reset cash",
        )
    loop = asyncio.get_running_loop()
    acct = await loop.run_in_executor(None, db.ensure_default_paper_account)
    updated = await loop.run_in_executor(
        None, db.reset_paper_account, acct["id"], req.initial_cash)
    return updated


@router.get("/positions")
async def positions(with_prices: bool = True):
    """Current open positions.

    ``with_prices`` (default True) decorates each row with a live quote + P&L,
    which means a (cache-cold) kline fetch per ticker — the slow part. The
    Paper page calls it twice: first ``with_prices=false`` to paint the table
    instantly, then ``with_prices=true`` to fill in quotes asynchronously. So a
    page visit feels instant even when the upstream quote feed is degraded.
    """
    loop = asyncio.get_running_loop()
    acct = await loop.run_in_executor(None, db.ensure_default_paper_account)
    positions = await loop.run_in_executor(None, db.list_paper_positions, acct["id"])

    if not with_prices:
        # Instant path: holdings only. Use names ONLY if already cached — don't
        # warm the map here, since a cold fetch over a slow feed would defeat
        # the whole point of the fast paint. Names + prices arrive on the
        # subsequent with_prices=true fetch.
        name_cache = _name_map_cached_only()
        items = [{**p, "name": name_cache.get(re.sub(r"\D", "", p["ticker"])[-6:]),
                  "last_price": None, "market_value": None,
                  "pnl_amount": None, "pnl_pct": None}
                 for p in positions]
        return {"items": items, "total": len(items)}

    # Warm the name map once off-loop; decorate() then does cheap dict lookups.
    await loop.run_in_executor(None, _name_map)

    async def decorate(p):
        name = _resolve_name(p["ticker"])
        last = await loop.run_in_executor(None, _fetch_last_price_sync, p["ticker"])
        if last is None:
            return {**p, "name": name, "last_price": None, "market_value": None,
                    "pnl_amount": None, "pnl_pct": None}
        shares = float(p["shares"])
        cost = float(p["avg_cost"])
        mv = round(shares * last, 2)
        pnl = round(shares * (last - cost), 2)
        pct = round((last - cost) / cost * 100, 2) if cost else None
        return {**p, "name": name, "last_price": last, "market_value": mv,
                "pnl_amount": pnl, "pnl_pct": pct}

    # Decorate concurrently, but cap the whole batch at _PRICE_PATH_BUDGET_SEC.
    # On timeout, return rows that finished (priced) and fall back to bare
    # holdings for the rest, so a dead quote feed can't hang the request.
    tasks = [asyncio.ensure_future(decorate(p)) for p in positions]
    try:
        decorated = await asyncio.wait_for(
            asyncio.gather(*tasks), timeout=_PRICE_PATH_BUDGET_SEC
        )
    except asyncio.TimeoutError:
        decorated = []
        for p, task in zip(positions, tasks):
            if task.done() and not task.cancelled() and not task.exception():
                decorated.append(task.result())
            else:
                task.cancel()
                decorated.append({**p, "name": _resolve_name(p["ticker"]),
                                  "last_price": None, "market_value": None,
                                  "pnl_amount": None, "pnl_pct": None})
        logger.warning("positions price path timed out at %.0fs; %d/%d priced",
                       _PRICE_PATH_BUDGET_SEC,
                       sum(1 for d in decorated if d["last_price"] is not None),
                       len(decorated))
    return {"items": decorated, "total": len(decorated)}


@router.get("/orders")
async def orders():
    loop = asyncio.get_running_loop()
    acct = await loop.run_in_executor(None, db.ensure_default_paper_account)
    items = await loop.run_in_executor(None, db.list_paper_orders, acct["id"], 200)
    await loop.run_in_executor(None, _name_map)  # warm cache off-loop
    items = [{**o, "name": _resolve_name(o["ticker"])} for o in items]
    return {"items": items, "total": len(items)}


@router.post("/orders")
async def place_order(req: PaperOrderRequest):
    loop = asyncio.get_running_loop()
    acct = await loop.run_in_executor(None, db.ensure_default_paper_account)
    price = req.price
    if price is None:
        price = await loop.run_in_executor(None, _fetch_last_price_sync, req.ticker)
    if price is None or price <= 0:
        raise HTTPException(
            status_code=400, detail="无法获取价格，请手动指定 price",
        )
    order, err = await loop.run_in_executor(
        None,
        lambda: db.place_paper_order(
            account_id=acct["id"],
            ticker=req.ticker,
            asset_type=req.asset_type,
            action=req.action,
            shares=req.shares,
            price=float(price),
            source=req.source,
            source_analysis_id=req.source_analysis_id,
            notes=req.notes,
        ),
    )
    if err:
        raise HTTPException(status_code=400, detail=err)
    return order


@router.post("/orders/from-decision")
async def order_from_decision(req: PaperOrderFromDecision):
    """Open a position based on a completed analysis's decision card.

    The action comes from the Portfolio Manager's Rating (Buy/Overweight
    → buy, Sell/Underweight → sell, Hold → 400). The price comes from
    the Trader's Entry Price (or the supplied override, or the latest
    close as a last resort).
    """
    loop = asyncio.get_running_loop()
    if (req.shares is None) == (req.cash_fraction is None):
        raise HTTPException(
            status_code=400,
            detail="指定 shares 或 cash_fraction 中的恰好一个",
        )
    analysis = await loop.run_in_executor(None, db.get_analysis, req.analysis_id)
    if not analysis:
        raise HTTPException(status_code=404, detail="分析记录不存在")
    if analysis["status"] != "complete":
        raise HTTPException(status_code=400, detail="该分析尚未完成")

    reports = await loop.run_in_executor(None, db.get_agent_reports, req.analysis_id)
    parsed = _parse_decision_card(reports, analysis.get("final_decision") or "")
    action = parsed["action"]
    if action not in ("buy", "sell"):
        raise HTTPException(
            status_code=400,
            detail=f"该决策为 {action or 'Hold'}，不产生订单",
        )
    price = req.price or parsed["entry_price"]
    if price is None or price <= 0:
        price = await loop.run_in_executor(
            None, _fetch_last_price_sync, analysis["ticker"],
        )
    if price is None or price <= 0:
        raise HTTPException(status_code=400, detail="无法解析价格，请手动指定 price")

    acct = await loop.run_in_executor(None, db.ensure_default_paper_account)
    if req.shares is not None:
        shares = float(req.shares)
    else:
        frac = max(0.0, min(1.0, float(req.cash_fraction or 0)))
        # For a buy: spend `frac` of cash; for a sell: sell `frac` of the
        # current position (proportional exit).
        if action == "buy":
            shares = (acct["cash"] * frac) / float(price)
        else:
            positions = await loop.run_in_executor(
                None, db.list_paper_positions, acct["id"],
            )
            pos = next((p for p in positions if p["ticker"] == analysis["ticker"].upper()), None)
            if not pos:
                raise HTTPException(status_code=400, detail="该标的无持仓可卖")
            shares = float(pos["shares"]) * frac
        # Round to 2 decimals — fine for both A-share lots (multiples of 100
        # come out clean) and fractional US shares.
        shares = round(shares, 2)
    if shares <= 0:
        raise HTTPException(status_code=400, detail="计算出的下单数量为 0")

    notes_parts = [f"来源决策 {req.analysis_id[:8]}"]
    if parsed.get("target_price"):
        notes_parts.append(f"目标 {parsed['target_price']}")
    if parsed.get("stop_loss"):
        notes_parts.append(f"止损 {parsed['stop_loss']}")
    notes = " / ".join(notes_parts)

    order, err = await loop.run_in_executor(
        None,
        lambda: db.place_paper_order(
            account_id=acct["id"],
            ticker=analysis["ticker"],
            asset_type=analysis.get("asset_type", "stock"),
            action=action,
            shares=shares,
            price=float(price),
            source="decision",
            source_analysis_id=req.analysis_id,
            notes=notes,
        ),
    )
    if err:
        raise HTTPException(status_code=400, detail=err)
    return order


@router.get("/nav")
async def nav():
    loop = asyncio.get_running_loop()
    acct = await loop.run_in_executor(None, db.ensure_default_paper_account)
    items = await loop.run_in_executor(None, db.list_paper_nav, acct["id"], 365)
    # Return chronological order for chart consumption.
    items.sort(key=lambda r: r["snapshot_date"])
    return {"items": items, "total": len(items)}


@router.post("/nav/snapshot")
async def take_snapshot():
    """Compute and persist today's NAV snapshot.

    Marks-to-market every open position using the latest close, sums with
    cash, and writes (or replaces) the row for today's date.
    """
    loop = asyncio.get_running_loop()
    acct = await loop.run_in_executor(None, db.ensure_default_paper_account)
    return await loop.run_in_executor(
        None, compute_and_store_nav_snapshot, acct["id"],
    )


def compute_and_store_nav_snapshot(account_id: int) -> dict:
    """Mark-to-market every open position and upsert today's NAV row.

    Synchronous so it can be called from both the HTTP handler (via
    run_in_executor) and the background scheduler's daily auto-snapshot.
    Quote failures fall back to cost basis so the snapshot never goes NaN.
    """
    acct = db.get_paper_account(account_id) or db.ensure_default_paper_account()
    positions = db.list_paper_positions(acct["id"])
    positions_value = 0.0
    for p in positions:
        last = _fetch_last_price_sync(p["ticker"])
        if last is None:
            positions_value += float(p["shares"]) * float(p["avg_cost"])
        else:
            positions_value += float(p["shares"]) * last
    snapshot_date = datetime.now().strftime("%Y-%m-%d")
    db.upsert_paper_nav(
        acct["id"], snapshot_date, acct["cash"], round(positions_value, 2),
    )
    return {
        "snapshot_date": snapshot_date,
        "cash": acct["cash"],
        "positions_value": round(positions_value, 2),
        "total_value": round(acct["cash"] + positions_value, 2),
    }


def _limit_pct_for(ticker: str, name: Optional[str]) -> Optional[float]:
    """Daily price-limit magnitude for an A-share, as a fraction (0.10 = ±10%).

    Rules (Shanghai/Shenzhen): ST / *ST → ±5%; 创业板 (300xxx) / 科创板
    (688xxx) → ±20%; 北交所 (8xx/4xx) → ±30%; everything else → ±10%.
    Returns None for non-A-share tickers (no limit modelled — US etc.).
    """
    if not db._is_a_share_ticker(ticker):
        return None
    if name and ("ST" in name.upper() or "退" in name):
        return 0.05
    digits = re.sub(r"\D", "", ticker or "")
    code = digits[-6:] if len(digits) >= 6 else digits
    if code.startswith(("300", "301", "688")):
        return 0.20
    if code.startswith(("8", "4")):
        return 0.30  # 北交所
    return 0.10


def _check_market_state(ticker: str, action: str) -> Optional[str]:
    """Gate an auto-trade against suspension + price-limit conditions.

    Returns a human-readable skip reason if the order should NOT go through,
    or None if it's clear to trade. Best-effort: a missing snapshot or
    non-A-share ticker returns None (trade allowed) rather than blocking.
    """
    snap = _fetch_quote_snapshot(ticker)
    if not snap:
        return None  # no data → don't block; downstream price fetch handles it

    # --- suspension / stale-data: last kline bar isn't recent enough ---
    bar_date = snap.get("bar_date")
    if bar_date is not None:
        gap_days = (datetime.now().date() - bar_date).days
        # >4 calendar days covers a normal Fri→next-week gap; beyond that the
        # ticker is almost certainly suspended or delisted.
        if gap_days > 4:
            return f"{ticker} 最新行情停留在 {bar_date}（疑似停牌/退市），跳过"

    # --- limit up / down: compute today's % move vs prev close ---
    last = snap.get("last")
    prev = snap.get("prev_close")
    limit = _limit_pct_for(ticker, _resolve_name(ticker))
    if last and prev and prev > 0 and limit is not None:
        move = (last - prev) / prev
        # Use a small tolerance so a price a hair below the cap still counts
        # as limit (boards round to a tick; we don't have the exact cap price).
        near = limit - 0.003
        if action == "buy" and move >= near:
            return f"{ticker} 涨幅 {move*100:.1f}% 触及涨停（限制 ±{limit*100:.0f}%），买不进，跳过"
        if action == "sell" and move <= -near:
            return f"{ticker} 跌幅 {move*100:.1f}% 触及跌停（限制 ±{limit*100:.0f}%），卖不出，跳过"
    return None


def execute_auto_trade(
    analysis_id: str,
    *,
    cash_fraction: float = 0.1,
    skip_if_held: bool = True,
) -> tuple[Optional[dict], str]:
    """Turn a completed analysis's decision into a paper order automatically.

    Designed to be called from the scheduler after a scheduled analysis
    completes. Synchronous and **never raises** — any problem is returned
    as a human-readable reason string so a failure can't take down the
    scheduler tick. Returns ``(order_or_None, reason)``.

    Trading rules (per the auto-trade product spec):
      - BUY  + not held  → buy ``cash_fraction`` of available cash
      - BUY  + already held → skip (no pyramiding) when ``skip_if_held``
      - SELL + held      → flatten the whole position
      - SELL + not held  → skip
      - HOLD / no action → skip

    Reuses the same decision-card parser, price helper, and broker call as
    the manual ``order_from_decision`` endpoint.
    """
    try:
        analysis = db.get_analysis(analysis_id)
        if not analysis:
            return None, "分析记录不存在"
        if analysis.get("status") != "complete":
            return None, "分析尚未完成，跳过自动交易"

        reports = db.get_agent_reports(analysis_id)
        parsed = _parse_decision_card(reports, analysis.get("final_decision") or "")
        action = parsed["action"]
        if action not in ("buy", "sell"):
            return None, f"决策为 {action or 'Hold'}，不产生订单"

        ticker = analysis["ticker"]
        acct = db.ensure_default_paper_account()
        positions = db.list_paper_positions(acct["id"])
        pos = next((p for p in positions if p["ticker"] == ticker.upper()), None)

        # Suspension / limit-up-down gate: skip orders that couldn't fill in
        # a real market (suspended ticker, or a board locked at its daily cap).
        blocked = _check_market_state(ticker, action)
        if blocked:
            return None, blocked

        price = parsed.get("entry_price")
        if price is None or price <= 0:
            price = _fetch_last_price_sync(ticker)
        if price is None or price <= 0:
            return None, "无法获取价格，跳过自动交易"
        price = float(price)

        if action == "buy":
            if pos and skip_if_held:
                return None, f"{ticker} 已持仓，不加仓"
            frac = max(0.0, min(1.0, float(cash_fraction)))
            is_a = db._is_a_share_ticker(ticker)
            lot = 100 if is_a else 1  # A股按 100 股整手，其余按 1 股
            budget = acct["cash"] * frac
            raw_shares = budget / price
            if is_a:
                shares = int(raw_shares // 100) * 100  # whole 100-share lots
            else:
                shares = round(raw_shares, 2)
            # Small-account floor: if the cash_fraction budget can't even cover
            # one lot but the *total* available cash can, buy a single lot so a
            # ¥10k account still trades. The position then exceeds cash_fraction
            # for that order — intended for tiny accounts where 1 lot is the
            # minimum tradable unit.
            if shares < lot and acct["cash"] + 1e-9 >= lot * price:
                shares = lot
            if shares <= 0:
                return None, "可用现金不足以买入 1 手，跳过"
        else:  # sell
            if not pos:
                return None, f"{ticker} 无持仓可卖，跳过"
            shares = float(pos["shares"])

        notes_parts = [f"自动交易 · 来源 {analysis_id[:8]}"]
        if parsed.get("target_price"):
            notes_parts.append(f"目标 {parsed['target_price']}")
        if parsed.get("stop_loss"):
            notes_parts.append(f"止损 {parsed['stop_loss']}")

        order, err = db.place_paper_order(
            account_id=acct["id"],
            ticker=ticker,
            asset_type=analysis.get("asset_type", "stock"),
            action=action,
            shares=shares,
            price=price,
            source="auto",
            source_analysis_id=analysis_id,
            notes=" / ".join(notes_parts),
        )
        if err:
            return None, err
        return order, f"{action} {shares} {ticker} @ {price}"
    except Exception as e:  # belt-and-braces: scheduler must not crash
        logger.exception("auto-trade failed for analysis %s", analysis_id)
        return None, f"自动交易异常: {e}"
