"""Holdings (持仓) REST endpoints.

CRUD over the ``holdings`` SQLite table plus a quote helper that grabs the
last close + day-range for each ticker via the AKShare/yfinance vendor
router. Quotes are computed on demand (no background polling) — the
frontend polls the endpoint when the holdings page is open.
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from fastapi import APIRouter, HTTPException

from .. import database as db
from ..models import HoldingCreate, HoldingUpdate, HoldingsImport

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/holdings", tags=["holdings"])


@router.get("")
async def list_all():
    """Return every holding, annotated with the latest analysis signal."""
    loop = asyncio.get_running_loop()
    holdings = await loop.run_in_executor(None, db.list_holdings)
    # Decorate with last analysis signal — cheap (1 query per ticker, indexed).
    for h in holdings:
        latest = await loop.run_in_executor(
            None, db.latest_signal_for_ticker, h["ticker"],
        )
        h["latest_analysis"] = latest
    return {"items": holdings, "total": len(holdings)}


@router.post("")
async def create(req: HoldingCreate):
    if req.shares <= 0 or req.cost_price <= 0:
        raise HTTPException(status_code=400, detail="shares and cost_price must be > 0")
    loop = asyncio.get_running_loop()
    row = await loop.run_in_executor(
        None,
        lambda: db.create_holding(
            ticker=req.ticker.strip().upper(),
            asset_type=req.asset_type,
            shares=req.shares,
            cost_price=req.cost_price,
            open_date=req.open_date,
            notes=req.notes,
        ),
    )
    return row


@router.put("/{holding_id}")
async def update(holding_id: int, req: HoldingUpdate):
    loop = asyncio.get_running_loop()
    updated = await loop.run_in_executor(
        None,
        lambda: db.update_holding(
            holding_id,
            shares=req.shares,
            cost_price=req.cost_price,
            open_date=req.open_date,
            notes=req.notes,
        ),
    )
    if not updated:
        raise HTTPException(status_code=404, detail="holding not found")
    return updated


@router.delete("/{holding_id}")
async def delete(holding_id: int):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, db.delete_holding, holding_id)
    return {"ok": True}


@router.post("/import")
async def import_csv(req: HoldingsImport):
    """Bulk import from pasted CSV text.

    Accepts a CSV with at minimum ``ticker, shares, cost_price`` columns.
    Optional columns: ``open_date``, ``notes``. Tolerates Chinese column
    headers (代码, 股数, 成本价, 持仓日期, 备注) so users can paste straight
    from broker exports without renaming columns first.
    """
    reader = csv.DictReader(io.StringIO(req.csv_text.strip()))
    header_map = {
        "代码": "ticker", "股票代码": "ticker", "ticker": "ticker", "symbol": "ticker",
        "股数": "shares", "数量": "shares", "持仓数量": "shares", "shares": "shares",
        "成本价": "cost_price", "成本": "cost_price", "买入价": "cost_price",
        "cost_price": "cost_price", "cost": "cost_price",
        "持仓日期": "open_date", "买入日期": "open_date", "open_date": "open_date", "date": "open_date",
        "备注": "notes", "notes": "notes",
    }
    created = 0
    errors: list[str] = []
    loop = asyncio.get_running_loop()
    for i, raw_row in enumerate(reader, start=2):
        norm = {
            header_map[k.strip()]: v.strip()
            for k, v in raw_row.items()
            if k and k.strip() in header_map and v
        }
        if "ticker" not in norm or "shares" not in norm or "cost_price" not in norm:
            errors.append(f"row {i}: missing required ticker/shares/cost_price")
            continue
        try:
            shares = float(norm["shares"].replace(",", ""))
            cost = float(norm["cost_price"].replace(",", ""))
        except ValueError as e:
            errors.append(f"row {i}: bad number — {e}")
            continue
        await loop.run_in_executor(
            None,
            lambda n=norm, s=shares, c=cost: db.create_holding(
                ticker=n["ticker"].upper(),
                asset_type=req.asset_type,
                shares=s,
                cost_price=c,
                open_date=n.get("open_date"),
                notes=n.get("notes"),
            ),
        )
        created += 1
    return {"created": created, "errors": errors}


@router.get("/{holding_id}/quote")
async def quote(holding_id: int):
    """Real-time-ish quote + P&L for one holding.

    Uses ``route_to_vendor("get_stock_data", ...)`` so A-share tickers go
    through AKShare and US-listed tickers go through yfinance — same
    routing the analysis pipeline uses. Returns ``None`` price if all
    vendors fail (typically because the market is closed and we can't
    fetch today's bar; the UI will fall back to "—").
    """
    loop = asyncio.get_running_loop()
    holding = await loop.run_in_executor(None, db.get_holding, holding_id)
    if not holding:
        raise HTTPException(status_code=404, detail="holding not found")

    last_price, prev_close = await loop.run_in_executor(
        None, _fetch_last_price, holding["ticker"],
    )
    pnl = _compute_pnl(holding, last_price)
    return {
        "ticker": holding["ticker"],
        "last_price": last_price,
        "prev_close": prev_close,
        **pnl,
    }


def _fetch_last_price(ticker: str) -> tuple[Optional[float], Optional[float]]:
    """Most recent close + previous-day close as (last, prev). None on failure."""
    from tradingagents.dataflows.interface import route_to_vendor
    import pandas as pd

    end_dt = datetime.utcnow()
    start_dt = end_dt - timedelta(days=10)
    try:
        csv_str = route_to_vendor(
            "get_stock_data",
            ticker,
            start_dt.strftime("%Y-%m-%d"),
            end_dt.strftime("%Y-%m-%d"),
        )
    except Exception as e:
        logger.warning("Quote fetch failed for %s: %s", ticker, e)
        return None, None

    if not isinstance(csv_str, str):
        return None, None

    header_end = csv_str.find("\n\n")
    data_section = csv_str[header_end + 2 :] if header_end != -1 else csv_str
    if "No " in csv_str[:200] and "data" in csv_str[:200]:
        return None, None
    try:
        df = pd.read_csv(io.StringIO(data_section))
    except Exception:
        return None, None
    if df.empty or "Close" not in df.columns:
        return None, None
    closes = df["Close"].dropna().tolist()
    if not closes:
        return None, None
    last = float(closes[-1])
    prev = float(closes[-2]) if len(closes) >= 2 else None
    return last, prev


def _compute_pnl(holding: dict, last_price: Optional[float]) -> dict[str, Any]:
    if last_price is None:
        return {"market_value": None, "pnl_amount": None, "pnl_pct": None}
    shares = float(holding["shares"])
    cost = float(holding["cost_price"])
    market_value = round(shares * last_price, 2)
    pnl_amount = round(shares * (last_price - cost), 2)
    pnl_pct = round((last_price - cost) / cost * 100, 2) if cost else None
    return {
        "market_value": market_value,
        "pnl_amount": pnl_amount,
        "pnl_pct": pnl_pct,
    }
