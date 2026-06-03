"""Deterministic factor filtering + scoring over a market snapshot.

Pure pandas, no I/O and no LLM — every function takes a normalized
DataFrame (from ``universe.get_market_snapshot``) and returns a filtered /
scored copy. This is the cheap, auditable funnel that compresses ~5000
stocks down to a few dozen candidates *before* any LLM sees them.

The ``StrategySpec`` is the contract between the LLM strategy compiler
(``agents.stock_screener.compile_strategy``) and this engine: filters
narrow the universe, weights rank what's left.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

# Factor keys → the snapshot column they score on, and whether *higher is
# better*. value factors (pe/pb) are "lower is better" so we invert them.
_FACTOR_DIRECTION = {
    "value": False,        # low PE/PB preferred
    "momentum": True,      # high recent change preferred
    "capital_flow": True,  # high main net inflow preferred
}

DEFAULT_WEIGHTS = {"value": 1.0, "momentum": 1.0, "capital_flow": 1.0}


@dataclass
class StrategySpec:
    """A compiled screening strategy. All fields optional → no-op filter."""

    # Range filters (None = unbounded on that side).
    pe_min: Optional[float] = None
    pe_max: Optional[float] = None
    pb_min: Optional[float] = None
    pb_max: Optional[float] = None
    market_cap_min: Optional[float] = None  # 亿元
    market_cap_max: Optional[float] = None  # 亿元
    change_pct_min: Optional[float] = None
    change_pct_max: Optional[float] = None
    turnover_min: Optional[float] = None
    turnover_max: Optional[float] = None
    # Momentum config. ``period`` selects which return drives the momentum
    # factor: 'today' | '5d' | '20d' | '60d' | 'ytd'. ``direction`` picks
    # 'up' (strongest gainers — trend following) or 'down' (worst losers —
    # oversold rebound / mean reversion). 'down' simply flips which end of
    # the return distribution scores highest.
    momentum_period: str = "today"
    momentum_direction: str = "up"
    # Universe restriction: only keep these 6-digit codes (e.g. a concept
    # board's constituents). None = whole market.
    universe_codes: Optional[list[str]] = None
    # Sector/board name to resolve into ``universe_codes`` at run time. Kept
    # separate from ``universe_codes`` so ``compile_strategy`` stays free of
    # network I/O — the runner resolves it via ``universe``.
    sector_query: Optional[str] = None
    # Drop ST / *ST / 退市整理期 names (default on). Critical for the
    # oversold-rebound screen: the day's biggest losers are dominated by
    # delisting stocks (退市华嵘 -90% etc.) which are near-zero, not rebound
    # candidates. Also drops 新股/N (first-day) which have no real history.
    exclude_st: bool = True
    # Ranking weights per factor.
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    # Free-text labels for provenance / UI ("低估值", "白酒概念", ...).
    labels: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        if self.universe_codes is not None:
            d["universe_codes"] = list(self.universe_codes)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "StrategySpec":
        known = {k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__}
        return cls(**known)


def _between(series: pd.Series, lo: Optional[float], hi: Optional[float]) -> pd.Series:
    mask = pd.Series(True, index=series.index)
    if lo is not None:
        mask &= series >= lo
    if hi is not None:
        mask &= series <= hi
    return mask


def apply_filters(df: pd.DataFrame, spec: StrategySpec) -> pd.DataFrame:
    """Return the subset of ``df`` matching ``spec``'s range/universe filters.

    PE/PB filters implicitly drop non-positive or NaN values (negative PE =
    loss-making; we don't want those slipping through an upper-bound filter).
    """
    out = df.copy()

    if spec.exclude_st and "name" in out.columns:
        # ST/*ST, 退/退市整理期, and N/C first-day listings — none are valid
        # screen targets (especially for oversold-rebound, where delisting
        # stocks otherwise dominate the worst-performers list).
        nm = out["name"].astype(str)
        bad = nm.str.contains("ST", case=False, na=False) | nm.str.contains("退", na=False)
        bad |= nm.str.match(r"^[NC][一-鿿]", na=False)
        out = out[~bad]

    if spec.universe_codes is not None:
        codes = {str(c).zfill(6) for c in spec.universe_codes}
        out = out[out["code"].isin(codes)]

    if spec.pe_min is not None or spec.pe_max is not None:
        out = out[out["pe"].notna() & (out["pe"] > 0)]
        out = out[_between(out["pe"], spec.pe_min, spec.pe_max)]
    if spec.pb_min is not None or spec.pb_max is not None:
        out = out[out["pb"].notna() & (out["pb"] > 0)]
        out = out[_between(out["pb"], spec.pb_min, spec.pb_max)]
    if spec.market_cap_min is not None or spec.market_cap_max is not None:
        out = out[_between(out["market_cap"], spec.market_cap_min, spec.market_cap_max)]
    if spec.change_pct_min is not None or spec.change_pct_max is not None:
        out = out[_between(out["change_pct"], spec.change_pct_min, spec.change_pct_max)]
    if spec.turnover_min is not None or spec.turnover_max is not None:
        out = out[_between(out["turnover"], spec.turnover_min, spec.turnover_max)]

    return out.reset_index(drop=True)


def _zscore(series: pd.Series, higher_is_better: bool) -> pd.Series:
    """Standardize to mean 0 / std 1, flipping sign for 'lower is better'.

    Constant or empty input → all zeros (no information to rank on).
    """
    s = pd.to_numeric(series, errors="coerce")
    std = s.std(ddof=0)
    if not std or pd.isna(std):
        return pd.Series(0.0, index=series.index)
    z = (s - s.mean()) / std
    z = z.fillna(0.0)
    return z if higher_is_better else -z


def score_and_rank(
    df: pd.DataFrame,
    weights: Optional[dict[str, float]] = None,
    capital_flow: Optional[pd.DataFrame] = None,
    top_n: Optional[int] = None,
    momentum_col: str = "change_pct",
    momentum_direction: str = "up",
) -> pd.DataFrame:
    """Attach per-factor sub-scores + a weighted composite, sorted desc.

    Adds columns ``score_value, score_momentum, score_capital_flow,
    score`` (composite), and ``main_net_inflow`` if ``capital_flow`` is
    joined in. The composite is a weight-normalized sum of z-scores, so
    it's comparable across runs with different weight mixes.

    ``momentum_col`` is the return column the momentum factor scores on
    (the runner sets this per chosen period). ``momentum_direction='down'``
    flips the sign so the *worst* performers rank highest — oversold-rebound
    screening. The scored momentum value is also copied to ``momentum_value``
    so ``to_candidates`` can surface the exact return used.
    """
    if df.empty:
        return df.assign(score=[], rank=[])

    weights = {**DEFAULT_WEIGHTS, **(weights or {})}
    out = df.copy()

    # Join capital flow by code (left join → missing flow becomes 0 score).
    if capital_flow is not None and not capital_flow.empty:
        out = out.merge(capital_flow, on="code", how="left")
    if "main_net_inflow" not in out.columns:
        out["main_net_inflow"] = float("nan")

    # The momentum source column may be absent (e.g. degraded feed has no
    # 60d return) — fall back to today's change so ranking still works.
    if momentum_col not in out.columns:
        momentum_col = "change_pct"
    out["momentum_value"] = pd.to_numeric(out[momentum_col], errors="coerce")
    mom_higher = momentum_direction != "down"

    # value factor: blend inverted PE + PB z-scores.
    out["score_value"] = (
        _zscore(out["pe"], higher_is_better=False)
        + _zscore(out["pb"], higher_is_better=False)
    ) / 2.0
    out["score_momentum"] = _zscore(out["momentum_value"], higher_is_better=mom_higher)
    out["score_capital_flow"] = _zscore(out["main_net_inflow"], higher_is_better=True)

    wsum = sum(abs(w) for w in weights.values()) or 1.0
    out["score"] = (
        weights.get("value", 0) * out["score_value"]
        + weights.get("momentum", 0) * out["score_momentum"]
        + weights.get("capital_flow", 0) * out["score_capital_flow"]
    ) / wsum
    out["score"] = out["score"].round(4)

    out = out.sort_values("score", ascending=False).reset_index(drop=True)
    out["rank"] = out.index + 1
    if top_n is not None:
        out = out.head(top_n).reset_index(drop=True)
    return out


def to_candidates(df: pd.DataFrame) -> list[dict]:
    """Serialize a scored frame into JSON-able candidate dicts.

    Splits objective ``metrics`` (sourced from data tools) from
    ``factor_breakdown`` (computed sub-scores) so the frontend can label
    provenance clearly — the LLM's rationale is added later, separately.
    """
    candidates = []
    for _, r in df.iterrows():
        candidates.append({
            "code": r["code"],
            "ticker": r["code"],
            "name": r.get("name"),
            "rank": int(r["rank"]) if "rank" in r and pd.notna(r["rank"]) else None,
            "score": float(r["score"]) if pd.notna(r.get("score")) else None,
            "metrics": {
                "price": _f(r.get("price")),
                "change_pct": _f(r.get("change_pct")),
                "momentum_value": _f(r.get("momentum_value")),
                "pe": _f(r.get("pe")),
                "pb": _f(r.get("pb")),
                "market_cap": _f(r.get("market_cap")),
                "turnover": _f(r.get("turnover")),
                "main_net_inflow": _f(r.get("main_net_inflow")),
            },
            "factor_breakdown": {
                "value": _f(r.get("score_value")),
                "momentum": _f(r.get("score_momentum")),
                "capital_flow": _f(r.get("score_capital_flow")),
            },
            "source": "akshare/eastmoney",
        })
    return candidates


def _f(v) -> Optional[float]:
    try:
        if v is None or pd.isna(v):
            return None
        return round(float(v), 4)
    except (TypeError, ValueError):
        return None
