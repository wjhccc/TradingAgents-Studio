"""Stock-screening agent: natural-language goal → compiled strategy → ranked picks.

Two LLM-touch points, both *bounded* (the heavy lifting is the deterministic
``screener.factors`` / ``screener.universe`` layer, which never sees the LLM):

  1. ``compile_strategy(text, llm)`` — NL → ``StrategySpec``. Rule layer first
     (regex/keyword, free, deterministic), LLM only as a fallback/augment, with
     ``source`` provenance — mirrors ``utils.nl_query_parser``.
  2. ``rank_candidates(candidates, goal, llm)`` — the LLM sees the *already
     screened* shortlist (≤ a few dozen rows of real metrics) and writes a
     one-line rationale + risk per pick. It never produces price/PE/etc.

Both degrade gracefully: with ``llm=None`` (or on any LLM error) the rule layer
and a deterministic reason generator carry the run — screening still works.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from tradingagents.screener.factors import DEFAULT_WEIGHTS, StrategySpec

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Rule vocabulary
# ---------------------------------------------------------------------------

# Each entry: keywords → a mutation applied to the spec, plus a provenance
# label. Keywords are matched as substrings against the raw query.
_VALUE_KW = ("低估值", "便宜", "低市盈率", "低市净", "低估", "价值", "低pe", "低pb")
_BREAKNAV_KW = ("破净", "破净股")
_HIGH_DIV_KW = ("高股息", "高分红", "股息率")
_MOMENTUM_KW = ("强势", "上涨", "突破", "动量", "趋势", "走强", "领涨", "热门", "涨幅")
# Oversold / mean-reversion intent — rank the WORST performers first.
_REVERSAL_KW = ("超跌", "抄底", "反弹", "反转", "跌得多", "跌幅", "跌得狠", "低位", "回调到位", "深跌")
_FLOW_KW = ("主力", "资金流入", "净流入", "资金流", "增仓", "吸筹", "流入")

# Momentum look-back period keywords → StrategySpec.momentum_period.
_PERIOD_KW: dict[str, str] = {
    "当日": "today", "今日": "today", "今天": "today",
    "一周": "5d", "近一周": "5d", "本周": "5d", "五日": "5d", "5日": "5d", "5天": "5d",
    "一月": "20d", "近一月": "20d", "本月": "20d", "一个月": "20d", "二十日": "20d", "20日": "20d", "月内": "20d",
    "两月": "60d", "60日": "60d", "三个月": "60d", "季度": "60d", "一季": "60d",
    "年初至今": "ytd", "今年以来": "ytd", "年内": "ytd", "今年": "ytd",
}
_MAINBOARD_KW = ("无门槛", "主板", "沪深主板", "不要创业板", "不要科创板",
                 "不要北交所", "不要科创", "无需开通", "普通账户")
_BUYABLE_KW = ("可买入", "能买", "能买入", "可买", "排除涨停", "非涨停", "未涨停",
               "可介入", "可上车", "低吸", "有买点")
_SMALL_KW = ("小市值", "小盘", "中小盘", "微盘")
_LARGE_KW = ("大市值", "大盘", "白马", "蓝筹", "权重")

# Common sector words → board name passed to universe.get_*_constituents.
# Kept intentionally small; the LLM fallback covers the long tail.
_SECTOR_KW: dict[str, str] = {
    "白酒": "白酒", "酒": "酿酒行业", "消费": "食品饮料", "食品": "食品饮料",
    "饮料": "食品饮料", "医药": "中药", "中药": "中药", "医疗": "医疗器械",
    "新能源": "新能源", "光伏": "光伏设备", "锂电": "电池", "电池": "电池",
    "半导体": "半导体", "芯片": "半导体", "军工": "国防军工", "证券": "证券",
    "券商": "证券", "银行": "银行", "保险": "保险", "地产": "房地产开发",
    "房地产": "房地产开发", "汽车": "汽车整车", "光模块": "光模块(CPO)",
    "人工智能": "人工智能", "ai": "人工智能", "机器人": "机器人概念",
    "算力": "算力", "数据中心": "数据中心", "煤炭": "煤炭行业", "石油": "石油行业",
    "有色": "有色金属", "钢铁": "钢铁行业", "电力": "电力行业",
    "低空经济": "低空经济", "固态电池": "固态电池", "创新药": "创新药",
}


def compile_strategy(text: str, *, llm=None) -> tuple[StrategySpec, dict]:
    """Compile a free-text goal into a ``StrategySpec`` + provenance dict.

    Provenance: ``{"source": "rule"|"rule+llm"|"llm", "labels": [...],
    "notes": "..."}``. The rule layer always runs; ``llm`` (if given) is
    asked to fill anything the rules missed — never to override a confident
    rule hit, so the result stays predictable.
    """
    spec, labels, hits = _compile_with_rules(text or "")
    provenance = {"source": "rule", "labels": labels, "notes": ""}

    # Only reach for the LLM when the rules found little to go on.
    if llm is not None and hits < 1:
        try:
            llm_spec = _compile_with_llm(text, llm)
            if llm_spec is not None:
                _merge_spec(spec, llm_spec)
                provenance["source"] = "rule+llm" if hits else "llm"
                provenance["labels"] = labels + [l for l in llm_spec.labels if l not in labels]
        except Exception as e:  # noqa: BLE001
            logger.warning("strategy LLM compile failed: %s", e)
            provenance["notes"] = "LLM 解析失败，已退回规则解析"

    spec.labels = provenance["labels"]
    return spec, provenance


def _compile_with_rules(text: str) -> tuple[StrategySpec, list[str], int]:
    """Return (spec, labels, hit_count). hit_count gauges rule confidence."""
    low = text.lower()
    spec = StrategySpec(weights=dict(DEFAULT_WEIGHTS))
    labels: list[str] = []
    weights = {"value": 0.0, "momentum": 0.0, "capital_flow": 0.0}
    hits = 0

    def has(words) -> bool:
        return any(w in low for w in words)

    if has(_VALUE_KW):
        spec.pe_max = 30.0
        weights["value"] += 1.5
        labels.append("低估值")
        hits += 1
    if has(_BREAKNAV_KW):
        spec.pb_max = 1.0
        weights["value"] += 1.0
        labels.append("破净")
        hits += 1
    if has(_HIGH_DIV_KW):
        # No dividend feed yet — proxy with low-PE value tilt and a label so
        # the UI is honest that this is an approximation.
        spec.pe_max = min(spec.pe_max or 20.0, 20.0)
        weights["value"] += 1.0
        labels.append("高股息(近似:低估值)")
        hits += 1
    # Momentum period — longest matching keyword wins (so "近一月" beats "月").
    for kw in sorted(_PERIOD_KW, key=len, reverse=True):
        if kw in low:
            spec.momentum_period = _PERIOD_KW[kw]
            break
    _PERIOD_LABEL = {"today": "当日", "5d": "近一周", "20d": "近一月", "60d": "60日", "ytd": "年初至今"}

    if has(_REVERSAL_KW):
        spec.momentum_direction = "down"
        weights["momentum"] += 1.5
        labels.append(f"超跌反弹({_PERIOD_LABEL[spec.momentum_period]})")
        hits += 1
    elif has(_MOMENTUM_KW):
        spec.momentum_direction = "up"
        weights["momentum"] += 1.5
        labels.append(f"强势动量({_PERIOD_LABEL[spec.momentum_period]})")
        hits += 1
    if has(_FLOW_KW):
        weights["capital_flow"] += 1.5
        labels.append("主力资金流入")
        hits += 1
    if has(_MAINBOARD_KW):
        spec.main_board_only = True
        labels.append("无门槛(沪深主板)")
        hits += 1
    if has(_BUYABLE_KW):
        spec.buyable_only = True
        labels.append("只看可买入")
        hits += 1
    if has(_SMALL_KW):
        spec.market_cap_max = 100.0  # 亿元
        labels.append("小市值")
        hits += 1
    if has(_LARGE_KW):
        spec.market_cap_min = 500.0
        labels.append("大市值")
        hits += 1

    # Sector — first keyword wins (longer keys checked first to prefer
    # "新能源" over "能").
    for kw in sorted(_SECTOR_KW, key=len, reverse=True):
        if kw in low:
            spec.sector_query = _SECTOR_KW[kw]
            labels.append(f"板块:{spec.sector_query}")
            hits += 1
            break

    # If no factor keyword fired, fall back to balanced weights so ranking
    # still produces a sensible order rather than all-zeros.
    if sum(weights.values()) == 0:
        weights = dict(DEFAULT_WEIGHTS)
    spec.weights = weights
    return spec, labels, hits


# ---------------------------------------------------------------------------
# LLM strategy compile (fallback)
# ---------------------------------------------------------------------------

_STRATEGY_SCHEMA_HINT = """Return ONLY a JSON object (no prose) with any of these optional keys:
{
  "pe_max": number, "pe_min": number,
  "pb_max": number, "pb_min": number,
  "market_cap_min": number, "market_cap_max": number,   // 单位: 亿元
  "change_pct_min": number, "change_pct_max": number,
  "turnover_min": number, "turnover_max": number,
  "sector_query": string,        // 行业/概念板块名, 如 "白酒" / "半导体"
  "weights": {"value": number, "momentum": number, "capital_flow": number},
  "labels": [string]             // 人类可读的策略标签
}
Only include keys you are confident about. Numbers only — never invent tickers."""


def _compile_with_llm(text: str, llm) -> Optional[StrategySpec]:
    prompt = (
        "你是A股选股策略编译器。把用户的自然语言选股需求翻译成结构化筛选条件。\n\n"
        f"用户需求: {text}\n\n{_STRATEGY_SCHEMA_HINT}"
    )
    resp = llm.invoke(prompt)
    content = getattr(resp, "content", resp)
    data = _extract_json(content if isinstance(content, str) else str(content))
    if not isinstance(data, dict):
        return None
    spec = StrategySpec.from_dict(data)
    if "weights" in data and isinstance(data["weights"], dict):
        spec.weights = {**DEFAULT_WEIGHTS, **{k: float(v) for k, v in data["weights"].items()
                                              if isinstance(v, (int, float))}}
    if isinstance(data.get("labels"), list):
        spec.labels = [str(x) for x in data["labels"]]
    return spec


def _merge_spec(base: StrategySpec, extra: StrategySpec) -> None:
    """Fill ``base``'s unset fields from ``extra`` (rules take precedence)."""
    for field_name in base.__dataclass_fields__:
        if field_name in ("weights", "labels"):
            continue
        if getattr(base, field_name) is None and getattr(extra, field_name) is not None:
            setattr(base, field_name, getattr(extra, field_name))
    # Adopt LLM weights only if the rules left the default mix in place.
    if base.weights == dict(DEFAULT_WEIGHTS) and extra.weights:
        base.weights = extra.weights


