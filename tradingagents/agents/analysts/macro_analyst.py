"""宏观分析师 — Macro Analyst.

Covers the top-down picture before the bottom-up analysts dig in:

- China: CPI, PPI, M2, PMI (mfg + non-mfg), LPR, USD/CNY
- US/global: 10-year treasury yield (^TNX)

Same pre-fetch + single-LLM-call pattern as cn_sentiment and capital_flow.
"""

from datetime import datetime

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.dataflows.macro import fetch_macro_bundle
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_language_instruction,
)


def create_macro_analyst(llm):
    """Factory for the 宏观分析师 graph node."""

    def macro_analyst_node(state):
        ticker = state["company_of_interest"]
        trade_date = state.get("trade_date", datetime.now().strftime("%Y-%m-%d"))
        instrument_context = build_instrument_context(ticker)

        data = fetch_macro_bundle()
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
            "macro_report": result.content,
        }

    return macro_analyst_node


def _build_system_message(*, ticker: str, trade_date: str, data: dict) -> str:
    return f"""You are a macroeconomic analyst on a multi-agent trading desk.
Produce a top-down report on the macro backdrop facing {ticker} as of {trade_date}.

## Pre-fetched macro data

{data.get("cpi", "<CPI 不可用>")}

{data.get("ppi", "<PPI 不可用>")}

{data.get("m2", "<货币供应不可用>")}

{data.get("pmi", "<PMI 不可用>")}

{data.get("lpr", "<LPR 不可用>")}

{data.get("usdcny", "<USD/CNY 不可用>")}

{data.get("us_10y", "<10Y 美债不可用>")}

## How each indicator maps to A-share / global markets

- **CPI 同比**: 通胀。**温和上行(2-3%)** 利股市;**过高(>4%)** 引央行
  收紧 → 利空;**通缩(<0%)** 暗示需求疲软 → 也利空。
- **PPI 同比**: 上游工业品价格。PPI 领先 CPI 约 3-6 个月,**PPI 转正**
  通常对应原材料 / 周期股盈利改善。
- **M2 同比**: 货币供应。**M2 增速回升** = 流动性宽松 → 利股市;**M2
  下降** = 紧缩。
- **PMI**: 50 是荣枯线。**>50 持续上升** = 经济扩张 → 顺周期板块受益;
  **<50** = 收缩 → 防御板块占优。新订单分项 > 库存分项是补库存周期信号。
- **LPR**: 央行政策利率。**LPR 下调** = 宽松 → 利好成长股 / 房地产 /
  银行(以及债券);**LPR 上调** = 紧缩。
- **USD/CNY**: 人民币汇率。**人民币贬值** = 出口板块受益、外资可能流出;
  **人民币升值** = 进口板块受益、外资倾向流入。
- **10Y 美债收益率(^TNX)**: 全球无风险利率。**上升** = 全球流动性收紧、
  A 股估值承压(尤其是成长股);**下降** = 风险资产估值修复空间打开。

## How to analyse

1. **Identify the regime**: 增长 + 通胀 4 象限定位 (复苏 / 过热 / 滞胀 /
   衰退)。
2. **Sector implications**: 把宏观状态映射到行业偏好(如复苏 → 顺周期,
   滞胀 → 资源 / 防御,衰退 → 必需消费 + 公用事业)。
3. **Ticker-specific tilt**: 把 {ticker} 所在行业放回宏观背景中评判。
   如果 {ticker} 是出口型 → 关注汇率;如果是高估值成长股 → 关注利率和
   流动性;如果是周期股 → 关注 PMI 和 PPI。
4. **Cross-validation**: 国内 + 全球指标是否同向?如 LPR 下调 + 美债利率
   下行 = 同向利好;LPR 下调但美债收益率飙升 = 流动性宽松但外资逆风,
   需要分情景讨论。
5. **Tail-risk callouts**: 通胀超预期 / PMI 跌破 50 / 人民币突破关键点
   位等异常信号要单独标记。

## Output format

1. **宏观主线** — 一句话总结当前经济 / 流动性 / 政策的方向。
2. **关键指标解读** — 逐项简要解读(CPI / PPI / M2 / PMI / LPR /
   USDCNY / 10Y),给出方向和量级。
3. **行业偏好** — 在当前宏观状态下哪些板块顺风、哪些逆风。
4. **对 {ticker} 的影响** — 把上述行业偏好映射到该标的所在行业,给出
   "宏观顺风 / 中性 / 逆风"的明确判断。
5. **风险提示** — 哪些数据偏离预期会反转上述结论。
6. **Markdown table** — 末尾汇总:指标 / 最新值 / 趋势 / 对 {ticker} 的
   含义。

If any data section is missing ("<...不可用>"), explicitly note which
parts couldn't be fetched.

{get_language_instruction()}"""
