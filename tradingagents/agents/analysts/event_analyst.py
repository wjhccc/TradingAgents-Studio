"""事件驱动分析师 - Event-Driven Analyst (LLM-Driven)

因果链完全由LLM从新闻内容中推理，不再依赖本地字典匹配。
预取新闻 → LLM推理因果链 → 事件影响报告。

MIT license - no GPL dependencies.

Usage:
    from tradingagents.agents.analysts.event_analyst import create_event_analyst

    event_analyst = create_event_analyst(llm)
    result = event_analyst(state)
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Any

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_news,
    get_global_news,
)
from tradingagents.dataflows.event_intelligence import (
    EVENT_ANALYSIS_PROMPT_TEMPLATE,
)
from tradingagents.dataflows.yfinance_news import (
    get_news_yfinance,
    get_global_news_yfinance,
)

logger = logging.getLogger(__name__)


# ============================================================================
# 工具函数
# ============================================================================

def _parse_trade_date(trade_date: str) -> str:
    """确保日期格式为 yyyy-mm-dd。"""
    if not trade_date:
        return datetime.now().strftime("%Y-%m-%d")
    try:
        return datetime.strptime(trade_date, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        return datetime.now().strftime("%Y-%m-%d")


def _seven_days_back(trade_date: str) -> str:
    """计算 trade_date 往前7天的日期。"""
    try:
        return (
            datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=7)
        ).strftime("%Y-%m-%d")
    except ValueError:
        return (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")


def _assemble_news_content(
    news_block: str,
    global_news_block: str,
    yf_news: str,
    yf_global: str,
) -> str:
    """
    将所有新闻来源合并为一个文本，供LLM分析。
    按来源标注，便于LLM判断可信度。
    """
    parts = []

    if news_block and news_block.strip():
        parts.append(f"=== 公司新闻 ===\n{news_block}")

    if yf_news and not yf_news.startswith("Error") and yf_news.strip():
        parts.append(f"=== Yahoo Finance公司新闻 ===\n{yf_news}")

    if global_news_block and global_news_block.strip():
        parts.append(f"=== 全球宏观新闻 ===\n{global_news_block}")

    if yf_global and not yf_global.startswith("Error") and yf_global.strip():
        parts.append(f"=== Yahoo Finance全球新闻 ===\n{yf_global}")

    if not parts:
        return "（无新闻数据）"

    return "\n\n".join(parts)


def _build_system_message(
    *,
    ticker: str,
    start_date: str,
    end_date: str,
    trade_date: str,
    news_content: str,
) -> str:
    """构建 LLM 系统提示词，包含新闻内容和分析指令。"""
    return EVENT_ANALYSIS_PROMPT_TEMPLATE.format(
        trade_date=trade_date,
        start_date=start_date,
        end_date=end_date,
        news_content=news_content,
    )


# ============================================================================
# Agent 工厂函数
# ============================================================================

def create_event_analyst(llm):
    """
    创建事件驱动分析师节点。

    核心流程：
    1. 预取新闻数据（get_news + get_global_news + yfinance，均为直接调用，非tool calling）
    2. 合并所有新闻文本
    3. LLM 从新闻内容中推理因果链 → 生成完整事件影响报告
    4. 返回 event_impact_report

    关键设计：
    - 因果链100%由LLM从新闻推理，不依赖本地字典。
    - 新闻数据直接获取，不依赖 state["news_report"]，避免执行顺序依赖。
    """

    def event_analyst_node(state: Dict[str, Any]) -> Dict[str, Any]:
        ticker = state.get("company_of_interest", "")
        trade_date = _parse_trade_date(state.get("trade_date", ""))
        start_date = _seven_days_back(trade_date)
        instrument_context = build_instrument_context(ticker)

        # 预取多个来源的实时新闻
        news_block = ""
        yf_news = ""
        global_news_block = ""
        yf_global = ""

        try:
            if ticker:
                news_block = get_news.func(ticker, start_date, trade_date)
        except Exception as e:
            logger.warning("get_news failed for %s: %s", ticker, e)

        try:
            if ticker:
                yf_news = get_news_yfinance(ticker, start_date, trade_date)
        except Exception as e:
            logger.warning("get_news_yfinance failed for %s: %s", ticker, e)

        try:
            global_news_block = get_global_news.func(trade_date)
        except Exception as e:
            logger.warning("get_global_news failed: %s", e)

        try:
            yf_global = get_global_news_yfinance(trade_date)
        except Exception as e:
            logger.warning("get_global_news_yfinance failed: %s", e)

        # 合并所有新闻文本
        news_content = _assemble_news_content(
            news_block=news_block,
            global_news_block=global_news_block,
            yf_news=yf_news,
            yf_global=yf_global,
        )

        # 构建系统提示词（新闻 + 因果链分析指令）
        system_message = _build_system_message(
            ticker=ticker,
            start_date=start_date,
            end_date=trade_date,
            trade_date=trade_date,
            news_content=news_content,
        )

        # 单次 LLM 调用，LLM 推理因果链并生成报告
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants. "
                    "If you or any other assistant has the "
                    "FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable, "
                    "prefix your response with "
                    "FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                    "\n{system_message}\n"
                    "For your reference, the current date is {current_date}. "
                    "{instrument_context}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(
            system_message=system_message,
            current_date=trade_date,
            instrument_context=instrument_context,
        )

        chain = prompt | llm
        result = chain.invoke(state["messages"])

        logger.info(
            "Event analyst produced report of length %d for %s",
            len(result.content),
            ticker,
        )

        return {
            "messages": [result],
            "event_impact_report": result.content,
        }

    return event_analyst_node


# ============================================================================
# 向后兼容别名
# ============================================================================

def create_event_intelligence_analyst(llm):
    """create_event_analyst 的别名（已废弃）。"""
    import warnings

    warnings.warn(
        "create_event_intelligence_analyst is deprecated. "
        "Use create_event_analyst instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return create_event_analyst(llm)


__all__ = [
    "create_event_analyst",
    "create_event_intelligence_analyst",
]
