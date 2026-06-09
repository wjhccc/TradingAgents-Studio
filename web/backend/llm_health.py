"""Cheap pre-flight check that an LLM client can be built for the current config.

The analysis pipeline hard-requires an LLM (unlike the screener, which degrades
to rules). Without this gate, kicking off analysis with no API key creates a row
and a task that immediately fails deep in the graph — the user sees a job appear
and then die. Calling this first lets the entry points reject with a clear 400.

Constructing the client validates credentials (e.g. a missing OPENAI_API_KEY
raises here) WITHOUT making a network call, so it's safe to run on the request
path. It won't catch a present-but-wrong key — that still surfaces at run time.
"""

from __future__ import annotations

from typing import Optional


def check_llm_ready() -> Optional[str]:
    """Return None if an LLM client can be constructed, else a short error."""
    try:
        from .routers.settings import get_effective_config
        from tradingagents.llm_clients import create_llm_client

        cfg = get_effective_config()
        client = create_llm_client(
            provider=cfg["llm_provider"],
            model=cfg.get("quick_think_llm") or cfg.get("deep_think_llm"),
            base_url=cfg.get("backend_url"),
        )
        client.get_llm()
        return None
    except Exception as e:  # noqa: BLE001 — any construction failure means not-ready
        return str(e)
