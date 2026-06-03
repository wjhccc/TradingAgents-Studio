"""Stock screening ("选股") subsystem.

A two-stage funnel that sits *upstream* of the heavy per-ticker
multi-agent analysis: scan the whole A-share market with deterministic
tools (``universe`` + ``factors``), then let an LLM compile the strategy
and rank/explain a bounded shortlist (``tradingagents.agents.stock_screener``).

The golden rule: every number (price / PE / PB / change / capital flow)
comes from the data tools here. The LLM only picks the strategy, ranks,
and explains — it never invents market data.
"""
