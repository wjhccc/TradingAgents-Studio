import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# Trigger the NO_PROXY bootstrap BEFORE anything else imports requests —
# otherwise a globally-set HTTP_PROXY will intercept our A-share fetches.
import tradingagents.dataflows  # noqa: F401  side-effect import

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from .database import init_db, fail_stale_runs
from .executors import shutdown as shutdown_heavy_executor
from .routers import analyze, history, dashboard, settings, holdings, schedule, paper, quote, backtest, screen, quality
from .scheduler import service as scheduler_service

# Keep the log file inside the writable ~/.tradingagents home (override with
# TRADINGAGENTS_LOG_DIR). The previous Path(__file__).parent.parent.parent
# location resolved into site-packages under a pip-installed/Docker layout,
# which the non-root runtime user cannot write to — crashing the server on
# import with PermissionError.
_LOG_DIR = Path(
    os.getenv("TRADINGAGENTS_LOG_DIR", Path.home() / ".tradingagents")
)
_LOG_HANDLERS = [logging.StreamHandler(sys.stdout)]
try:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    _LOG_HANDLERS.insert(0, logging.FileHandler(str(_LOG_DIR / "web_server.log"), encoding="utf-8"))
except OSError as exc:  # pragma: no cover - degrade to stdout-only logging
    print(f"[warn] file logging disabled ({_LOG_DIR}): {exc}", file=sys.stderr)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=_LOG_HANDLERS,
)
logger = logging.getLogger("tradingagents.web")


@asynccontextmanager
async def lifespan(app):
    logger.info("Server starting up, initializing database...")
    init_db()
    # Reconcile runs left 'pending'/'running' by a previous crash/reload so the
    # UI doesn't show ghost rows the user can't delete.
    stale = fail_stale_runs()
    if stale:
        logger.info("Reconciled %d interrupted run(s) to failed.", stale)
    await scheduler_service.start()
    logger.info("Database initialized. Server ready.")
    yield
    logger.info("Server shutting down.")
    await scheduler_service.stop()
    shutdown_heavy_executor()


app = FastAPI(title="TradingAgents Web", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(analyze.router)
app.include_router(history.router)
app.include_router(dashboard.router)
app.include_router(settings.router)
app.include_router(holdings.router)
app.include_router(schedule.router)
app.include_router(paper.router)
app.include_router(quote.router)
app.include_router(backtest.router)
app.include_router(screen.router)
app.include_router(quality.router)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.websocket("/ws/analyze/{analysis_id}")
async def ws_analyze(websocket: WebSocket, analysis_id: str):
    await websocket.accept()
    from .routers.analyze import _active_queues
    from . import database as db

    queue = _active_queues.get(analysis_id)
    if not queue:
        events = db.get_agent_events(analysis_id)
        for ev in events:
            await websocket.send_json(ev)
        await websocket.close()
        return

    try:
        while True:
            event = await queue.get()
            await websocket.send_json(event)
            if event.get("type") in ("analysis_complete", "error"):
                break
    except WebSocketDisconnect:
        pass


@app.websocket("/ws/screen/{run_id}")
async def ws_screen(websocket: WebSocket, run_id: str):
    await websocket.accept()
    from .routers.screen import _active_queues
    from . import database as db

    queue = _active_queues.get(run_id)
    if not queue:
        # Run already finished (or unknown) — replay the stored result so a
        # late-connecting client still gets the candidates, then close.
        run = db.get_screen_run(run_id)
        if run:
            await websocket.send_json({
                "type": "screen_complete" if run["status"] == "complete" else run["status"],
                "agent": "screener",
                "strategy": run.get("strategy"),
                "candidates": run.get("candidates", []),
            })
        await websocket.close()
        return

    try:
        while True:
            event = await queue.get()
            await websocket.send_json(event)
            if event.get("type") in ("screen_complete", "error"):
                break
    except WebSocketDisconnect:
        pass


# Serve frontend static files if built
_STATIC_DIR = Path(__file__).parent.parent / "frontend" / "dist"
if _STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(_STATIC_DIR / "assets")), name="static")

    @app.get("/{path:path}")
    async def spa_fallback(path: str):
        if path.startswith("api") or path.startswith("ws"):
            raise HTTPException(status_code=404)
        file_path = _STATIC_DIR / path
        if file_path.is_file():
            return FileResponse(str(file_path))
        return FileResponse(str(_STATIC_DIR / "index.html"))
