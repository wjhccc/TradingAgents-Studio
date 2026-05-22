"""Natural-language query parser: "研究茅台短期" → structured analyze request.

Two-stage pipeline:
  1. Rule-based: regex + the existing STOCK_CODE_MAPPING from eastmoney_guba.
     Fast, free, deterministic — handles 80% of common phrasings.
  2. LLM fallback: when rules can't pin down a ticker, ask quick_think_llm
     for a JSON response. Optional — only triggers when an LLM is provided.

Returns a ``ParsedQuery`` with ``source`` indicating which path produced it,
so the UI can surface "rule-based" vs "ai-assisted" provenance.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Optional

from tradingagents.dataflows.eastmoney_guba import STOCK_CODE_MAPPING

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Common nicknames that aren't in eastmoney_guba.STOCK_CODE_MAPPING.
# Users almost always say "茅台" / "宁王" / "迈瑞", not the full registered
# name — bridge those colloquial forms back to the canonical entries.
# ---------------------------------------------------------------------------
_NICKNAMES: dict[str, str] = {
    "茅台": "贵州茅台",
    "宁王": "宁德时代",
    "宁德": "宁德时代",
    "比亚迪": "比亚迪",
    "迈瑞": "迈瑞医疗",
    "恒瑞": "恒瑞医药",
    "海康": "海康威视",
    "海尔": "海尔智家",
    "格力": "格力电器",
    "美的": "美的集团",
    "京东方": "京东方A",
    "中芯": "中芯国际",
    "立讯": "立讯精密",
    "牧原": "牧原股份",
    "温氏": "温氏股份",
    "汾酒": "山西汾酒",
    "五粮液": "五粮液",
    "泸州老窖": "泸州老窖",
}


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

# Period keyword → look-back days. These match common Chinese trading parlance:
# 短期 = a week-ish swing; 中期 = a month; 长期 = a quarter.
_PERIOD_KEYWORDS: dict[str, int] = {
    "超短": 3, "超短期": 3, "极短": 3,
    "短线": 5, "短期": 5, "近期": 5, "本周": 5,
    "中期": 20, "中线": 20, "中长期": 30,
    "长期": 60, "长线": 60, "战略": 90,
}
_DEFAULT_PERIOD_DAYS = 20

# Action verbs (we drop these to isolate the entity name)
_ACTION_WORDS = (
    "分析", "研究", "看看", "看一下", "查一下", "查看", "评估", "估值", "诊断",
    "帮我", "帮忙", "我想", "想看", "麻烦", "请", "一下", "看下", "瞅瞅",
)

# Exchange suffix patterns we keep verbatim (already a complete ticker)
_TICKER_SUFFIX_RE = re.compile(
    r"\b\d{1,6}\.(SS|SH|SZ|HK|T|L|TO|AX|NS|BO)\b", re.IGNORECASE,
)
# 6-digit A-share code as a standalone token
_ASHARE_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
# US-style alphabetic ticker (2-5 caps) — guard against false positives by
# requiring it to be space-separated and not a common English stopword
_US_TICKER_RE = re.compile(r"\b([A-Z]{2,5})\b")
_US_TICKER_STOPWORDS = {"BUY", "SELL", "HOLD", "PE", "PB", "EPS", "USD", "RMB", "GDP", "ETF", "IPO", "CEO", "ANA", "LL", "AI"}

# Relative date phrases → offset in days from "today" (server-local)
_RELATIVE_DATE_RE = re.compile(
    r"(?P<offset>今天|今日|昨天|昨日|前天|前日|大前天|上周|上个月)"
)
_REL_OFFSETS = {
    "今天": 0, "今日": 0,
    "昨天": -1, "昨日": -1,
    "前天": -2, "前日": -2,
    "大前天": -3,
    "上周": -7, "上个月": -30,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@dataclass
class ParsedQuery:
    """Structured intent extracted from a free-text query."""
    ticker: str = ""
    company_name: str = ""        # human-readable name when we resolved one
    trade_date: str = ""          # YYYY-MM-DD; empty if no date hint
    period_days: int = _DEFAULT_PERIOD_DAYS
    period_label: str = ""        # e.g. "短期"; empty if we used default
    confidence: float = 0.0       # 0..1; rule hits get high confidence
    source: str = "rule"          # "rule" | "llm" | "rule+llm"
    notes: str = ""               # short explanation for the UI

    def to_dict(self) -> dict:
        return asdict(self)


def parse_query(text: str, *, llm=None, today: Optional[str] = None) -> ParsedQuery:
    """Parse a NL query into a ParsedQuery. Returns confidence=0 on total failure.

    ``today`` lets tests override the reference date; in production it
    defaults to ``datetime.now()``.
    """
    if not text or not text.strip():
        return ParsedQuery(source="rule", confidence=0.0, notes="空输入")

    today_dt = (
        datetime.strptime(today, "%Y-%m-%d") if today else datetime.now()
    )
    cleaned = _strip_action_words(text)

    rule = _parse_with_rules(cleaned, today_dt)
    if rule.ticker and rule.confidence >= 0.6:
        return rule

    if llm is not None:
        try:
            llm_result = _parse_with_llm(text, llm, today_dt)
            if llm_result.ticker:
                # If rules found a partial match (e.g. period but not ticker),
                # mark provenance as combined so the UI can show "AI 补全".
                if rule.ticker or rule.period_label:
                    llm_result.source = "rule+llm"
                return llm_result
        except Exception as e:
            logger.warning("LLM fallback failed: %s", e)

    # Return whatever the rule found, even if low confidence — UI can
    # decide whether to show a warning.
    return rule


# ---------------------------------------------------------------------------
# Rule-based pipeline
# ---------------------------------------------------------------------------

def _strip_action_words(text: str) -> str:
    """Remove leading politeness/verb fluff to make name extraction easier."""
    s = text.strip()
    for w in sorted(_ACTION_WORDS, key=len, reverse=True):
        s = s.replace(w, " ")
    return re.sub(r"\s+", " ", s).strip()


def _parse_with_rules(text: str, today_dt: datetime) -> ParsedQuery:
    pq = ParsedQuery(source="rule")
    confidence = 0.0
    notes: list[str] = []

    # --- Ticker / name resolution ---
    # Priority: explicit suffix → 6-digit code → known company name → US ticker
    m = _TICKER_SUFFIX_RE.search(text)
    if m:
        pq.ticker = m.group(0).upper()
        confidence = max(confidence, 0.95)
        notes.append(f"识别到完整代码 {pq.ticker}")
    else:
        m6 = _ASHARE_CODE_RE.search(text)
        if m6:
            code = m6.group(1)
            pq.ticker = code  # A-share router auto-resolves SH/SZ
            confidence = max(confidence, 0.9)
            notes.append(f"识别到 6 位 A 股代码 {code}")
        else:
            # Match company names from the STOCK_CODE_MAPPING dict (longest first).
            name_match = _match_company_name(text)
            if name_match:
                name, code = name_match
                pq.company_name = name
                pq.ticker = code
                confidence = max(confidence, 0.85)
                notes.append(f"识别到「{name}」→ {code}")
            else:
                us = _match_us_ticker(text)
                if us:
                    pq.ticker = us
                    confidence = max(confidence, 0.55)
                    notes.append(f"猜测美股代码 {us}（置信度偏低）")

    # --- Period extraction ---
    period_days, period_label = _extract_period(text)
    if period_label:
        pq.period_days = period_days
        pq.period_label = period_label
        confidence = max(confidence, min(confidence + 0.05, 1.0))
        notes.append(f"周期：{period_label}（{period_days} 个交易日）")
    else:
        # Try "N天/N周/N月" numeric form
        m = re.search(r"(\d{1,3})\s*(天|个交易日|日)", text)
        if m:
            pq.period_days = int(m.group(1))
            pq.period_label = f"{pq.period_days}天"
            notes.append(f"周期：{pq.period_days} 天")
        else:
            m = re.search(r"(\d{1,2})\s*周", text)
            if m:
                pq.period_days = int(m.group(1)) * 5
                pq.period_label = f"{m.group(1)}周"
                notes.append(f"周期：{m.group(1)} 周 → {pq.period_days} 天")
            else:
                m = re.search(r"(\d{1,2})\s*个?\s*月", text)
                if m:
                    pq.period_days = int(m.group(1)) * 20
                    pq.period_label = f"{m.group(1)}月"
                    notes.append(f"周期：{m.group(1)} 月 → {pq.period_days} 天")

    # --- Trade date extraction ---
    pq.trade_date = _extract_trade_date(text, today_dt)
    if pq.trade_date:
        notes.append(f"分析日期：{pq.trade_date}")
    else:
        pq.trade_date = today_dt.strftime("%Y-%m-%d")
        notes.append(f"分析日期：默认今日 {pq.trade_date}")

    pq.confidence = round(confidence, 2)
    pq.notes = "；".join(notes)
    return pq


def _match_company_name(text: str) -> Optional[tuple[str, str]]:
    """Longest-prefix match against the known stock-name → code dictionary.

    Tries nicknames first (they're typically shorter and more colloquial,
    e.g. "茅台" instead of "贵州茅台"), then the full eastmoney_guba dict.
    """
    # Nicknames: longest first to prefer "泸州老窖" over "酒" if both existed.
    nicks = sorted(_NICKNAMES.keys(), key=len, reverse=True)
    for nick in nicks:
        if nick in text:
            canonical = _NICKNAMES[nick]
            code = STOCK_CODE_MAPPING.get(canonical, "")
            if code:
                return canonical, code

    names = sorted(STOCK_CODE_MAPPING.keys(), key=len, reverse=True)
    for name in names:
        if name in text:
            return name, STOCK_CODE_MAPPING[name]
    return None


def _match_us_ticker(text: str) -> Optional[str]:
    """Match an uppercase 2-5 letter token that isn't a common English word."""
    # Only consider the text in its original casing — Chinese queries
    # rarely contain capitalised English noise, so a hit here is signal.
    for m in _US_TICKER_RE.finditer(text):
        token = m.group(1)
        if token in _US_TICKER_STOPWORDS:
            continue
        return token
    return None


