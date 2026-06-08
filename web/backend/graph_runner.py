import asyncio
import logging
import re
from datetime import datetime
from typing import Dict, Any, Optional

from tradingagents.graph.trading_graph import TradingAgentsGraph

from . import database as db
from .executors import heavy_executor, analysis_semaphore

logger = logging.getLogger(__name__)

# Node name → friendly agent name mapping
_NODE_AGENT_MAP = {
    "Market Analyst": "market_analyst",
    "Sentiment Analyst": "sentiment_analyst",
    "News Analyst": "news_analyst",
    "Fundamentals Analyst": "fundamentals_analyst",
    "CN Sentiment Analyst": "cn_sentiment_analyst",
    "Event Analyst": "event_analyst",
    "Capital Flow Analyst": "capital_flow_analyst",
    "Macro Analyst": "macro_analyst",
    "Bull Researcher": "bull_researcher",
    "Bear Researcher": "bear_researcher",
    "Research Manager": "research_manager",
    "Trader": "trader",
    "Aggressive Analyst": "aggressive_analyst",
    "Conservative Analyst": "conservative_analyst",
    "Neutral Analyst": "neutral_analyst",
    "Portfolio Manager": "portfolio_manager",
}

# State keys that hold report content
_REPORT_KEYS = {
    "market_report": ("market_analyst", "market"),
    "sentiment_report": ("sentiment_analyst", "sentiment"),
    "news_report": ("news_analyst", "news"),
    "fundamentals_report": ("fundamentals_analyst", "fundamentals"),
    "cn_sentiment_report": ("cn_sentiment_analyst", "cn_sentiment"),
    "event_impact_report": ("event_analyst", "event"),
    "capital_flow_report": ("capital_flow_analyst", "capital_flow"),
    "macro_report": ("macro_analyst", "macro"),
}

# Map graph stream chunk keys to agent names for progress tracking.
# investment_debate_state / risk_debate_state are intentionally absent here —
# they're handled by _emit_debate_turns which inspects current_response to
# distinguish bull vs bear (and aggressive vs conservative vs neutral).
_STATE_KEY_AGENT = {
    "market_report": "market_analyst",
    "sentiment_report": "sentiment_analyst",
    "news_report": "news_analyst",
    "fundamentals_report": "fundamentals_analyst",
    "cn_sentiment_report": "cn_sentiment_analyst",
    "event_impact_report": "event_analyst",
    "capital_flow_report": "capital_flow_analyst",
    "macro_report": "macro_analyst",
    "trader_investment_plan": "trader",
    "final_trade_decision": "portfolio_manager",
}

# Prefix → (role tag for the frontend, friendly agent name for the timeline)
_INVEST_ROLE_MAP = {
    "Bull Analyst:": ("bull", "bull_researcher"),
    "Bear Analyst:": ("bear", "bear_researcher"),
}
_RISK_ROLE_MAP = {
    "Aggressive Analyst:": ("aggressive", "aggressive_analyst"),
    "Conservative Analyst:": ("conservative", "conservative_analyst"),
    "Neutral Analyst:": ("neutral", "neutral_analyst"),
}


