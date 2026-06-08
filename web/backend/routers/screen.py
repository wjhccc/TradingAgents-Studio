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
from ..models import (
    ScreenRequest, ScreenToPaperRequest, ScreenToAnalyzeRequest,
    ScreenToScheduleRequest,
)
from ..screener_runner import ScreenerRunner

router = APIRouter(prefix="/api/screen", tags=["screen"])

# analysis_id-style live queue map, consumed by the /ws/screen/{id} socket.
_active_queues: dict[str, asyncio.Queue] = {}

# Strong refs to fire-and-forget run tasks so the GC can't drop a mid-flight
# run (asyncio only weakly references a bare create_task result).
_bg_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return task


@router.post("")
async def start_screen(req: ScreenRequest):
    run_id = str(uuid.uuid4())
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: db.create_screen_run(run_id, req.text))

    queue = asyncio.Queue()
    _active_queues[run_id] = queue
    runner = ScreenerRunner(run_id, req.text, req.filters, req.top_n, req.use_llm, queue)
    _spawn(_run_and_cleanup(run_id, runner))
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


@router.delete("/{run_id}")
async def delete_screen(run_id: str):
    """Delete a screen run from history."""
    loop = asyncio.get_running_loop()
    deleted = await loop.run_in_executor(None, db.delete_screen_run, run_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="选股记录不存在")
    return {"ok": True}


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
    from .analyze import (
        _active_queues as analyze_queues,
        _run_and_cleanup as analyze_cleanup,
        _spawn as analyze_spawn,
    )
    from ..graph_runner import GraphRunner, build_config
    from ..models import AnalyzeRequest

    if not req.tickers:
        raise HTTPException(status_code=400, detail="未选择任何股票")
    trade_date = req.trade_date or datetime.now().strftime("%Y-%m-%d")
    loop = asyncio.get_running_loop()

    # Each ticker spawns a deep analysis that hard-requires an LLM. Check once
    # up front so a missing key returns 400 instead of starting N doomed runs.
    from ..llm_health import check_llm_ready
    err = await loop.run_in_executor(None, check_llm_ready)
    if err:
        raise HTTPException(status_code=400, detail=f"LLM 未就绪，无法启动分析：{err}")

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
        analyze_spawn(analyze_cleanup(analysis_id, runner))
        started.append({"ticker": ticker, "analysis_id": analysis_id})

    return {"started": started, "total": len(started)}


@router.post("/{run_id}/to-schedule")
async def screen_to_schedule(run_id: str, req: ScreenToScheduleRequest):
    """Turn the selected tickers into an auto-trading portfolio.

    Creates one schedule per ticker with ``auto_trade`` on. Default frequency is
    ``interval`` (intraday monitoring every ``interval_minutes`` — the scheduler
    only fires interval runs during trading hours); ``daily`` runs once a day at
    ``time_of_day``. Tickers that already have an active schedule are skipped (no
    dupes), mirroring ``schedule.bulk_from_holdings``.
    """
    from ..scheduler import compute_first_run_at

    if not req.tickers:
        raise HTTPException(status_code=400, detail="未选择任何股票")
    is_interval = req.schedule_type == "interval"
    if is_interval and req.interval_minutes < 5:
        raise HTTPException(status_code=400, detail="interval_minutes 不能小于 5")
    loop = asyncio.get_running_loop()
    existing = await loop.run_in_executor(None, db.list_schedules, None)
    active_tickers = {
        s["ticker"].upper() for s in existing if s["status"] != "disabled"
    }
    interval_minutes = req.interval_minutes if is_interval else None
    # First run brought forward to the next open trading moment (now if the
    # market is already in session; interval/daily handled in compute_first_run_at).
    next_run = compute_first_run_at(
        req.schedule_type, interval_minutes, req.time_of_day, None, req.asset_type,
    )
    config = {
        "max_debate_rounds": req.max_debate_rounds,
        "max_risk_discuss_rounds": req.max_risk_discuss_rounds,
        "llm_provider": None,
        "deep_think_llm": None,
        "quick_think_llm": None,
        "output_language": "Chinese",
        "checkpoint_enabled": False,
    }
    created = 0
    skipped = []
    for ticker in req.tickers:
        t = ticker.strip().upper()
        if t in active_tickers:
            skipped.append(t)
            continue
        await loop.run_in_executor(
            None,
            lambda t=t: db.create_schedule(
                name=f"自动交易: {t}",
                ticker=t,
                asset_type=req.asset_type,
                schedule_type=req.schedule_type,
                interval_minutes=interval_minutes,
                time_of_day=None if is_interval else req.time_of_day,
                day_of_week=None,
                analysts=req.analysts,
                config=config,
                next_run_at=next_run,
                from_holding=False,
                auto_trade=req.auto_trade,
                auto_trade_cash_fraction=req.auto_trade_cash_fraction,
            ),
        )
        active_tickers.add(t)
        created += 1
    return {"created": created, "skipped": skipped}
