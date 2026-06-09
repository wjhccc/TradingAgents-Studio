# TradingAgents/graph/setup.py
#
# Modified by TradingAgents-Studio contributors (2026) — see CHANGELOG.md
# Original: github.com/TauricResearch/TradingAgents (Apache License 2.0)

from typing import Any, Callable, Dict, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from tradingagents.agents import *
from tradingagents.agents.utils.agent_states import AgentState

from .analyst_execution import build_analyst_execution_plan
from .analyst_runner import run_analyst_loop
from .conditional_logic import ConditionalLogic


class GraphSetup:
    """Handles the setup and configuration of the agent graph."""

    def __init__(
        self,
        quick_thinking_llm: Any,
        deep_thinking_llm: Any,
        tool_nodes: Dict[str, ToolNode],
        conditional_logic: ConditionalLogic,
        analyst_concurrency_limit: int = 1,
        config: Optional[Dict] = None,
        on_analyst_done: Optional[Callable[[str, str], None]] = None,
    ):
        """Initialize with required components.

        ``on_analyst_done(report_key, report_text)`` — optional callback fired
        from a worker thread as each analyst finishes inside the parallel
        barrier, so the web layer can stream per-analyst completion in real time
        instead of all-at-once when the barrier node returns.
        """
        self.quick_thinking_llm = quick_thinking_llm
        self.deep_thinking_llm = deep_thinking_llm
        self.tool_nodes = tool_nodes
        self.conditional_logic = conditional_logic
        self.analyst_concurrency_limit = analyst_concurrency_limit
        self.config = config or {}
        self.on_analyst_done = on_analyst_done

    def setup_graph(
        self, selected_analysts=["market", "social", "news", "fundamentals"]
    ):
        """Set up and compile the agent workflow graph.

        Args:
            selected_analysts (list): List of analyst types to include. Options are:
                - "market": Market analyst
                - "social": Social media analyst (StockTwits + Reddit)
                - "news": News analyst
                - "fundamentals": Fundamentals analyst
                - "cn_social": CN A-share social sentiment analyst (东方财富股吧 + MediaCrawler)
                - "event": Event-driven analyst (causal chain from international events)
        """
        plan = build_analyst_execution_plan(
            selected_analysts,
            concurrency_limit=self.analyst_concurrency_limit,
        )

        analyst_factories = {
            "market": lambda: create_market_analyst(self.quick_thinking_llm),
            "social": lambda: create_sentiment_analyst(self.quick_thinking_llm),
            "news": lambda: create_news_analyst(self.quick_thinking_llm),
            "fundamentals": lambda: create_fundamentals_analyst(self.quick_thinking_llm),
            "cn_social": lambda: create_cn_sentiment_analyst(
                self.quick_thinking_llm,
                cn_sentiment_config=self.config.get("cn_sentiment_config"),
            ),
            "event": lambda: create_event_analyst(self.quick_thinking_llm),
            "capital_flow": lambda: create_capital_flow_analyst(self.quick_thinking_llm),
            "macro": lambda: create_macro_analyst(self.quick_thinking_llm),
        }

        # Create researcher and manager nodes
        bull_researcher_node = create_bull_researcher(self.quick_thinking_llm)
        bear_researcher_node = create_bear_researcher(self.quick_thinking_llm)
        research_manager_node = create_research_manager(self.deep_thinking_llm)
        trader_node = create_trader(self.quick_thinking_llm)

        # Create risk analysis nodes
        aggressive_analyst = create_aggressive_debator(self.quick_thinking_llm)
        neutral_analyst = create_neutral_debator(self.quick_thinking_llm)
        conservative_analyst = create_conservative_debator(self.quick_thinking_llm)
        portfolio_manager_node = create_portfolio_manager(self.deep_thinking_llm)

        # Create workflow
        workflow = StateGraph(AgentState)

        # ── Analysts: one parallel barrier node ──────────────────────────────
        # Instead of chaining analysts (serial) we run them all concurrently
        # inside a single node. Each analyst's ReAct loop runs over its own
        # isolated message list (see analyst_runner), so there's no shared-state
        # contention and no need for the old per-analyst tool / Msg-Clear nodes.
        # LangGraph's synchronous executor does NOT parallelize sibling nodes
        # within a step, so we own the concurrency here via a ThreadPoolExecutor.
        analyst_nodes = {
            spec.key: analyst_factories[spec.key]() for spec in plan.specs
        }
        max_recur = self.config.get("max_recur_limit", 100)
        # 0/None → no cap → run every analyst at once.
        limit = self.analyst_concurrency_limit or len(plan.specs)
        max_workers = max(1, min(len(plan.specs), limit))

        def run_analysts(state):
            def _run(spec):
                return spec, run_analyst_loop(
                    analyst_nodes[spec.key],
                    self.tool_nodes[spec.key],
                    base_state=state,
                    report_key=spec.report_key,
                    max_iterations=max_recur,
                )

            merged: Dict[str, Any] = {}
            with ThreadPoolExecutor(
                max_workers=max_workers, thread_name_prefix="ta-analyst"
            ) as ex:
                futures = [ex.submit(_run, spec) for spec in plan.specs]
                for fut in as_completed(futures):
                    spec, result = fut.result()
                    merged.update(result)
                    # Stream this analyst's completion as soon as it lands, so
                    # the UI shows analysts finishing one by one rather than all
                    # at the barrier. Fired from a worker thread — the callback
                    # must be thread-safe (graph_runner._enqueue_from_thread is).
                    if self.on_analyst_done:
                        self.on_analyst_done(
                            spec.report_key, result.get(spec.report_key, "")
                        )
            return merged

        workflow.add_node("Analysts", run_analysts)

        # Add other nodes
        workflow.add_node("Bull Researcher", bull_researcher_node)
        workflow.add_node("Bear Researcher", bear_researcher_node)
        workflow.add_node("Research Manager", research_manager_node)
        workflow.add_node("Trader", trader_node)
        workflow.add_node("Aggressive Analyst", aggressive_analyst)
        workflow.add_node("Neutral Analyst", neutral_analyst)
        workflow.add_node("Conservative Analyst", conservative_analyst)
        workflow.add_node("Portfolio Manager", portfolio_manager_node)

        # Define edges: analysts run in parallel, then converge on the debate.
        workflow.add_edge(START, "Analysts")
        workflow.add_edge("Analysts", "Bull Researcher")

        # Add remaining edges
        workflow.add_conditional_edges(
            "Bull Researcher",
            self.conditional_logic.should_continue_debate,
            {
                "Bear Researcher": "Bear Researcher",
                "Research Manager": "Research Manager",
            },
        )
        workflow.add_conditional_edges(
            "Bear Researcher",
            self.conditional_logic.should_continue_debate,
            {
                "Bull Researcher": "Bull Researcher",
                "Research Manager": "Research Manager",
            },
        )
        workflow.add_edge("Research Manager", "Trader")
        workflow.add_edge("Trader", "Aggressive Analyst")
        workflow.add_conditional_edges(
            "Aggressive Analyst",
            self.conditional_logic.should_continue_risk_analysis,
            {
                "Conservative Analyst": "Conservative Analyst",
                "Portfolio Manager": "Portfolio Manager",
            },
        )
        workflow.add_conditional_edges(
            "Conservative Analyst",
            self.conditional_logic.should_continue_risk_analysis,
            {
                "Neutral Analyst": "Neutral Analyst",
                "Portfolio Manager": "Portfolio Manager",
            },
        )
        workflow.add_conditional_edges(
            "Neutral Analyst",
            self.conditional_logic.should_continue_risk_analysis,
            {
                "Aggressive Analyst": "Aggressive Analyst",
                "Portfolio Manager": "Portfolio Manager",
            },
        )

        workflow.add_edge("Portfolio Manager", END)

        return workflow
