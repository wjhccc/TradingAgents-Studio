"""A股中文舆情统一数据层 - CN Sentiment Data Layer

整合方案一（东方财富股吧）+ 方案二（MediaCrawler）+ 中文情感分析，
输出格式化字符串供 CN Social Sentiment Analyst 使用。

MIT 协议，无 GPL 风险。

数据源优先级：
1. 东方财富股吧（方案一）—— 最直接、数据最精准
2. MediaCrawler 微博（方案二）—— 覆盖面广
3. MediaCrawler 小红书（方案二）—— 年轻投资者
"""

from __future__ import annotations
import logging
from typing import Optional, Dict, List

from tradingagents.dataflows.event_intelligence import get_stock_code as _get_stock_code

logger = logging.getLogger(__name__)

__all__ = [
    "fetch_cn_sentiment_data",
    "analyze_cn_sentiment",
]

# ---------------------------------------------------------------------------
# 情感关键词（降级用）
# ---------------------------------------------------------------------------

_BULLISH_KEYWORDS = [
    "买入", "加仓", "满仓", "涨停", "看多", "抄底", "牛市",
    "上涨", "突破", "新高", "看好", "做多", "趋势", "金叉",
    "低吸", "布局", "暴拉", "主升", "反弹", "护盘", "格局",
    "低估", "价值", "业绩", "超预期", "分红", "回购",
]

_BEARISH_KEYWORDS = [
    "卖出", "清仓", "止损", "跌停", "看空", "割肉", "熊市",
    "下跌", "破位", "新低", "崩盘", "出货", "跑路", "利空",
    "减持", "套牢", "回撤", "死叉", "逃顶", "风险", "高估",
    "业绩雷", "黑天鹅", "踩雷", "爆雷", "造假", "ST", "退市",
]


def _keyword_sentiment(text: str) -> tuple[str, float]:
    """基于关键词的情感分析（降级方案）。"""
    text_lower = text.lower()
    b_count = sum(1 for k in _BULLISH_KEYWORDS if k in text_lower)
    r_count = sum(1 for k in _BEARISH_KEYWORDS if k in text_lower)
    if b_count > r_count:
        return "正面", min(0.5 + b_count * 0.1, 0.9)
    elif r_count > b_count:
        return "负面", min(0.5 + r_count * 0.1, 0.9)
    return "中性", 0.5


def analyze_cn_sentiment(texts: List[str]) -> Dict[str, int]:
    """
    对一批文本进行中文情感分析。

    Args:
        texts: 文本列表

    Returns:
        {"正面": count, "负面": count, "中性": count, "total": count}
    """
    stats = {"正面": 0, "负面": 0, "中性": 0, "total": 0}
    for text in texts:
        label, _ = _keyword_sentiment(text)
        stats[label] += 1
        stats["total"] += 1
    return stats


# ---------------------------------------------------------------------------
# 主入口函数
# ---------------------------------------------------------------------------

def fetch_cn_sentiment_data(
    ticker: str,
    stock_code: Optional[str] = None,
    config: Optional[dict] = None,
    limit: int = 50,
) -> Dict[str, str]:
    """
    获取A股中文舆情数据（方案一 + 方案二）。

    Args:
        ticker: 股票名称（如 "贵州茅台"）
        stock_code: 股票代码（如 "600519"），None时自动推断
        config: 配置字典，含 eastmoney 和 mediacrawler 配置
        limit: 各数据源的返回数量

    Returns:
        {
            "eastmoney": "<东方财富股吧数据字符串>",
            "weibo": "<微博舆情数据字符串>",
            "xhs": "<小红书舆情数据字符串>",
            "combined": "<合并后的综合数据字符串>",
        }
    """
    cfg = config or {}
    guba_cfg = cfg.get("eastmoney_guba", {})
    mc_cfg = cfg.get("mediacrawler", {})
    results: Dict[str, str] = {}

    # ---- 方案一：东方财富股吧 ----
    if guba_cfg.get("enabled", True):
        try:
            from .eastmoney_guba import fetch_eastmoney_guba_sentiment
            code = stock_code or _get_stock_code(ticker)
            results["eastmoney"] = fetch_eastmoney_guba_sentiment(
                ticker=ticker,
                stock_code=code,
                limit=limit,
            )
        except Exception as e:
            logger.warning("东方财富股吧数据获取失败: %s", e)
            results["eastmoney"] = f"<东方财富股吧数据获取失败: {e}>"
    else:
        results["eastmoney"] = "<东方财富股吧已禁用>"

    # ---- 方案二：MediaCrawler 各平台 ----
    platforms = mc_cfg.get("platforms", ["weibo", "xhs"])
    db_config = mc_cfg.get("db_config", None)

    for platform in platforms:
        try:
            from .mediacrawler_wrapper import query_mediacrawler_sentiment
            results[platform] = query_mediacrawler_sentiment(
                keyword=stock_code or ticker,
                platform=platform,
                hours_back=mc_cfg.get("hours_back", 168),
                limit=limit,
                db_config=db_config,
            )
        except Exception as e:
            logger.warning("MediaCrawler %s 平台数据获取失败: %s", platform, e)
            results[platform] = f"<MediaCrawler {platform} 数据获取失败: {e}>"

    # ---- 综合报告 ----
    results["combined"] = _build_combined_report(ticker, results)

    return results


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _build_combined_report(ticker: str, sources: Dict[str, str], trade_date: str = "") -> str:
    """构建综合舆情报告。"""
    date_str = f" — {trade_date}" if trade_date else ""
    report = f"# A股中文舆情综合报告 — {ticker}{date_str}\n\n"

    # 收集所有文本片段
    all_texts: List[str] = []
    for source_name, content in sources.items():
        if source_name == "combined":
            continue
        if content.startswith("<") or "未找到" in content or "不可用" in content:
            continue
        for line in content.split("\n"):
            if len(line) > 10 and not line.startswith("#") and not line.startswith("|"):
                all_texts.append(line.strip())

    # 情感统计
    sentiment_stats = analyze_cn_sentiment(all_texts)
    total = sentiment_stats.get("total", 0)

    if total > 0:
        report += "## 情感分析统计\n\n"
        report += "| 情感 | 数量 | 占比 |\n|------|------|------|\n"
        for label in ["正面", "负面", "中性"]:
            count = sentiment_stats.get(label, 0)
            pct = 100 * count / total
            emoji = "📈" if label == "正面" else ("📉" if label == "负面" else "💬")
            report += f"| {emoji} {label} | {count} | {pct:.1f}% |\n"
        report += "\n"

    # 各数据源摘要
    report += "## 各数据源摘要\n\n"
    source_labels = {
        "eastmoney": "东方财富股吧",
        "weibo": "微博",
        "xhs": "小红书",
        "douyin": "抖音",
        "kuaishou": "快手",
        "bili": "B站",
    }
    for source_name, content in sources.items():
        if source_name == "combined":
            continue
        label = source_labels.get(source_name, source_name)
        if content.startswith("<"):
            report += f"### {label}: {content}\n\n"
        else:
            # 不截断，完整传给LLM
            report += f"### {label}\n{content}\n\n"

    return report
