"""主力资金分析师 — Capital Flow Analyst.

Specialised for A-share. Looks at the four most actionable institutional /
short-term-capital signals in the Chinese market:

- Per-ticker main-force net flow (主力净流入)
- Northbound funds (北向资金 / 沪深港通)
- Margin balance (融资融券余额)
- Top stocks (龙虎榜)

Follows the pre-fetch pattern used by ``cn_sentiment_analyst``: data is
fetched in Python, injected into a single prompt, one LLM call returns
the report. No tool calling — keeps the graph simpler and reduces token
churn.
"""

from datetime import datetime

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.dataflows.capital_flow import fetch_capital_flow_bundle
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_language_instruction,
)


def create_capital_flow_analyst(llm):
    """Factory for the 主力资金分析师 graph node."""

    def capital_flow_analyst_node(state):
        ticker = state["company_of_interest"]
        trade_date = state.get("trade_date", datetime.now().strftime("%Y-%m-%d"))
        instrument_context = build_instrument_context(ticker)

        data = fetch_capital_flow_bundle(ticker)
        system_message = _build_system_message(
            ticker=ticker,
            trade_date=trade_date,
            data=data,
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
            "capital_flow_report": result.content,
        }

    return capital_flow_analyst_node


def _build_system_message(*, ticker: str, trade_date: str, data: dict) -> str:
    return f"""You are an A-share capital-flow analyst. Produce a report on
institutional and short-term capital movement around {ticker} as of {trade_date}.

## Pre-fetched data

{data.get("individual", "<个股资金流不可用>")}

{data.get("northbound", "<北向资金不可用>")}

{data.get("margin", "<两融数据不可用>")}

{data.get("lhb", "<龙虎榜不可用>")}

## What these signals mean in A-share

- **主力净流入**: 大资金当日净买入金额。**连续多日正净流入** = 机构 /
  游资在吸筹;**连续负净流入** = 派发出货。注意净占比比绝对金额更稳定。
- **北向资金**: 外资通过沪深港通进出 A 股。**连续净买入** 往往领先大盘
  上涨,**连续净卖出** 是风险偏好降低的预警。可关注个股是否在北向重仓股
  名单中。
- **融资融券余额**: 反映杠杆水平。**快速上升** = 散户加杠杆追涨,极端
  情况下是过热信号;**快速下降** = 强制平仓 / 风险偏好下降,熊市底部
  常见。
- **龙虎榜**: 当日成交异常的个股名单 + 买卖席位。**特定知名游资席位**
  (如"知名游资"、"机构专用")出现在买方 = 短线题材炒作信号。

## How to analyse

1. **Per-ticker first**: 看个股主力净流入是否近 5 日为正、连续性如何、
   主力 vs 大单/超大单分布。
2. **Market backdrop**: 北向资金近 5-10 日方向 + 两融变化 = 大盘资金面
   底色。个股资金流入要在"大盘资金面流入"的背景下才更可信。
3. **龙虎榜匹配**: 该标的是否上榜?上榜买方是机构还是游资?这决定信号
   的持续性(机构买入 ≈ 中线;游资 ≈ 短线博弈)。
4. **背离检查**: 个股资金净流入 vs 股价方向是否一致?**资金流入但股价
   不涨** 可能是吸筹;**资金流出但股价上涨** 是机构出货的典型形态。
5. **极端值提醒**: 单日净流入/流出超过历史 90 分位需要单独标记。

## Output format

1. **Headline read** — Bullish / Bearish / Neutral / Mixed,一句话总结
   主力资金对该标的的态度。
2. **Per-ticker breakdown** — 数据 + 解读;最近 5 日主力净流入的方向、
   连续性、量级。
3. **Market liquidity backdrop** — 北向资金近期趋势 + 两融余额方向;
   说明这是顺风还是逆风。
4. **龙虎榜信号(如适用)** — 该标的是否上榜;买方席位类型;隐含的资
   金性质。
5. **Risk flags** — 资金面 vs 股价背离、过热/超卖、单日极端值。
6. **Trading implications** — 这些信号对短/中线决策的指引。
7. **Markdown table** — 最末附一个汇总表:信号 / 方向 / 量级 / 证据
   来源。

If any data section is missing ("<...不可用>"), explicitly call out which
parts couldn't be fetched so downstream agents know the report is partial.

{get_language_instruction()}"""
