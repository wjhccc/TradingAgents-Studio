"""Actionable trade-timing signals for screened candidates.

Pure functions, no I/O and no LLM. Given a candidate's objective metrics
(price/change/turnover/capital-flow, already fetched by ``universe``), derive
a *deterministic, board-aware* "can I actually buy this tomorrow?" verdict.

The motivation: the factor screen ranks by momentum, so a naive "强势动量"
run returns a limit-up board — names that already surged and that you either
can't buy at tomorrow's open (一字涨停 / 排队封单) or that are statistically
prone to gap-up-then-dump. This module turns the raw return into an honest,
explainable action label so the UI stops recommending un-buyable stocks.

Nothing here predicts price. It encodes well-known A-share microstructure:
 - per-board daily price limits (涨跌停幅度),
 - that a name pinned at its limit is hard to enter the next day,
 - that an over-extended intraday move carries elevated pullback risk,
 - that healthy turnover + main-capital inflow is more sustainable than a
   thin, low-volume spike.

All thresholds are conservative heuristics, surfaced (not hidden) so the user
can judge them. ``action_signal`` is the entry point; ``extension_penalty``
feeds the ranking so the surfaced Top-N is actionable, not just the biggest
gainers.
"""

from __future__ import annotations

from typing import Optional

# Signal "level" → drives UI colour. Ordered from safest to most cautionary.
LEVEL_GOOD = "good"        # green   —量价健康，可考虑介入
LEVEL_WATCH = "watch"      # neutral — 观望/等确认
LEVEL_WARN = "warn"        # orange  — 高位，谨慎
LEVEL_AVOID = "avoid"      # red     — 涨停/追高易套，不建议追

# Timing bucket → when the action applies, if at all.
TIMING_TODAY = "today"     # 今日盘中可操作
TIMING_TOMORROW = "tomorrow"  # 次日竞价/开盘观察后操作
TIMING_WAIT = "wait"       # 暂不操作，等信号


def board_limit_pct(code: str, name: Optional[str] = None) -> float:
    """Daily price-limit magnitude (%) for an A-share code.

    ST / *ST / 退市整理 names are ±5% regardless of board. Otherwise by board:
    科创板(688/689) & 创业板(300/301) ±20%, 北交所(4../8../920) ±30%,
    主板(600/601/603/605/000/001/002/003) ±10%. Unknown prefixes default to
    ±10% (the conservative main-board limit).
    """
    nm = (name or "").upper()
    if "ST" in nm or "退" in nm:
        return 5.0
    c = str(code or "").zfill(6)
    if c.startswith(("688", "689", "300", "301")):
        return 20.0
    if c.startswith(("8", "4", "920", "92")):
        return 30.0
    if c.startswith(("600", "601", "603", "605", "000", "001", "002", "003")):
        return 10.0
    return 10.0


def _clean_num(x) -> Optional[float]:
    """Coerce to float, mapping None / NaN / non-numeric → None.

    Callers pass values straight off a pandas frame where missing data is NaN,
    which (unlike None) passes ``is not None`` and corrupts numeric branches.
    """
    if x is None:
        return None
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return None
    return None if xf != xf else xf  # xf != xf is True only for NaN


def board_name(code: str) -> str:
    """Human board label for a code: 主板 | 创业板 | 科创板 | 北交所 | 其他."""
    c = str(code or "").zfill(6)
    if c.startswith(("688", "689")):
        return "科创板"
    if c.startswith(("300", "301")):
        return "创业板"
    if c.startswith(("8", "4", "920", "92")):
        return "北交所"
    if c.startswith(("600", "601", "603", "605", "000", "001", "002", "003")):
        return "主板"
    return "其他"


def requires_permission(code: str, name: Optional[str] = None) -> bool:
    """True when buying this code needs a special account permission / 资金门槛.

    A-share boards with an entry barrier an ordinary new account can't trade:
      - 科创板 (688/689): 50万资产 + 2年经验
      - 创业板 (300/301): 10万资产 + 2年经验
      - 北交所 (4../8../920): 50万资产 + 2年经验
      - ST / *ST / 退市整理: 需单独开通风险警示品种权限
    主板 (沪深 600/601/603/605/000/001/002/003) is barrier-free ("无门槛").
    Unknown prefixes are treated as restricted (conservative — don't surface a
    name we can't confirm is freely tradable).
    """
    nm = (name or "").upper()
    if "ST" in nm or "退" in nm:
        return True
    return board_name(code) in ("科创板", "创业板", "北交所", "其他")


def extension_ratio(change_pct: Optional[float], limit_pct: float) -> Optional[float]:
    """How far today's move has travelled toward the up-limit, in [.. , 1+].

    1.0 == sitting at the limit. >0.9 is effectively pinned. Negative when the
    stock is down on the day. Returns None when inputs are missing.
    """
    if change_pct is None or not limit_pct:
        return None
    return round(change_pct / limit_pct, 4)


