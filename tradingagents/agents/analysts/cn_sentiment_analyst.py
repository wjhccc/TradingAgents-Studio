"""A股中文舆情分析师 - CN Social Sentiment Analyst

新增的 Analyst，专门分析A股的中文舆情数据。
遵循预取模式：预取数据 → 注入 prompt → 单次 LLM 调用 → 报告。

对比原有的 sentiment_analyst（美股）：
- 数据源：东方财富股吧 + 微博/小红书/抖音等（中文平台）
- 情感分析：中文关键词 + 情绪统计
- 关键词语境：中国特有的市场术语（打板/核按钮/龙头/题材等）

使用方式：
与其他 Analyst 一样，在 setup.py 中注册为 "cn_social" 类型，
然后在 selected_analysts 列表中传入即可。

Usage:
    graph = TradingAgentsGraph(
        selected_analysts=["market", "social", "cn_social", "news", "fundamentals"]
    )
"""

from datetime import datetime

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.dataflows.cn_sentiment import fetch_cn_sentiment_data
from tradingagents.dataflows.event_intelligence import get_stock_code
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_language_instruction,
)


# ---------------------------------------------------------------------------
# Agent 工厂函数
# ---------------------------------------------------------------------------

def create_cn_sentiment_analyst(llm, cn_sentiment_config=None):
    """
    创建A股中文舆情分析师节点。

    Args:
        llm: LLM instance.
        cn_sentiment_config: 配置字典，传入用户自定义的 cn_sentiment_config。
    """
    cn_config = cn_sentiment_config or {}

    def cn_sentiment_analyst_node(state):
        ticker = state["company_of_interest"]
        trade_date = state.get("trade_date", datetime.now().strftime("%Y-%m-%d"))
        instrument_context = build_instrument_context(ticker)
        stock_code = get_stock_code(ticker)

        cn_data = fetch_cn_sentiment_data(
            ticker=ticker,
            stock_code=stock_code,
            config=cn_config,
            limit=cn_config.get("eastmoney_guba", {}).get("limit", 50),
        )

        system_message = _build_system_message(
            ticker=ticker,
            stock_code=stock_code,
            trade_date=trade_date,
            cn_data=cn_data,
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL**"
                    " or deliverable, prefix your response with"
                    " FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                    "\n{system_message}\n"
                    "For your reference, the current date is {current_date}."
                    " {instrument_context}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(current_date=trade_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        chain = prompt | llm
        result = chain.invoke(state["messages"])

        return {
            "messages": [result],
            "cn_sentiment_report": result.content,
        }

    return cn_sentiment_analyst_node


# ---------------------------------------------------------------------------
# System Message 构建
# ---------------------------------------------------------------------------

def _build_system_message(
    *,
    ticker: str,
    stock_code: str,
    trade_date: str,
    cn_data: dict,
) -> str:
    """构建注入中文舆情数据的 system message。"""
    return f"""You are a financial market sentiment analyst specializing in the Chinese A-share market.
Your task is to produce a comprehensive CN (China A-share) sentiment report for {ticker} (stock code: {stock_code})
covering the period up to {trade_date}.

## CN A-share Data Sources (pre-fetched)

### 东方财富股吧数据
东方财富股吧是中国A股散户讨论最活跃的平台，类似美股的StockTwits。
<start_eastmoney>
{cn_data.get("eastmoney", "<数据不可用>")}
<end_eastmoney>

### 微博舆情数据
<start_weibo>
{cn_data.get("weibo", "<数据不可用>")}
<end_weibo>

### 小红书舆情数据
<start_xhs>
{cn_data.get("xhs", "<数据不可用>")}
<end_xhs>

### 综合情感分析
<start_combined>
{cn_data.get("combined", "<数据不可用>")}
<end_combined>

## CN A-share Market Context

中国A股有独特的市场术语和现象，在分析时需要注意：
- **打板/涨停板**: A股特有，T+1制度和涨跌停板限制（主板10%，创业板/科创板20%）
- **龙头股**: 板块中涨幅最大的领涨股
- **题材/概念**: 热点主题炒作，如"新能源概念"、"AI概念"、"华为概念"
- **核按钮**: 指当天涨停后次日被大单砸到跌停（极度负面信号）
- **散户情绪**: 东方财富股吧、微博、小红书反映散户情绪，与机构行为可能背离
- **北向资金**: 外资通过沪股通/深股通流入A股的金额，是重要机构情绪指标
- **涨停敢死队**: 专门追涨停板的游资手法
- **次日闷杀**: 头天涨停后次日低开低走，追板者全部被套

## How to Analyze CN A-share Sentiment

1. **关注散户情绪指标**: 股吧帖子数量、阅读量、评论数反映散户关注度。
   极端的看多/看空比例可能是反向指标。

2. **识别题材炒作信号**: 如果某只股票在微博/小红书出现频率突然上升，
   可能是有题材炒作。分析是否有实质基本面支撑。

3. **跨平台交叉验证**:
   - 东方财富股吧 → 传统老股民情绪
   - 微博 → 机构+大V观点
   - 小红书 → 年轻投资者（Z世代）情绪

4. **热度与基本面匹配**: 高热度但无实质利好 = 泡沫风险信号。
   低热度但有利好 = 潜在价值机会。

5. **风险信号识别**:
   - "核按钮"、"割肉"、"清仓" 等词频繁出现 = 恐慌情绪
   - "满仓"、"加杠杆"、"牛市来了" = 过度乐观
   - "业绩雷"、"黑天鹅"、"爆雷" = 基本面恶化信号

6. **注意A股特有风险**:
   - 涨跌停板制度导致流动性风险（想买买不进，想卖卖不出）
   - T+1制度限制日内交易
   - 监管政策对市场情绪影响巨大（证监会公告、IPO政策等）

7. **A+H股溢价**: 如有H股（港股）上市，注意A/H溢价率。
   A股溢价过高可能透支未来上涨空间。

## Output Format

Produce a CN A-share sentiment report covering, in order:

1. **Overall CN Sentiment Direction** — Bullish / Bearish / Neutral / Mixed
   with a confidence note based on data quality and sample size.
2. **Source-by-source breakdown** — what each platform (eastmoney, weibo, xhs) tells you,
   with specific evidence and numbers.
3. **Sentiment distribution** — statistics from the combined analysis (if available).
4. **Key narratives and themes** — what the CN investor community is focused on.
5. **Catalysts and risks** — upcoming events, policy changes, earnings, etc.
6. **Trading implications for A-share market** — how CN retail/institutional sentiment
   should inform trading decisions (with A-share specifics: T+1, limit up/down, etc.)
7. **Markdown table** at the end summarizing key sentiment signals, direction, source, and evidence.

{get_language_instruction()}"""
