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
# 2nd request with RemoteDisconnected. A 15-second TTL is enough to
# collapse the storm without making the displayed price feel stale.
_PRICE_CACHE: dict[str, tuple[float, Optional[float]]] = {}
_PRICE_CACHE_TTL_SEC = 15.0
_PRICE_CACHE_LOCK = threading.Lock()


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
    """Latest close via the same vendor router holdings.quote uses.

    Wrapped with a 15-second in-process cache so rapid repeat hits (the
    Paper / Holdings pages tend to fan out per-row quote requests, and
    refresh on focus) don't trigger eastmoney's per-IP RST throttling.
    A None result is also cached for the TTL — saves us from retrying a
    delisted / suspended ticker every render.
    """
    hit, cached = _price_cache_get(ticker)
    if hit:
        return cached
    price = _fetch_last_price_uncached(ticker)
    _price_cache_put(ticker, price)
    return price


def _fetch_last_price_uncached(ticker: str) -> Optional[float]:
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
    updated = await loop.run_in_executor(None, db.reset_paper_account, acct["id"])
    return updated


@router.get("/positions")
async def positions():
    """Current open positions, decorated with live quote + P&L."""
    loop = asyncio.get_running_loop()
    acct = await loop.run_in_executor(None, db.ensure_default_paper_account)
    positions = await loop.run_in_executor(None, db.list_paper_positions, acct["id"])

    async def decorate(p):
        last = await loop.run_in_executor(None, _fetch_last_price_sync, p["ticker"])
        if last is None:
            return {**p, "last_price": None, "market_value": None,
                    "pnl_amount": None, "pnl_pct": None}
        shares = float(p["shares"])
        cost = float(p["avg_cost"])
        mv = round(shares * last, 2)
        pnl = round(shares * (last - cost), 2)
        pct = round((last - cost) / cost * 100, 2) if cost else None
        return {**p, "last_price": last, "market_value": mv,
                "pnl_amount": pnl, "pnl_pct": pct}

    decorated = await asyncio.gather(*(decorate(p) for p in positions))
    return {"items": decorated, "total": len(decorated)}


@router.get("/orders")
async def orders():
    loop = asyncio.get_running_loop()
    acct = await loop.run_in_executor(None, db.ensure_default_paper_account)
    items = await loop.run_in_executor(None, db.list_paper_orders, acct["id"], 200)
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
    positions = await loop.run_in_executor(None, db.list_paper_positions, acct["id"])

    positions_value = 0.0
    for p in positions:
        last = await loop.run_in_executor(None, _fetch_last_price_sync, p["ticker"])
        if last is None:
            # Fall back to cost basis when quote fails so the snapshot
            # doesn't become zero / NaN.
            positions_value += float(p["shares"]) * float(p["avg_cost"])
        else:
            positions_value += float(p["shares"]) * last
    snapshot_date = datetime.now().strftime("%Y-%m-%d")
    await loop.run_in_executor(
        None,
        lambda: db.upsert_paper_nav(
            acct["id"], snapshot_date, acct["cash"], round(positions_value, 2),
        ),
    )
    return {
        "snapshot_date": snapshot_date,
        "cash": acct["cash"],
        "positions_value": round(positions_value, 2),
        "total_value": round(acct["cash"] + positions_value, 2),
    }