def extension_penalty(change_pct: Optional[float], limit_pct: float) -> float:
    """Ranking demerit (0..1) for an over-extended *up* move.

    Used by the momentum-up ranking so names already near their limit — which
    you can't realistically buy tomorrow — sink below strong-but-enterable
    ones. 0 below 60% of the limit, ramping to ~1 at the limit, so a mild
    gainer is untouched while a sealed limit-up is heavily docked.
    """
    r = extension_ratio(change_pct, limit_pct)
    if r is None or r <= 0.6:
        return 0.0
    if r >= 0.98:
        return 1.0
    # Linear ramp 0.6 → 0.98 mapped to 0 → 1.
    return round((r - 0.6) / (0.98 - 0.6), 4)


def in_enterable_band(change_pct: Optional[float], code: str,
                      name: Optional[str] = None, direction: str = "up") -> bool:
    """Is today's move in a band you can still realistically enter?

    Drives the ``buyable_only`` screen filter — the decisive answer to "find me
    stocks I can actually buy". Excludes names pinned at / near a price limit
    (can't get filled next day) and names crashing on the day (don't catch a
    falling knife). Cheap (extension-ratio only), so it's run over the whole
    universe before ranking.

    up:   keep -3% .. 60%-of-limit (strength without exhaustion).
    down: keep names that fell but aren't limit-down (-95% .. -20% of limit) —
          oversold yet still tradable for a rebound.
    """
    limit = board_limit_pct(code, name)
    r = extension_ratio(change_pct, limit)
    if r is None:
        return False
    if direction == "down":
        return -0.98 < r <= -0.2
    return -0.3 <= r <= 0.6


def trade_plan(price: Optional[float], timing: str, direction: str = "up") -> Optional[dict]:
    """A concrete, deterministic buy/sell plan for an enterable name.

    Returns entry band + stop + target as absolute prices, plus plain-language
    *when to buy* / *when to sell* guidance keyed off the timing bucket. These
    are fixed-percentage risk levels (NOT predictions): a ~6% stop against a
    ~10% target gives a positive risk/reward, with session-aware entry timing.
    Returns None when there's no usable price.
    """
    if not price or price <= 0:
        return None
    p = float(price)
    if direction == "down":
        buy_low, buy_high = round(p * 0.97, 2), round(p * 1.00, 2)
        stop, target = round(p * 0.95, 2), round(p * 1.08, 2)
    else:
        buy_low, buy_high = round(p * 0.985, 2), round(p * 1.005, 2)
        stop, target = round(p * 0.94, 2), round(p * 1.10, 2)

    if timing == TIMING_TODAY:
        when_buy = f"今日盘中回踩 {buy_low}~{buy_high}(分时均价附近)分批买入"
    elif timing == TIMING_TOMORROW:
        when_buy = (f"明日集合竞价(9:25)观察：平开或小幅高开(<3%)且开盘不跌破 "
                    f"{buy_low} 再买入；若大幅低开跌破 {stop} 则放弃")
    else:
        when_buy = "暂不买入，等出现上述买点再说"

    sp = round((stop / p - 1) * 100)
    tp = round((target / p - 1) * 100)
    when_sell = (f"止盈 {target}(约+{tp}%)分批了结；跌破 {stop}(约{sp}%)无条件止损；"
                 f"持有约 1~3 个交易日，放量冲高减仓、滞涨则次日清仓")
    return {"entry_low": buy_low, "entry_high": buy_high, "stop": stop,
            "target": target, "when_buy": when_buy, "when_sell": when_sell}


def _turnover_health(turnover: Optional[float]) -> str:
    """Classify turnover (换手率, %): thin | healthy | crowded | unknown."""
    if turnover is None:
        return "unknown"
    if turnover < 1.0:
        return "thin"        # 换手不足，流动性差/缺乏关注
    if turnover <= 20.0:
        return "healthy"     # 量能活跃且未到分歧出货区
    return "crowded"         # 换手过高，多空分歧大，易冲高回落


