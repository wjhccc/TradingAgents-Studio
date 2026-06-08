"""Tests for the parallel-analyst runner and the global LLM throttle."""

import threading
import time
import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.prebuilt import ToolNode

from tradingagents.graph.analyst_runner import run_analyst_loop
from tradingagents.llm_clients import throttle


@tool
def _fetch(symbol: str) -> str:
    """Fetch data for a symbol."""
    return f"DATA:{symbol}"


class AnalystRunnerTests(unittest.TestCase):
    def test_prefetch_analyst_runs_once_no_tools(self):
        """A pre-fetch analyst (empty ToolNode) returns its report on turn 1."""
        calls = {"n": 0}

        def node(state):
            calls["n"] += 1
            return {"messages": [AIMessage(content="x")], "macro_report": "MACRO"}

        out = run_analyst_loop(
            node, ToolNode([]),
            base_state={"company_of_interest": "X", "trade_date": "2026-01-01"},
            report_key="macro_report",
        )
        self.assertEqual(out, {"macro_report": "MACRO"})
        self.assertEqual(calls["n"], 1)

    def test_tool_calling_loop_executes_tools_and_pairs_ids(self):
        """Tool calls are executed and their ToolMessages fed back, by id."""
        calls = {"n": 0}

        def node(state):
            calls["n"] += 1
            if calls["n"] == 1:
                ai = AIMessage(
                    content="",
                    tool_calls=[{"name": "_fetch", "args": {"symbol": "AAPL"},
                                 "id": "c1", "type": "tool_call"}],
                )
                return {"messages": [ai], "market_report": ""}
            # Turn 2 must see the ToolMessage from turn 1's call.
            last = state["messages"][-1]
            self.assertEqual(last.content, "DATA:AAPL")
            self.assertEqual(last.tool_call_id, "c1")
            return {"messages": [AIMessage(content="done")], "market_report": "MARKET"}

        out = run_analyst_loop(
            node, ToolNode([_fetch]),
            base_state={"company_of_interest": "X", "trade_date": "2026-01-01"},
            report_key="market_report",
        )
        self.assertEqual(out, {"market_report": "MARKET"})
        self.assertEqual(calls["n"], 2)

    def test_messages_isolated_from_base_state(self):
        """The runner must not mutate or leak into the caller's state messages."""
        base = {"company_of_interest": "X", "trade_date": "2026-01-01",
                "messages": [HumanMessage(content="ORIGINAL")]}

        def node(state):
            # The analyst sees a fresh 'Continue' seed, not ORIGINAL.
            self.assertEqual(state["messages"][0].content, "Continue")
            return {"messages": [AIMessage(content="x")], "news_report": "NEWS"}

        run_analyst_loop(
            node, ToolNode([]), base_state=base, report_key="news_report",
        )
        # Caller's messages untouched.
        self.assertEqual(base["messages"][0].content, "ORIGINAL")

    def test_unknown_tool_is_skipped_not_fatal(self):
        calls = {"n": 0}

        def node(state):
            calls["n"] += 1
            if calls["n"] == 1:
                ai = AIMessage(
                    content="",
                    tool_calls=[{"name": "nonexistent", "args": {},
                                 "id": "c1", "type": "tool_call"}],
                )
                return {"messages": [ai], "market_report": ""}
            return {"messages": [AIMessage(content="done")], "market_report": "OK"}

        out = run_analyst_loop(
            node, ToolNode([_fetch]),
            base_state={"company_of_interest": "X", "trade_date": "2026-01-01"},
            report_key="market_report",
        )
        self.assertEqual(out, {"market_report": "OK"})


class ThrottleTests(unittest.TestCase):
    def test_rate_limit_detection(self):
        class Err429(Exception):
            status_code = 429

        self.assertTrue(throttle._is_rate_limit(Err429("boom")))
        self.assertTrue(throttle._is_rate_limit(Exception("Rate limit reached")))
        self.assertTrue(throttle._is_rate_limit(Exception("HTTP 429 Too Many Requests")))
        self.assertFalse(throttle._is_rate_limit(ValueError("bad request 400")))

    def test_semaphore_caps_concurrency(self):
        cur = {"n": 0, "peak": 0}
        lock = threading.Lock()

        with patch.object(throttle, "_semaphore", threading.BoundedSemaphore(3)):
            def work():
                def fn():
                    with lock:
                        cur["n"] += 1
                        cur["peak"] = max(cur["peak"], cur["n"])
                    time.sleep(0.03)
                    with lock:
                        cur["n"] -= 1
                throttle.call_with_throttle(fn)

            threads = [threading.Thread(target=work) for _ in range(12)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertLessEqual(cur["peak"], 3)

    def test_retries_on_rate_limit_then_succeeds(self):
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise Exception("rate limit")
            return "ok"

        with patch.object(throttle.time, "sleep", lambda s: None):
            result = throttle.call_with_throttle(flaky)
        self.assertEqual(result, "ok")
        self.assertEqual(calls["n"], 3)

    def test_non_rate_limit_error_propagates_immediately(self):
        calls = {"n": 0}

        def boom():
            calls["n"] += 1
            raise ValueError("nope")

        with self.assertRaises(ValueError):
            throttle.call_with_throttle(boom)
        self.assertEqual(calls["n"], 1)


if __name__ == "__main__":
    unittest.main()
