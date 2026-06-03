"""Stock-screening ("选股") REST endpoints.

A screen run is the upstream funnel before deep analysis: NL goal →
deterministic factor screen over the whole A-share market → bounded LLM
ranking. Runs stream progress over ``/ws/screen/{id}`` (see main.py) using
the same ``_active_queues`` pattern as analyze.

Two one-click handoffs close the loop downstream:
  - ``/to-paper``   — buy the selected tickers in the paper account.
  - ``/to-analyze`` — push them into the multi-agent analysis pipeline.
"""

from __future__ import annotations

import asyncio
import math
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException

from .. import database as db
from ..models import ScreenRequest, ScreenToPaperRequest, ScreenToAnalyzeRequest
from ..screener_runner import ScreenerRunner

router = APIRouter(prefix="/api/screen", tags=["screen"])

# analysis_id-style live queue map, consumed by the /ws/screen/{id} socket.
_active_queues: dict[str, asyncio.Queue] = {}


@router.post("")
async def start_screen(req: ScreenRequest):
    run_id = str(uuid.uuid4())
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: db.create_screen_run(run_id, req.text))

    queue = asyncio.Queue()
    _active_queues[run_id] = queue
    runner = ScreenerRunner(run_id, req.text, req.filters, req.top_n, req.use_llm, queue)
    asyncio.create_task(_run_and_cleanup(run_id, runner))
    return {"id": run_id, "status": "pending"}


async def _run_and_cleanup(run_id: str, runner: ScreenerRunner):
    try:
        await runner.run()
    finally:
        await asyncio.sleep(5)
        _active_queues.pop(run_id, None)


@router.get("")
async def list_screens():
    loop = asyncio.get_running_loop()
    items = await loop.run_in_executor(None, db.list_screen_runs, 50)
    return {"items": items, "total": len(items)}


@router.get("/{run_id}")
async def get_screen(run_id: str):
    loop = asyncio.get_running_loop()
    run = await loop.run_in_executor(None, db.get_screen_run, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="选股记录不存在")
    return run


def _floor_lot(ticker: str, shares: float) -> float:
    """A-share orders trade in 100-share lots — floor to a whole lot."""
    if db._is_a_share_ticker(ticker):
        return float(int(shares // 100) * 100)
    return float(round(shares, 2))


@router.post("/{run_id}/to-paper")
async def screen_to_paper(run_id: str, req: ScreenToPaperRequest):
    """Buy the selected tickers in the paper account. Returns per-ticker results."""
    from .paper import _fetch_last_price_sync

    if not req.tickers:
        raise HTTPException(status_code=400, detail="未选择任何股票")
    if req.sizing not in ("equal_cash", "fixed_cash", "fixed_shares"):
        raise HTTPException(status_code=400, detail=f"不支持的 sizing: {req.sizing}")

    loop = asyncio.get_running_loop()
    run = await loop.run_in_executor(None, db.get_screen_run, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="选股记录不存在")
    acct = await loop.run_in_executor(None, db.ensure_default_paper_account)

    # Per-ticker cash budget for equal_cash sizing.
    per_cash = None
    if req.sizing == "equal_cash":
        frac = max(0.0, min(1.0, float(req.value)))
        per_cash = (float(acct["cash"]) * frac) / len(req.tickers)
    elif req.sizing == "fixed_cash":
        per_cash = float(req.value)

    results = []
    for ticker in req.tickers:
        price = await loop.run_in_executor(None, _fetch_last_price_sync, ticker)
        if price is None or price <= 0:
            results.append({"ticker": ticker, "filled": False, "reason": "无法获取价格"})
            continue
        if req.sizing == "fixed_shares":
            shares = _floor_lot(ticker, float(req.value))
        else:
            shares = _floor_lot(ticker, per_cash / price)
        if shares <= 0:
            results.append({"ticker": ticker, "filled": False,
                            "reason": "可买数量为 0（金额不足 1 手）"})
            continue
        order, err = await loop.run_in_executor(
            None,
            lambda t=ticker, s=shares, p=price: db.place_paper_order(
                account_id=acct["id"], ticker=t, asset_type=req.asset_type,
                action="buy", shares=s, price=float(p),
                source="screen", source_analysis_id=run_id,
                notes=f"选股 {run_id[:8]}",
            ),
        )
        if err:
            results.append({"ticker": ticker, "filled": False, "reason": err})
        else:
            results.append({"ticker": ticker, "filled": True, "shares": shares,
                            "price": float(price), "order_id": order.get("id")})

    filled = sum(1 for r in results if r["filled"])
    return {"results": results, "filled": filled, "total": len(results)}


@router.post("/{run_id}/to-analyze")
async def screen_to_analyze(run_id: str, req: ScreenToAnalyzeRequest):
    """Kick off a deep analysis for each selected ticker. Returns their ids."""
    from .analyze import _active_queues as analyze_queues, _run_and_cleanup as analyze_cleanup
    from ..graph_runner import GraphRunner, build_config
    from ..models import AnalyzeRequest

    if not req.tickers:
        raise HTTPException(status_code=400, detail="未选择任何股票")
    trade_date = req.trade_date or datetime.now().strftime("%Y-%m-%d")
    loop = asyncio.get_running_loop()

    started = []
    for ticker in req.tickers:
        areq = AnalyzeRequest(
            ticker=ticker, trade_date=trade_date,
            asset_type=req.asset_type, analysts=req.analysts,
        )
        analysis_id = str(uuid.uuid4())
        config = build_config(areq)
        await loop.run_in_executor(None, lambda a=analysis_id, t=ticker, c=config: db.create_analysis(
            id=a, ticker=t, trade_date=trade_date, asset_type=req.asset_type,
            analysts=req.analysts, config=c,
        ))
        queue = asyncio.Queue()
        analyze_queues[analysis_id] = queue
        runner = GraphRunner(analysis_id, config, req.analysts, queue)
        asyncio.create_task(analyze_cleanup(analysis_id, runner))
        started.append({"ticker": ticker, "analysis_id": analysis_id})

    return {"started": started, "total": len(started)}