def _extract_period(text: str) -> tuple[int, str]:
    """Match period keywords longest-first; return (days, matched_label)."""
    keywords = sorted(_PERIOD_KEYWORDS.keys(), key=len, reverse=True)
    for kw in keywords:
        if kw in text:
            return _PERIOD_KEYWORDS[kw], kw
    return _DEFAULT_PERIOD_DAYS, ""


def _extract_trade_date(text: str, today_dt: datetime) -> str:
    """Pull a trade date from the query, supporting:
       - explicit YYYY-MM-DD or YYYY/MM/DD
       - relative phrases (今天/昨天/前天/上周/上个月)
       - month/day shorthand (5月18, 5-18, 5/18) — assumes current year
    """
    m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
    if m:
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass

    m = _RELATIVE_DATE_RE.search(text)
    if m:
        offset = _REL_OFFSETS.get(m.group("offset"), 0)
        return (today_dt + timedelta(days=offset)).strftime("%Y-%m-%d")

    m = re.search(r"(\d{1,2})[-/月](\d{1,2})[日号]?", text)
    if m:
        try:
            dt = datetime(today_dt.year, int(m.group(1)), int(m.group(2)))
            # If the parsed date is in the future relative to today, assume
            # they meant last year (e.g. "12月3" said in January).
            if dt > today_dt:
                dt = dt.replace(year=today_dt.year - 1)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass

    return ""