def action_signal(
    *,
    code: str,
    name: Optional[str],
    change_pct: Optional[float],
    turnover: Optional[float] = None,
    main_net_inflow: Optional[float] = None,
    direction: str = "up",
) -> dict:
    """Derive an actionable trade-timing verdict for one candidate.

    Returns a dict::

        {"action": str,      # short Chinese label, e.g. "量价健康·可逢低介入"
         "timing": str,      # TIMING_TODAY | TIMING_TOMORROW | TIMING_WAIT
         "level": str,       # LEVEL_* — drives UI colour
         "buyable": bool,    # is this realistically enterable near-term?
         "note": str,        # one-line plain-language rationale / caution
         "limit_pct": float, # the board limit applied
         "extension": float} # extension_ratio (None-safe → may be absent)

    ``direction='down'`` switches to oversold-rebound semantics (the screen is
    looking for beaten-down names, so "buyable" means *stabilising*, and a
    limit-DOWN is the thing to avoid — 别接飞刀).
    """
    # Inputs may arrive straight off a DataFrame, where "missing" is NaN, not
    # None. NaN is not None and silently breaks every numeric comparison (and
    # would mislabel NaN turnover as "crowded"), so coerce NaN → None first.
    change_pct = _clean_num(change_pct)
    turnover = _clean_num(turnover)
    main_net_inflow = _clean_num(main_net_inflow)

    limit = board_limit_pct(code, name)
    r = extension_ratio(change_pct, limit)
    health = _turnover_health(turnover)
    inflow_pos = main_net_inflow is not None and main_net_inflow > 0
    inflow_neg = main_net_inflow is not None and main_net_inflow < 0

    out = {"limit_pct": limit}
    if r is not None:
        out["extension"] = r

    # ---- Oversold-rebound screen (direction='down') ----------------------
    if direction == "down":
        if r is not None and r <= -0.98:
            out.update(action="跌停·勿接飞刀", timing=TIMING_WAIT, level=LEVEL_AVOID,
                       buyable=False, note="封死跌停，抛压未尽，等开板放量企稳再看")
            return out
        if r is not None and r <= -0.6:
            out.update(action="深跌中·等企稳", timing=TIMING_WAIT, level=LEVEL_WARN,
                       buyable=False, note="仍在加速下杀，左侧抄底风险高，等止跌信号")
            return out
        if inflow_pos and health in ("healthy", "thin"):
            out.update(action="超跌企稳·可小仓试探", timing=TIMING_TOMORROW, level=LEVEL_WATCH,
                       buyable=True, note="跌幅充分且主力回流，明日不破前低可小仓低吸")
            return out
        out.update(action="超跌观察", timing=TIMING_WAIT, level=LEVEL_WATCH,
                   buyable=False, note="跌幅到位但缺资金验证，等放量企稳确认")
        return out

    # ---- Trend-following screen (direction='up') -------------------------
    # 1) Pinned at / near the up-limit — you can't reliably buy tomorrow.
    if r is not None and r >= 0.98:
        out.update(action="涨停封板·明日难追", timing=TIMING_WAIT, level=LEVEL_AVOID,
                   buyable=False,
                   note="已封涨停，明日多为高开排队，追高易套；回踩不破支撑再考虑")
        return out
    # 2) Strong but over-extended — elevated gap-up-then-dump risk.
    if r is not None and r >= 0.6:
        note = "涨幅已大，明日冲高回落概率高，等回调企稳再介入"
        if health == "crowded":
            note = "涨幅大且换手过高，分歧出货迹象，明日谨防高开走低"
        out.update(action="高位·不建议追高", timing=TIMING_WAIT, level=LEVEL_WARN,
                   buyable=False, note=note)
        return out
    # 3) Healthy moderate strength with capital support — the sweet spot.
    #    (1% .. 60%-of-limit; the >=0.6 over-extended case already returned.)
    if change_pct is not None and 1.0 <= change_pct < 0.6 * limit:
        if health == "healthy" and inflow_pos:
            out.update(action="量价健康·可逢低介入", timing=TIMING_TODAY, level=LEVEL_GOOD,
                       buyable=True,
                       note="涨幅温和、换手活跃且主力净流入，回踩均价可考虑介入")
            return out
        if health == "thin":
            out.update(action="量能不足·谨慎", timing=TIMING_WAIT, level=LEVEL_WATCH,
                       buyable=False, note="换手偏低，资金关注度不足，等放量再看")
            return out
        if inflow_neg:
            out.update(action="涨但资金流出·观望", timing=TIMING_WAIT, level=LEVEL_WATCH,
                       buyable=False, note="价涨但主力净流出，背离需警惕，暂观望")
            return out
        out.update(action="温和走强·可关注", timing=TIMING_TOMORROW, level=LEVEL_WATCH,
                   buyable=True, note="尚未过度透支，明日竞价不高开可低吸")
        return out
    # 4) Flat / mild start — early, watch for volume confirmation.
    if change_pct is not None and -3.0 <= change_pct < 1.0:
        if inflow_pos and health in ("healthy", "thin"):
            out.update(action="启动初期·可关注", timing=TIMING_TOMORROW, level=LEVEL_WATCH,
                       buyable=True, note="尚在低位且主力回流，明日放量突破可跟进")
            return out
        out.update(action="蓄势观望", timing=TIMING_WAIT, level=LEVEL_WATCH,
                   buyable=False, note="方向未明，等放量选择方向")
        return out
    # 5) Down on the day in an up-screen — weak, avoid.
    if change_pct is not None and change_pct < -3.0:
        out.update(action="走弱·回避", timing=TIMING_WAIT, level=LEVEL_WARN,
                   buyable=False, note="当日大幅走弱，与强势选股目标背离，暂回避")
        return out

    # Fallback when change_pct is missing entirely (degraded feed).
    out.update(action="数据不足·观望", timing=TIMING_WAIT, level=LEVEL_WATCH,
               buyable=False, note="缺少当日涨跌/换手数据，无法判断买点")
    return out
