"""Process-wide LLM concurrency gate + rate-limit backoff.

The analysis graph runs many LLM calls concurrently — analysts now fan out
inside a single graph step (see ``tradingagents/graph/setup.py``), and several
graphs run at once when multiple tickers are analysed together (the web
``heavy_executor`` pool). Without a shared ceiling, that burst of concurrent
requests trips the provider's RPM/TPM limit and every call starts 429'ing.

So every LLM ``_generate`` is funnelled through one process-wide semaphore.
It is a *threading* semaphore (not asyncio) because the graph runs
synchronously inside a worker thread, and the analyst fan-out uses a
``ThreadPoolExecutor`` — the calls we need to cap are on threads, not the
event loop. The single semaphore therefore bounds *total* in-flight LLM calls
across all analysts of all concurrent graphs, which is exactly the global
throttle the two-level concurrency (multi-ticker × multi-analyst) needs.

Tunable via ``TRADINGAGENTS_LLM_CONCURRENCY`` (default 16). On a rate-limit
error the call retries with exponential backoff + jitter, and crucially the
backoff sleep happens *outside* the semaphore so a retrying call doesn't hold
a slot while it waits.
"""

from __future__ import annotations

import logging
import os
import random
import threading
import time
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

# Default chosen for a mid-tier commercial key; override for stricter/looser
# quotas. Bounded so an erroneous double-release can't inflate the ceiling.
_DEFAULT_CONCURRENCY = max(1, int(os.getenv("TRADINGAGENTS_LLM_CONCURRENCY", "16")))
_MAX_RETRIES = max(0, int(os.getenv("TRADINGAGENTS_LLM_MAX_RETRIES", "5")))

_semaphore = threading.BoundedSemaphore(_DEFAULT_CONCURRENCY)


def get_llm_semaphore() -> threading.BoundedSemaphore:
    """Return the process-wide LLM concurrency gate."""
    return _semaphore


def _is_rate_limit(exc: Exception) -> bool:
    """Best-effort detection of a provider rate-limit (HTTP 429) error.

    Covers the openai / anthropic / google SDK exception shapes: an explicit
    ``status_code`` (or nested ``response.status_code``) of 429, or the
    substring fallback for SDKs that only surface it in the message.
    """
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    if status == 429:
        return True
    text = str(exc).lower()
    return "rate limit" in text or "429" in text or "too many requests" in text


def call_with_throttle(fn: Callable[..., _T], *args, **kwargs) -> _T:
    """Run ``fn`` under the global LLM semaphore with 429 backoff.

    The semaphore is held only for the duration of the actual call; on a
    rate-limit error we release it, sleep with exponential backoff + jitter,
    then re-acquire. Non-rate-limit errors propagate immediately.
    """
    attempt = 0
    while True:
        with _semaphore:
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 — re-raised unless 429 & retriable
                if not _is_rate_limit(exc) or attempt >= _MAX_RETRIES:
                    raise
        # Backoff outside the semaphore so we don't pin a slot while waiting.
        delay = min(60.0, (2 ** attempt) + random.uniform(0, 1))
        logger.warning(
            "LLM rate-limited (attempt %d/%d); backing off %.1fs",
            attempt + 1, _MAX_RETRIES, delay,
        )
        time.sleep(delay)
        attempt += 1


class ThrottledLLMMixin:
    """Mixin funnelling LangChain chat ``_generate`` through the global gate.

    Wrapping ``_generate`` (rather than ``invoke``) is deliberate: tool-calling
    runs through ``bind_tools(...)`` which returns a RunnableBinding whose
    ``invoke`` bypasses the subclass ``invoke`` override but still calls the
    underlying ``_generate``. Wrapping here covers both the direct ``.invoke``
    path and the tool-calling path. ``run_manager`` is forwarded untouched so
    callback-based token tracking keeps working.
    """

    def _generate(self, *args, **kwargs):  # type: ignore[override]
        return call_with_throttle(super()._generate, *args, **kwargs)
