"""Dedicated executor + concurrency gate for heavy, multi-minute jobs.

The multi-agent analysis graph and the screener snapshot fetch each occupy a
worker thread for seconds-to-minutes. If they ran on asyncio's *default*
executor — which every quick DB call in the backend also reaches via
``run_in_executor(None, ...)`` — a handful of concurrent analyses would pin
every worker and starve the request path, hanging list/get/delete across all
endpoints (observed: a batch-analyze froze the whole API).

So heavy work runs on its own bounded pool, fully isolated from the default
one that serves request-path DB calls. ``analysis_semaphore`` further caps how
many analyses run at once, so a batch-analyze of many tickers queues instead
of flooding — and queued runs stay ``pending`` until a slot frees, which keeps
the history status column honest.

Both limits are env-tunable:
  * ``TRADINGAGENTS_HEAVY_WORKERS``          (default 4) — pool size.
  * ``TRADINGAGENTS_MAX_CONCURRENT_ANALYSES`` (default 3) — concurrent graphs.
"""

from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

HEAVY_MAX_WORKERS = max(1, int(os.getenv("TRADINGAGENTS_HEAVY_WORKERS", "4")))
MAX_CONCURRENT_ANALYSES = max(
    1, int(os.getenv("TRADINGAGENTS_MAX_CONCURRENT_ANALYSES", "3"))
)

# Heavy jobs (analysis graph, screener snapshot) submit here — NOT the default
# executor that request-path DB calls use.
heavy_executor = ThreadPoolExecutor(
    max_workers=HEAVY_MAX_WORKERS, thread_name_prefix="ta-heavy"
)

# Created lazily on first use so it binds to whatever loop is running, instead
# of one captured at import time.
_analysis_semaphore: Optional[asyncio.Semaphore] = None


def analysis_semaphore() -> asyncio.Semaphore:
    """Process-wide gate capping concurrent analysis graph runs."""
    global _analysis_semaphore
    if _analysis_semaphore is None:
        _analysis_semaphore = asyncio.Semaphore(MAX_CONCURRENT_ANALYSES)
    return _analysis_semaphore


def shutdown() -> None:
    """Stop the heavy pool — called on app shutdown so reloads don't leak
    threads. Doesn't wait on in-flight runs (they'd block a fast restart)."""
    heavy_executor.shutdown(wait=False, cancel_futures=True)