# ---------------------------------------------------------------------------
# LLM fallback
# ---------------------------------------------------------------------------

_LLM_PROMPT = """你是一个金融查询解析器。将用户的自然语言查询解析为 JSON。

用户查询: {query}
今日日期: {today}

请只返回严格的 JSON 对象，包含以下字段（缺失字段返回空字符串或 0）：
{{
  "ticker": "股票代码（A股6位数字代码 / 美股大写字母 / 港股带.HK / 日股带.T）",
  "company_name": "公司中文/英文全称",
  "trade_date": "分析日期 YYYY-MM-DD，未指定则空字符串",
  "period_days": 整数，时间窗口的交易日数，短期=5/中期=20/长期=60，未指定默认 20,
  "period_label": "原始时间词，如 短期/中期/长期/30天",
  "confidence": 0.0 到 1.0 之间的浮点数
}}

只返回 JSON，不要任何额外说明文字。"""


def _parse_with_llm(text: str, llm, today_dt: datetime) -> ParsedQuery:
    prompt = _LLM_PROMPT.format(query=text, today=today_dt.strftime("%Y-%m-%d"))
    response = llm.invoke(prompt)
    content = getattr(response, "content", str(response)).strip()

    # The LLM may wrap JSON in code fences — strip them.
    if content.startswith("```"):
        content = re.sub(r"^```[a-zA-Z]*\n", "", content)
        content = re.sub(r"\n```$", "", content)
    # Strip any leading/trailing prose
    m = re.search(r"\{.*\}", content, re.DOTALL)
    if m:
        content = m.group(0)

    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM returned non-JSON: {content[:200]}") from e

    pq = ParsedQuery(
        ticker=str(data.get("ticker") or "").strip(),
        company_name=str(data.get("company_name") or "").strip(),
        trade_date=str(data.get("trade_date") or "").strip() or today_dt.strftime("%Y-%m-%d"),
        period_days=int(data.get("period_days") or _DEFAULT_PERIOD_DAYS),
        period_label=str(data.get("period_label") or "").strip(),
        confidence=float(data.get("confidence") or 0.5),
        source="llm",
        notes=f"由 LLM 解析（{data.get('company_name', '')}）",
    )
    return pq


__all__ = ["ParsedQuery", "parse_query"]
