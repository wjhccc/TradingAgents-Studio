from fastapi import APIRouter, Query, HTTPException
from typing import Optional
import asyncio

from .. import database as db

router = APIRouter(prefix="/api", tags=["history"])


@router.get("/history")
async def list_history(
    ticker: Optional[str] = None,
    signal: Optional[str] = None,
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    page: int = 1,
    size: int = 20,
):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, lambda: db.list_analyses(ticker=ticker, signal=signal, date_from=date_from,
                                       date_to=date_to, page=page, size=size)
    )


@router.get("/reports/{analysis_id}")
async def get_report(analysis_id: str):
    loop = asyncio.get_running_loop()
    analysis = await loop.run_in_executor(None, db.get_analysis, analysis_id)
    if not analysis:
        raise HTTPException(status_code=404, detail="not found")
    reports = await loop.run_in_executor(None, db.get_agent_reports, analysis_id)
    events = await loop.run_in_executor(None, db.get_agent_events, analysis_id)
    return {
        "analysis": analysis,
        "reports": reports,
        "events": events,
    }


@router.delete("/reports/{analysis_id}")
async def delete_report(analysis_id: str):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, db.delete_analysis, analysis_id)
    return {"ok": True}


@router.get("/reports/{analysis_id}/export")
async def export_report(analysis_id: str, format: str = "md"):
    loop = asyncio.get_running_loop()
    analysis = await loop.run_in_executor(None, db.get_analysis, analysis_id)
    if not analysis:
        raise HTTPException(status_code=404, detail="not found")
    reports = await loop.run_in_executor(None, db.get_agent_reports, analysis_id)

    if format == "md":
        md = _build_markdown(analysis, reports)
        return {"format": "md", "content": md}
    return {"error": "unsupported format, use 'md'"}


def _build_markdown(analysis: dict, reports: list) -> str:
    lines = [
        f"# Trading Analysis Report: {analysis['ticker']}",
        f"**Date:** {analysis['trade_date']}  ",
        f"**Signal:** {analysis.get('signal', 'N/A')}  ",
        f"**Confidence:** {analysis.get('confidence', 'N/A')}  ",
        f"**Created:** {analysis['created_at']}  ",
        "",
        "---",
        "",
    ]
    for r in reports:
        lines.append(f"## {r['report_type'].replace('_', ' ').title()}")
        lines.append("")
        lines.append(r["content"])
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)
