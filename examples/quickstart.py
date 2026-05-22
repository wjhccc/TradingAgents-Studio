"""Minimal programmatic-use example for TradingAgents-Studio.

Runs a single analysis end-to-end using whatever LLM provider you've
configured via environment variables (see .env.example). All provider
choice / model / debate-rounds settings come from DEFAULT_CONFIG, which
already applies TRADINGAGENTS_* env-var overrides — so you can switch
provider purely via .env without editing this file.

Override individual keys here only when you want a hard-coded value
that should ignore the environment.
"""

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

config = DEFAULT_CONFIG.copy()

ta = TradingAgentsGraph(debug=True, config=config)

# US ticker — swap in "600519" or "贵州茅台" for A-share examples;
# A-share tickers are auto-routed through AKShare → Tushare → yfinance.
_, decision = ta.propagate("NVDA", "2026-01-15")
print(decision)

# Memorize mistakes and reflect on realised returns
# ta.reflect_and_remember(1000)  # parameter is the position return
