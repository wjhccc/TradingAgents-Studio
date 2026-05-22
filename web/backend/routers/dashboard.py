from fastapi import APIRouter, Query
from typing import Optional

from .. import database as db

router = APIRouter(prefix="/api", tags=["dashboard"])


@router.get("/dashboard")
async def dashboard():
    stats = db.get_dashboard_stats()
    return stats


@router.get("/compare")
async def compare(
    tickers: str = Query(..., description="Comma-separated ticker list"),
    days: int = 30,
):
    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    return db.get_compare(ticker_list, days)