# ---------------------------------------------------------------------------
# LLM ranking / rationale over the screened shortlist
# ---------------------------------------------------------------------------

_PERIOD_LABELS = {"today": "当日", "5d": "近一周", "20d": "近一月", "60d": "60日", "ytd": "年初至今"}

# Candidates per LLM rationale call. Keeps each prompt small + JSON reliable
# when the user asks for a large result set with AI reasons on.
_LLM_CHUNK = 40


def rank_candidates(candidates: list[dict], goal: str, *, llm=None,
                    momentum_period: str = "today",
                    momentum_direction: str = "up") -> list[dict]:
    """Attach ``reason`` + ``risk`` (+ ``reason_source``) to each candidate.

    Factor order from ``score_and_rank`` is preserved — the LLM enriches,
    it does not re-sort, so the objective composite score stays the ranking
    authority. With no LLM (or on failure) a deterministic templated reason
    is used instead. The LLM is shown only the structured metrics already in
    ``candidates`` — it cannot introduce new numbers. ``momentum_period`` /
    ``momentum_direction`` only shape the human wording of the rationale.
    """
    if not candidates:
        return candidates

    plabel = _PERIOD_LABELS.get(momentum_period, "当日")

    def _apply_fallback():
        for c in candidates:
            c["reason"] = _fallback_reason(c, plabel, momentum_direction)
            c["risk"] = ""
            c["reason_source"] = "rule"

    if llm is None:
        _apply_fallback()
        return candidates

    # Every returned candidate gets an AI rationale. To keep each prompt
    # small and the JSON parseable, rank in chunks rather than one giant
    # call — a failed/garbled chunk falls back to rule reasons for just
    # that slice, not the whole list.
    by_ticker: dict = {}
    for i in range(0, len(candidates), _LLM_CHUNK):
        chunk = candidates[i:i + _LLM_CHUNK]
        try:
            for e in _rank_with_llm(chunk, goal, llm):
                if isinstance(e, dict) and e.get("ticker"):
                    by_ticker[e["ticker"]] = e
        except Exception as e:  # noqa: BLE001 — chunk degrades to rule reasons
            logger.warning("candidate ranking LLM chunk %d failed: %s", i // _LLM_CHUNK, e)

    for c in candidates:
        e = by_ticker.get(c["ticker"]) or {}
        c["reason"] = (e.get("reason") or _fallback_reason(c, plabel, momentum_direction)).strip()
        c["risk"] = (e.get("risk") or "").strip()
        c["reason_source"] = "llm" if e.get("reason") else "rule"
    return candidates


def _rank_with_llm(candidates: list[dict], goal: str, llm) -> list[dict]:
    # Compact table — only what's needed to reason, to keep tokens low.
    rows = []
    for c in candidates:
        m = c.get("metrics", {})
        sig = c.get("signal", {})
        rows.append({
            "ticker": c["ticker"], "name": c.get("name"),
            "price": m.get("price"), "change_pct": m.get("change_pct"),
            "pe": m.get("pe"), "pb": m.get("pb"),
            "market_cap_yi": m.get("market_cap"), "turnover": m.get("turnover"),
            "main_net_inflow": m.get("main_net_inflow"), "score": c.get("score"),
            # The deterministic buy-timing verdict — the LLM must stay consistent
            # with it (e.g. never say "可买入" on a 涨停封板·明日难追 name).
            "action": sig.get("action"), "buyable": sig.get("buyable"),
        })
    prompt = (
        "你是A股选股分析师。下面是已经按量化因子筛选并打分的候选股票(数据真实，来自行情接口)。\n"
        f"用户的选股目标是: {goal or '(未指定，按综合因子)'}\n\n"
        "每只股票已附带一个确定性的『操作建议』(action/buyable)，由板块涨跌停、"
        "涨幅透支度、换手与资金流推导得出。请只依据给定数据，为每只股票写一句中文"
        "入选理由和一句风险提示，且必须与 action/buyable 保持一致——"
        "对 buyable=false 的票不要写出鼓励买入的措辞，应点明为何当前不宜追(如已涨停、"
        "高位透支、量能不足、资金流出等)。不要编造任何数据，不要新增表中没有的股票。\n\n"
        f"候选(JSON): {json.dumps(rows, ensure_ascii=False)}\n\n"
        'Return ONLY a JSON array: [{"ticker":"600519","reason":"...","risk":"..."}]'
    )
    resp = llm.invoke(prompt)
    content = getattr(resp, "content", resp)
    data = _extract_json(content if isinstance(content, str) else str(content))
    if isinstance(data, dict):  # tolerate {"picks": [...]}
        data = data.get("picks") or data.get("items") or []
    return data if isinstance(data, list) else []


def _fallback_reason(c: dict, period_label: str = "当日",
                     direction: str = "up") -> str:
    """Deterministic rationale from the factor breakdown — no LLM needed."""
    m = c.get("metrics", {})
    fb = c.get("factor_breakdown", {})
    bits: list[str] = []
    if (m.get("pe") is not None) and fb.get("value", 0) and fb["value"] > 0.3:
        bits.append(f"估值偏低(PE {m['pe']:g} / PB {m.get('pb')})")
    mv = m.get("momentum_value")
    if fb.get("momentum", 0) and fb["momentum"] > 0.3 and mv is not None:
        if direction == "down":
            bits.append(f"{period_label}超跌({mv:g}%)，存在反弹空间")
        else:
            bits.append(f"{period_label}涨幅居前({mv:+g}%)")
    if fb.get("capital_flow", 0) and fb["capital_flow"] > 0.3:
        bits.append("主力资金净流入居前")
    if not bits:
        bits.append("综合因子评分居前")
    return "、".join(bits)


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------

def _extract_json(text: str):
    """Pull the first JSON object/array out of an LLM response."""
    if not isinstance(text, str):
        return None
    # Strip ```json fences if present.
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    # Parse from whichever bracket appears FIRST — an array response
    # "[{...}, ...]" must not be mistaken for its first object "{...}".
    obj_at = text.find("{")
    arr_at = text.find("[")
    candidates = [p for p in ((obj_at, "{", "}"), (arr_at, "[", "]")) if p[0] != -1]
    candidates.sort(key=lambda p: p[0])
    for start, open_ch, close_ch in candidates:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == open_ch:
                depth += 1
            elif text[i] == close_ch:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
    return None