class GraphRunner:
    """Wraps TradingAgentsGraph and emits events to an asyncio.Queue for WebSocket streaming."""

    def __init__(self, analysis_id: str, config: dict, selected_analysts: list,
                 queue: asyncio.Queue):
        self.analysis_id = analysis_id
        self.config = config
        self.selected_analysts = selected_analysts
        self.queue = queue
        # Per-side turn counters so the frontend can label "Bull · Round 2".
        self._invest_rounds: Dict[str, int] = {}
        self._risk_rounds: Dict[str, int] = {}
        # Last current_response we already pushed, to avoid duplicate emits when
        # the same chunk arrives twice (graph.stream("values") republishes the
        # merged state after every node, including non-debate nodes).
        self._last_invest_response: str = ""
        self._last_risk_response: str = ""
        # Analysts now finish inside one parallel barrier node and stream their
        # completion individually (via on_analyst_done). The barrier's merged
        # chunk would otherwise re-emit every analyst; track which we've already
        # surfaced so each agent_complete fires exactly once.
        self._emitted_agents: set = set()

    async def _emit(self, event_type: str, agent: str, content: str = "",
                    tokens: int = 0, extra: Optional[dict] = None):
        event = {
            "type": event_type,
            "agent": agent,
            "content": content,
            "stats": {"tokens": tokens},
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
        if extra:
            event.update(extra)
        await self.queue.put(event)
        db.add_agent_event(self.analysis_id, agent, event_type, content, tokens)

    def _enqueue_from_thread(self, event: dict):
        """Thread-safe enqueue used from the executor thread that runs the graph."""
        self._loop.call_soon_threadsafe(self.queue.put_nowait, event)
        # DB write is sync — fine to call from the executor thread directly.
        db.add_agent_event(
            self.analysis_id,
            event.get("agent", ""),
            event.get("type", ""),
            event.get("content", ""),
            event.get("stats", {}).get("tokens", 0),
        )

    def _run_sync(self) -> tuple:
        """Run the graph synchronously (called in executor thread)."""
        graph = TradingAgentsGraph(
            selected_analysts=self.selected_analysts,
            debug=True,
            config=self.config,
            on_node_complete=self._on_node,
        )
        return graph.propagate(
            self.config["_ticker"],
            self.config["_trade_date"],
        )

    def _on_node(self, state_keys: list, chunk: Optional[dict] = None):
        """Called from the sync thread when a graph node completes.

        Emits two kinds of events:
          - ``agent_complete`` for analyst / trader / portfolio_manager nodes
          - ``debate_turn`` for each new bull/bear/aggressive/conservative/neutral
            turn, parsed out of investment_debate_state / risk_debate_state so
            the UI can render a live dialogue bubble per round.
        """
        if chunk is None:
            chunk = {}

        # Plain analyst / trader / pm completion events
        seen_agents = set()
        for key in state_keys:
            agent = _NODE_AGENT_MAP.get(key) or _STATE_KEY_AGENT.get(key)
            if agent and agent not in seen_agents and agent not in self._emitted_agents:
                seen_agents.add(agent)
                self._emitted_agents.add(agent)
                self._enqueue_from_thread({
                    "type": "agent_complete",
                    "agent": agent,
                    "content": f"{key} 完成",
                    "stats": {"tokens": 0},
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                })

        # Per-turn debate emission
        if "investment_debate_state" in chunk:
            self._emit_invest_turn(chunk.get("investment_debate_state") or {})
        if "risk_debate_state" in chunk:
            self._emit_risk_turn(chunk.get("risk_debate_state") or {})

    def _emit_invest_turn(self, debate_state: dict):
        """Emit a debate_turn event for a fresh bull/bear response, if any."""
        current = (debate_state.get("current_response") or "").strip()
        if not current or current == self._last_invest_response:
            return
        self._last_invest_response = current

        role_info = self._match_role(current, _INVEST_ROLE_MAP)
        if not role_info:
            return
        role, agent_name, body = role_info
        self._invest_rounds[role] = self._invest_rounds.get(role, 0) + 1
        round_num = self._invest_rounds[role]

        self._enqueue_from_thread({
            "type": "debate_turn",
            "agent": agent_name,
            "content": body,
            "stats": {"tokens": 0},
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "debate": "invest",
            "role": role,
            "round": round_num,
        })

    def _emit_risk_turn(self, debate_state: dict):
        """Emit a debate_turn for whichever risk speaker just spoke."""
        latest_speaker = (debate_state.get("latest_speaker") or "").lower()
        speaker_to_response_key = {
            "aggressive": "current_aggressive_response",
            "conservative": "current_conservative_response",
            "neutral": "current_neutral_response",
        }
        response_key = speaker_to_response_key.get(latest_speaker)
        if not response_key:
            return

        current = (debate_state.get(response_key) or "").strip()
        if not current or current == self._last_risk_response:
            return
        self._last_risk_response = current

        role_info = self._match_role(current, _RISK_ROLE_MAP)
        if not role_info:
            return
        role, agent_name, body = role_info
        self._risk_rounds[role] = self._risk_rounds.get(role, 0) + 1
        round_num = self._risk_rounds[role]

        self._enqueue_from_thread({
            "type": "debate_turn",
            "agent": agent_name,
            "content": body,
            "stats": {"tokens": 0},
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "debate": "risk",
            "role": role,
            "round": round_num,
        })

    @staticmethod
    def _match_role(text: str, role_map: dict):
        """Return (role, agent_name, body_without_prefix) or None."""
        for prefix, (role, agent_name) in role_map.items():
            if text.startswith(prefix):
                body = text[len(prefix):].lstrip()
                return role, agent_name, body
        return None

    async def run(self) -> Optional[tuple]:
        """Run analysis, streaming node events via the queue.

        Gated by ``analysis_semaphore`` and executed on the dedicated
        ``heavy_executor`` so concurrent/batch runs queue here instead of
        pinning the default executor and freezing the request path. A queued
        run stays ``pending`` until a slot frees.
        """
        self._loop = asyncio.get_running_loop()
        async with analysis_semaphore():
            return await self._run_guarded()

    async def _run_guarded(self) -> Optional[tuple]:
        ticker = self.config["_ticker"]
        trade_date = self.config["_trade_date"]

        db.update_analysis_status(self.analysis_id, "running")
        await self._emit("agent_start", "system", f"Starting analysis for {ticker} on {trade_date}")

        try:
            final_state, signal = await self._loop.run_in_executor(heavy_executor, self._run_sync)

            # Extract and store reports
            for key, (agent_name, report_type) in _REPORT_KEYS.items():
                content = final_state.get(key, "")
                if content:
                    db.add_agent_report(self.analysis_id, agent_name, report_type, content)

            # Store debate histories
            invest_state = final_state.get("investment_debate_state", {})
            if invest_state.get("bull_history"):
                db.add_agent_report(self.analysis_id, "bull_researcher", "bull_debate", invest_state["bull_history"])
            if invest_state.get("bear_history"):
                db.add_agent_report(self.analysis_id, "bear_researcher", "bear_debate", invest_state["bear_history"])
            if invest_state.get("judge_decision"):
                db.add_agent_report(self.analysis_id, "research_manager", "research_plan", invest_state["judge_decision"])

            # Trader plan
            if final_state.get("trader_investment_plan"):
                db.add_agent_report(self.analysis_id, "trader", "trader_proposal", final_state["trader_investment_plan"])

            # Risk debate
            risk_state = final_state.get("risk_debate_state", {})
            if risk_state.get("history"):
                db.add_agent_report(self.analysis_id, "risk_debate", "risk_debate", risk_state["history"])

            # Final decision
            final_decision = final_state.get("final_trade_decision", "")
            if final_decision:
                db.add_agent_report(self.analysis_id, "portfolio_manager", "final_decision", final_decision)

            # Parse confidence from signal
            confidence = _extract_confidence(signal)

            # Determine signal direction
            signal_direction = _extract_signal_direction(signal)

            db.update_analysis_status(
                self.analysis_id, "complete",
                signal=signal_direction,
                confidence=confidence,
                final_decision=final_decision,
            )
            await self._emit("analysis_complete", "system", signal)
            return final_state, signal

        except Exception as e:
            logger.exception("Analysis %s failed", self.analysis_id)
            db.update_analysis_status(self.analysis_id, "failed", error_msg=str(e))
            await self._emit("error", "system", str(e))
            return None


def _extract_confidence(signal: str) -> Optional[float]:
    match = re.search(r"confidence[:\s]*(\d+(?:\.\d+)?)", signal, re.IGNORECASE)
    if match:
        return float(match.group(1))
    return None


def _extract_signal_direction(signal: str) -> str:
    upper = signal.upper() if signal else ""
    if "BUY" in upper or "BULLISH" in upper:
        return "BUY"
    if "SELL" in upper or "BEARISH" in upper:
        return "SELL"
    return "HOLD"


def build_config(req) -> dict:
    """Build a config dict from an AnalyzeRequest, merging with effective config.

    Base is ``get_effective_config()`` — DEFAULT_CONFIG plus the in-memory
    overrides the settings page applies at runtime — NOT a bare
    ``DEFAULT_CONFIG.copy()``. DEFAULT_CONFIG is a snapshot frozen at process
    import, so it misses a provider/model the user switched to after startup;
    using it here meant the LLM pre-flight (which reads the effective config)
    passed on deepseek while the graph was built on stale openai and failed.
    Explicit request fields still win over both.
    """
    from .routers.settings import get_effective_config

    config = dict(get_effective_config())
    if req.llm_provider:
        config["llm_provider"] = req.llm_provider
    if req.deep_think_llm:
        config["deep_think_llm"] = req.deep_think_llm
    if req.quick_think_llm:
        config["quick_think_llm"] = req.quick_think_llm
    # Default to Chinese output unless user explicitly picks another language
    config["output_language"] = req.output_language or "Chinese"
    config["max_debate_rounds"] = req.max_debate_rounds
    config["max_risk_discuss_rounds"] = req.max_risk_discuss_rounds
    config["checkpoint_enabled"] = req.checkpoint_enabled
    # Internal fields for the runner
    config["_ticker"] = req.ticker
    config["_trade_date"] = req.trade_date
    return config
