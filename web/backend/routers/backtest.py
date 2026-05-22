"""REST endpoints for the backtest engine.

The engine itself lives in ``tradingagents.backtesting`` so it can be
exercised programmatically too (e.g. from a Jupyter notebook). The
router just wraps the engine, persists the run to SQLite, and exposes
the results to the UI.

Backtests are executed synchronously inside the request handler because
the Agent-historical-decision flavour finishes in well under a second
(it's just SQLite queries + pandas math). The DB schema already
supports ``pending`` / ``running`` statuses if we later add background-
task variants (e.g. re-run Agents on historical snapshots).
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException

from .. import database as db
from ..models import BacktestRunRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


def _serialise_metrics(metrics) -> dict:
    """dataclasses.asdict but with the inf → null conversion needed for JSON."""
    raw = dataclasses.asdict(metrics)
    out = {}
    for k, v in raw.items():
        if isinstance(v, float) and (v != v or v == float("inf") or v == float("-inf")):
            out[k] = None
        else:
            out[k] = v
    return out


@router.get("/sources")
async def sources():
    """Available signal sources + their config schema hints.

    Returned to the UI so the page can render the right form for each
    source. Phase 1 ships memory_log only; later phases add rule and
    live_agent.
    """
    return {
        "items": [
            {
                "key": "memory_log",
                "label": "决策回放(Decision Replay)",
                "tagline": "复盘 Agent 已有决策 · 真信号 · 零 LLM 成本",
                "description": (
                    "回放你已经跑过的 Agent 分析作为信号:每条历史 BUY/SELL 决策"
                    "都被当作一笔虚拟下单,按当时建议的方向在次日开盘成交,"
                    "然后用真实的历史价格走出来净值曲线。"
                    "\n\n"
                    "区别于传统回测重新生成信号的做法 — 我们不让 Agent 在历史日期上"
                    "重跑(那会引入未来信息泄漏),而是诚实地用「当时 Agent 给的什么"
                    "结论」作输入。\n\n"
                    "因为只查 SQLite + 跑数学,毫秒级完成,无 LLM 调用。"
                ),
                "available": True,
            },
            {
                "key": "rule",
                "label": "规则回测(Rule Backtest)",
                "tagline": "MA / MACD / RSI / 突破 — 经典量化基准",
                "description": (
                    "用确定性的技术指标规则产生信号(均线交叉、RSI 超买超卖、"
                    "MACD 金叉死叉等),作为 Agent 决策的对照基准。"
                    "这是 vnpy / 同花顺 / 通达信里常见的回测形式。"
                    "\n\nPhase 2 规划中。"
                ),
                "available": False,
            },
            {
                "key": "live_agent",
                "label": "Agent 重跑(Live Replay)",
                "tagline": "在历史日期上重新调用 Agent — 高成本,有泄漏风险",
                "description": (
                    "对每个历史日期重新调用 Agent 推理,产生信号。"
                    "理论上能在没有历史决策时也跑出回测,但有两个严重缺点:"
                    "1) 每次都要花 LLM 调用费,长窗口非常贵;"
                    "2) Agent 拿到的不是当时的实时新闻 / 舆情,"
                    "而是今天能查到的当时附近的信息 — 有未来信息泄漏风险。"
                    "\n\nPhase 3 待评估(可能不实现)。"
                ),
                "available": False,
            },
        ]
    }


@router.get("/universe")
async def universe():
    """Discover what tickers and date ranges are available locally.

    Used by the UI to pre-fill the filters when the user hits the page,
    so they don't have to guess what's in their decision log.
    """
    from tradingagents.backtesting.signals import (
        discover_available_tickers,
        discover_date_range,
    )

    loop = asyncio.get_running_loop()
    tickers = await loop.run_in_executor(None, discover_available_tickers)
    min_date, max_date = await loop.run_in_executor(None, discover_date_range)
    return {
        "tickers": tickers,
        "min_date": min_date,
        "max_date": max_date,
    }


@router.get("")
async def list_runs(limit: int = 50):
    loop = asyncio.get_running_loop()
    rows = await loop.run_in_executor(None, db.list_backtest_runs, limit)
    # Inline-decode metrics_json so the UI can render the list without
    # an extra per-row fetch.
    for r in rows:
        if r.get("metrics_json"):
            try:
                r["metrics"] = json.loads(r["metrics_json"])
            except Exception:
                r["metrics"] = None
        else:
            r["metrics"] = None
        r.pop("metrics_json", None)
    return {"items": rows, "total": len(rows)}


@router.post("")
async def run_backtest(req: BacktestRunRequest):
    """Create + execute a backtest. Returns the finished run row."""
    # Validate dates.
    try:
        start_dt = datetime.strptime(req.start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(req.end_date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="dates must be YYYY-MM-DD")
    if end_dt <= start_dt:
        raise HTTPException(status_code=400, detail="end_date must be after start_date")
    if req.signal_source != "memory_log":
        raise HTTPException(
            status_code=400,
            detail=f"signal_source '{req.signal_source}' not implemented yet",
        )

    loop = asyncio.get_running_loop()
    name = req.name or f"回测 {start_dt:%Y%m%d}-{end_dt:%Y%m%d}"
    sizing_config = {
        "fixed_cash_per_signal": req.fixed_cash_per_signal,
        "strict_sell_only": req.strict_sell_only,
    }
    row = await loop.run_in_executor(
        None,
        lambda: db.create_backtest_run(
            name=name,
            signal_source=req.signal_source,
            source_config=req.source_config or {},
            tickers=req.tickers,
            benchmark=req.benchmark,
            start_date=req.start_date,
            end_date=req.end_date,
            initial_cash=req.initial_cash,
            sizing_mode=req.sizing_mode,
            sizing_config=sizing_config,
            confidence_floor=req.confidence_floor,
        ),
    )
    run_id = row["id"]

    try:
        await loop.run_in_executor(None, lambda: db.update_backtest_status(run_id, status="running"))
        result = await loop.run_in_executor(
            None, _run_engine, req, start_dt, end_dt,
        )
        # Persist trades + nav + metrics.
        await loop.run_in_executor(None, db.insert_backtest_trades, run_id, result.trades)
        await loop.run_in_executor(None, db.insert_backtest_nav, run_id, result.nav_curve)
        metrics_dict = _serialise_metrics(result.metrics)
        await loop.run_in_executor(
            None,
            lambda: db.update_backtest_status(
                run_id,
                status="complete",
                metrics_json=json.dumps(metrics_dict),
                warnings_text="\n".join(result.warnings) if result.warnings else None,
                final_cash=result.final_cash,
                final_total=result.final_total,
            ),
        )
        finished = await loop.run_in_executor(None, db.get_backtest_run, run_id)
        finished["metrics"] = metrics_dict
        finished.pop("metrics_json", None)
        return finished
    except Exception as e:
        logger.exception("Backtest %s failed", run_id)
        await loop.run_in_executor(
            None,
            lambda: db.update_backtest_status(
                run_id, status="failed", error_msg=str(e),
            ),
        )
        raise HTTPException(status_code=500, detail=f"backtest failed: {e}")


def _run_engine(req: BacktestRunRequest, start_dt: datetime, end_dt: datetime):
    """Build the engine, hook in the signal source, run, return result.

    Lives at module level so it can run cleanly in the default executor
    (asyncio's run_in_executor doesn't pickle closures across processes,
    but ThreadPoolExecutor is fine — kept top-level for readability).
    """
    from tradingagents.backtesting import BacktestConfig, BacktestEngine
    from tradingagents.backtesting.signals import MemoryLogSignalSource

    cfg = BacktestConfig(
        start_date=start_dt,
        end_date=end_dt,
        initial_cash=req.initial_cash,
        tickers=req.tickers,
        benchmark=req.benchmark,
        sizing_mode=req.sizing_mode,
        fixed_cash_per_signal=req.fixed_cash_per_signal,
        strict_sell_only=req.strict_sell_only,
        confidence_floor=req.confidence_floor,
    )
    src_kwargs = req.source_config or {}
    source = MemoryLogSignalSource(**src_kwargs)
    engine = BacktestEngine(cfg, source)
    return engine.run()


@router.get("/{run_id}")
async def get_run(run_id: int):
    loop = asyncio.get_running_loop()
    row = await loop.run_in_executor(None, db.get_backtest_run, run_id)
    if not row:
        raise HTTPException(status_code=404, detail="run not found")
    if row.get("metrics_json"):
        try:
            row["metrics"] = json.loads(row["metrics_json"])
        except Exception:
            row["metrics"] = None
    else:
        row["metrics"] = None
    row.pop("metrics_json", None)
    return row


@router.get("/{run_id}/curve")
async def get_curve(run_id: int):
    """NAV snapshots + benchmark curve for chart rendering."""
    loop = asyncio.get_running_loop()
    rows = await loop.run_in_executor(None, db.get_backtest_nav, run_id)
    return {"items": rows, "total": len(rows)}


@router.get("/{run_id}/trades")
async def get_trades(run_id: int):
    loop = asyncio.get_running_loop()
    rows = await loop.run_in_executor(None, db.get_backtest_trades, run_id)
    # Drop the big metadata blob from the list endpoint; UI can click through.
    for r in rows:
        if r.get("metadata_json"):
            try:
                r["metadata"] = json.loads(r["metadata_json"])
            except Exception:
                r["metadata"] = None
        r.pop("metadata_json", None)
    return {"items": rows, "total": len(rows)}


@router.delete("/{run_id}")
async def delete_run(run_id: int):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, db.delete_backtest_run, run_id)
    return {"ok": True}
