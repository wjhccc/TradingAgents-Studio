import asyncio
import uuid

from fastapi import APIRouter, HTTPException

from ..models import AnalyzeRequest, NLQueryRequest
from .. import database as db
from ..graph_runner import GraphRunner, build_config
from .settings import get_effective_config

router = APIRouter(prefix="/api", tags=["analyze"])

# In-memory map of analysis_id → asyncio.Queue for active runs
_active_queues: dict[str, asyncio.Queue] = {}


@router.post("/parse-query")
async def parse_nl_query(req: NLQueryRequest):
    """Parse a natural-language analyze request like "研究茅台短期".

    The rule layer is always tried first (deterministic, no API cost). If
    ``use_llm_fallback=true`` and the rules can't pin down a ticker, the
    server invokes ``quick_think_llm`` to extract a structured response.
    Returns a ``ParsedQuery`` dict the frontend can use to prefill the
    analyze form.
    """
    from tradingagents.utils.nl_query_parser import parse_query

    llm = None
    if req.use_llm_fallback:
        try:
            from tradingagents.llm_clients import create_llm_client
            config = get_effective_config()
            client = create_llm_client(
                provider=config["llm_provider"],
                model=config["quick_think_llm"],
                base_url=config.get("backend_url"),
            )
            llm = client.get_llm()
        except Exception as e:
            # If LLM init fails (e.g. missing key) we still return rule results
            # rather than 500'ing — the UI shows confidence + notes so the
            # user can see we degraded gracefully.
            return {
                "result": parse_query(req.text).to_dict(),
                "llm_error": str(e),
            }

    loop = asyncio.get_running_loop()
    # parse_query may call into the LLM (sync I/O) — run in executor.
    result = await loop.run_in_executor(None, lambda: parse_query(req.text, llm=llm))
    return {"result": result.to_dict()}


@router.post("/analyze")
async def start_analysis(req: AnalyzeRequest):
    loop = asyncio.get_running_loop()

    # Analysis needs an LLM with no fallback — fail fast with a clear message
    # instead of creating a row that dies deep in the graph on a missing key.
    from ..llm_health import check_llm_ready
    err = await loop.run_in_executor(None, check_llm_ready)
    if err:
        raise HTTPException(status_code=400, detail=f"LLM 未就绪，无法启动分析：{err}")

    analysis_id = str(uuid.uuid4())
    config = build_config(req)

    await loop.run_in_executor(None, lambda: db.create_analysis(
        id=analysis_id,
        ticker=req.ticker,
        trade_date=req.trade_date,
        asset_type=req.asset_type,
        analysts=req.analysts,
        config=config,
    ))

    queue = asyncio.Queue()
    _active_queues[analysis_id] = queue

    runner = GraphRunner(analysis_id, config, req.analysts, queue)
    asyncio.create_task(_run_and_cleanup(analysis_id, runner))

    return {"id": analysis_id, "status": "pending"}


async def _run_and_cleanup(analysis_id: str, runner: GraphRunner):
    try:
        await runner.run()
    finally:
        await asyncio.sleep(5)
        _active_queues.pop(analysis_id, None)


@router.get("/analyze/{analysis_id}/status")
async def get_status(analysis_id: str):
    loop = asyncio.get_running_loop()
    analysis = await loop.run_in_executor(None, db.get_analysis, analysis_id)
    if not analysis:
        raise HTTPException(status_code=404, detail="not found")
    return {"id": analysis_id, "status": analysis["status"], "signal": analysis.get("signal")}

