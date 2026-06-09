"""REST endpoints for the recurring analysis scheduler.

Mirrors the holdings router style: thin async wrappers around sync DB
calls (run in the default executor), plus a "bulk from holdings" helper
that creates one schedule per current position in a single request.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException

from .. import database as db
from ..models import ScheduleCreate, ScheduleUpdate, ScheduleFromHoldings
from ..scheduler import compute_first_run_at, compute_next_run_at, service as scheduler_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/schedules", tags=["schedules"])


def _config_from_create(req: ScheduleCreate) -> dict:
    """Persisted LLM config — copied at create-time so later Settings
    changes don't silently alter scheduled runs."""
    return {
        "max_debate_rounds": req.max_debate_rounds,
        "max_risk_discuss_rounds": req.max_risk_discuss_rounds,
        "llm_provider": req.llm_provider,
        "deep_think_llm": req.deep_think_llm,
        "quick_think_llm": req.quick_think_llm,
        "output_language": req.output_language or "Chinese",
        "checkpoint_enabled": False,
    }


def _validate_schedule_type(req) -> None:
    if req.schedule_type not in ("interval", "daily", "weekly"):
        raise HTTPException(
            status_code=400,
            detail=f"schedule_type must be 'interval' | 'daily' | 'weekly'",
        )
    if req.schedule_type == "interval" and (req.interval_minutes or 0) < 5:
        raise HTTPException(
            status_code=400,
            detail="interval_minutes must be >= 5 to avoid hammering LLM providers",
        )
    if req.schedule_type in ("daily", "weekly") and not req.time_of_day:
        raise HTTPException(
            status_code=400,
            detail="time_of_day (HH:MM) is required for daily/weekly schedules",
        )
    if req.schedule_type == "weekly":
        if req.day_of_week is None or not (0 <= req.day_of_week <= 6):
            raise HTTPException(
                status_code=400,
                detail="day_of_week must be 0..6 (Mon..Sun) for weekly schedules",
            )


@router.get("")
async def list_all():
    loop = asyncio.get_running_loop()
    items = await loop.run_in_executor(None, db.list_schedules, None)
    return {"items": items, "total": len(items)}


@router.post("")
async def create(req: ScheduleCreate):
    _validate_schedule_type(req)
    next_run = compute_first_run_at(
        req.schedule_type,
        req.interval_minutes,
        req.time_of_day,
        req.day_of_week,
        req.asset_type,
    )
    loop = asyncio.get_running_loop()
    row = await loop.run_in_executor(
        None,
        lambda: db.create_schedule(
            name=req.name,
            ticker=req.ticker.strip().upper(),
            asset_type=req.asset_type,
            schedule_type=req.schedule_type,
            interval_minutes=req.interval_minutes,
            time_of_day=req.time_of_day,
            day_of_week=req.day_of_week,
            analysts=req.analysts,
            config=_config_from_create(req),
            next_run_at=next_run,
            from_holding=False,
            auto_trade=req.auto_trade,
            auto_trade_cash_fraction=req.auto_trade_cash_fraction,
        ),
    )
    return row


@router.put("/{schedule_id}")
async def update(schedule_id: int, req: ScheduleUpdate):
    loop = asyncio.get_running_loop()
    existing = await loop.run_in_executor(None, db.get_schedule, schedule_id)
    if not existing:
        raise HTTPException(status_code=404, detail="schedule not found")
    # If recurrence pattern changed, recompute next_run_at.
    fields = req.model_dump(exclude_unset=True)
    pattern_changed = any(
        k in fields
        for k in ("schedule_type", "interval_minutes", "time_of_day", "day_of_week")
    )
    if pattern_changed:
        merged = {**existing, **fields}
        fields["next_run_at"] = compute_next_run_at(
            merged.get("schedule_type") or existing["schedule_type"],
            merged.get("interval_minutes"),
            merged.get("time_of_day"),
            merged.get("day_of_week"),
        )
        # Re-enable a disabled schedule if the user is reconfiguring it,
        # but don't override an explicit status change in this request.
        if "status" not in fields and existing["status"] == "disabled":
            fields["status"] = "active"
            fields["fail_count"] = 0
    updated = await loop.run_in_executor(
        None,
        lambda: db.update_schedule(schedule_id, **fields),
    )
    return updated


@router.delete("/{schedule_id}")
async def delete(schedule_id: int):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, db.delete_schedule, schedule_id)
    return {"ok": True}


@router.post("/{schedule_id}/trigger")
async def trigger(schedule_id: int):
    """Fire a schedule once, immediately, without touching next_run_at.

    Used by the "Run now" button. Returns the new analysis_id so the UI
    can navigate straight to the progress page.
    """
    analysis_id = await scheduler_service.trigger_manual(schedule_id)
    if not analysis_id:
        raise HTTPException(status_code=404, detail="schedule not found")
    return {"analysis_id": analysis_id}


@router.post("/bulk-from-holdings")
async def bulk_from_holdings(req: ScheduleFromHoldings):
    """One-click: create a schedule for every current holding.

    Skips tickers that already have an active schedule (no duplicates).
    """
    _validate_schedule_type(req)
    loop = asyncio.get_running_loop()
    holdings = await loop.run_in_executor(None, db.list_holdings)
    existing = await loop.run_in_executor(None, db.list_schedules, None)
    active_tickers = {
        s["ticker"].upper() for s in existing if s["status"] != "disabled"
    }
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
    for h in holdings:
        t = h["ticker"].upper()
        if t in active_tickers:
            skipped.append(t)
            continue
        asset_type = h.get("asset_type", "stock")
        # Per-holding so each asset's trading session governs the immediate run.
        next_run = compute_first_run_at(
            req.schedule_type,
            req.interval_minutes,
            req.time_of_day,
            req.day_of_week,
            asset_type,
        )
        await loop.run_in_executor(
            None,
            lambda h=h, t=t, at=asset_type, nr=next_run: db.create_schedule(
                name=f"持仓: {t}",
                ticker=t,
                asset_type=at,
                schedule_type=req.schedule_type,
                interval_minutes=req.interval_minutes,
                time_of_day=req.time_of_day,
                day_of_week=req.day_of_week,
                analysts=req.analysts,
                config=config,
                next_run_at=nr,
                from_holding=True,
                auto_trade=req.auto_trade,
                auto_trade_cash_fraction=req.auto_trade_cash_fraction,
            ),
        )
        created += 1
    return {"created": created, "skipped": skipped}
