import os

import uvicorn


def main():
    # Host/port/reload are env-overridable so the same entrypoint works for
    # local dev (defaults: 127.0.0.1, autoreload on) and containers, where we
    # need 0.0.0.0 to be reachable through a published port and reload off.
    # docker-compose sets TRADINGAGENTS_WEB_HOST=0.0.0.0 and
    # TRADINGAGENTS_WEB_RELOAD=0.
    host = os.getenv("TRADINGAGENTS_WEB_HOST", "127.0.0.1")
    port = int(os.getenv("TRADINGAGENTS_WEB_PORT", "8000"))
    reload = os.getenv("TRADINGAGENTS_WEB_RELOAD", "1").lower() in ("1", "true", "yes")
    uvicorn.run(
        "web.backend.main:app",
        host=host,
        port=port,
        reload=reload,
    )


if __name__ == "__main__":
    main()
