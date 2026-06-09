"""Run a single analyst's ReAct loop in isolation, off the shared graph state.

The serial graph wired analysts in a chain, each one's tool-calling loop
separated from the next by a "Msg Clear" node that wiped the shared
``messages`` channel. That serialization existed *only* to keep one analyst's
tool messages from leaking into the next — analysts have no real data
dependency on each other (each writes its own ``*_report`` key, and every
downstream node reads only those report keys, never ``messages``).

This runner makes the isolation explicit instead of topological: it drives one
analyst's ReAct loop over a *local* message list that never touches the parent
graph state. With messages isolated per analyst, the parent graph can run all
analysts concurrently in a single step (see ``graph/setup.py``) — only their
report keys are merged back.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict

from langchain_core.messages import HumanMessage

logger = logging.getLogger(__name__)


def run_analyst_loop(
    analyst_node: Callable[[Dict[str, Any]], Dict[str, Any]],
    tool_node: Any,
    *,
    base_state: Dict[str, Any],
    report_key: str,
    max_iterations: int = 100,
) -> Dict[str, Any]:
    """Drive one analyst's ReAct loop and return only ``{report_key: text}``.

    ``analyst_node`` is the node fn from ``create_*_analyst(llm)``; it reads
    ``state["messages"]`` and returns ``{"messages": [ai], report_key: text}``.
    ``tool_node`` is the matching langgraph ToolNode (an empty one for the
    pre-fetch analysts, whose first turn never produces tool calls).

    The loop runs over a *local* copy of ``base_state`` whose ``messages`` start
    fresh — exactly what each analyst saw in the serial design after a Msg Clear
    (a single ``HumanMessage("Continue")``). Tool calls are executed directly
    via ``tool_node.tools_by_name`` so this works without a langgraph runtime
    context (a bare ``ToolNode.invoke`` requires one). Messages are never
    written back to the parent graph.
    """
    local_state = dict(base_state)
    local_state["messages"] = [HumanMessage(content="Continue")]

    tools_by_name = getattr(tool_node, "tools_by_name", {}) or {}
    report = ""

    for _ in range(max(1, max_iterations)):
        result = analyst_node(local_state)
        ai = result["messages"][-1]
        local_state["messages"] = local_state["messages"] + [ai]

        produced = result.get(report_key) or ""
        if produced:
            report = produced

        tool_calls = getattr(ai, "tool_calls", None) or []
        if not tool_calls:
            # No tool calls → loop is done (mirrors should_continue_* → clear).
            break

        # Execute each requested tool, appending its ToolMessage. Direct
        # tools_by_name dispatch avoids ToolNode's graph-runtime requirement
        # and keeps tool_call_id pairing intact.
        for call in tool_calls:
            tool = tools_by_name.get(call["name"])
            if tool is None:
                logger.warning(
                    "Analyst %s requested unknown tool %s; skipping",
                    report_key, call.get("name"),
                )
                continue
            local_state["messages"] = local_state["messages"] + [tool.invoke(call)]

    return {report_key: report}
