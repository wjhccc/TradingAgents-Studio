"""Decision-quality (决策质量) analytics endpoints.

Answers the question users actually care about: "is the Agent making good
calls?". Reads ``analyses`` rows (which already carry signal + confidence
+ analyst combo + LLM config) and matches each one against realised
N-day price moves pulled from the same vendor router the analysis pipeline
uses (AKShare → yfinance), benchmarked against a sensible regional index.

No new tables — everything is computed on demand. Price series are cached
in-process so a "compute quality across the whole history" request only
hits the vendor a handful of times per ticker / per server lifetime.

All endpoints accept a ``horizon`` query (5 / 30 / 60 days, default 30)
that defines the holding period used to measure each signal's realised
return. A decision is considered "evaluable" only if at least one trading
bar exists ``horizon`` days after its trade_date — pending decisions on
very recent dates are surfaced but not counted in win-rate stats.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import math
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd
from fastapi import APIRouter, Query

from .. import database as db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/quality", tags=["quality"])


# Horizons the frontend exposes. Validated server-side so callers can't
# request 10000-day windows that would balloon the price-fetch range.
_VALID_HORIZONS = {5, 30, 60}


# Signals we treat as directional (counted in win-rate). Anything else
# (e.g. None, "HOLD") is surfaced in the decision list but skipped from
# the realized-return aggregates because there's no implied position.
_LONG_SIGNALS = {"BUY", "OVERWEIGHT"}
_SHORT_SIGNALS = {"SELL", "UNDERWEIGHT"}


# ---------------------------------------------------------------------------
# Price-series cache
# ---------------------------------------------------------------------------
# A single "quality overview" request can touch every analysis row, which
# means dozens of repeated calls for the same (ticker, range). Cache the
# parsed close series in memory keyed by ticker. Per-ticker TTL is generous
# (10 min) because historical bars don't move; the rightmost bar may, but
# horizon-N return calculation reads bars at trade_date+N, which is in the
# past by definition for any evaluable decision.

_PRICE_CACHE: dict[str, tuple[float, pd.Series]] = {}
_PRICE_CACHE_TTL_SEC = 600.0
_PRICE_CACHE_LOCK = threading.Lock()
# Misses (ticker → fail timestamp) so we don't spam vendors when a ticker
# is delisted / typo'd. Short TTL so a transient vendor blip recovers fast.
_PRICE_MISS_CACHE: dict[str, float] = {}
_PRICE_MISS_TTL_SEC = 120.0


def _benchmark_for(ticker: str) -> str:
    """Pick a regional benchmark for alpha calculation.

    Mirrors the logic in ``trading_graph.alpha_benchmark_for`` but kept
    local so this module has no dependency on the LangGraph engine.
    """
    t = (ticker or "").upper()
    # A-share heuristics: 6-digit code or explicit SH/SZ suffix.
    digits = "".join(ch for ch in t if ch.isdigit())
    if len(digits) == 6 and (t == digits or ".SH" in t or ".SS" in t or ".SZ" in t):
        return "000300.SS"  # 沪深 300
    if t.endswith(".HK"):
        return "^HSI"
    if t.endswith(".T"):
        return "^N225"
    if t.endswith(".L"):
        return "^FTSE"
    if t.endswith(".TO"):
        return "^GSPTSE"
    if t.endswith(".AX"):
        return "^AXJO"
    if t.endswith(".NS"):
        return "^NSEI"
    if t.endswith(".BO"):
        return "^BSESN"
    return "SPY"


def _fetch_close_series(ticker: str, start: date, end: date) -> Optional[pd.Series]:
    """Return a date-indexed Close series for ``ticker`` covering [start, end].

    None on vendor failure. Cached in-process for ``_PRICE_CACHE_TTL_SEC``.
    """
    key = f"{ticker.upper()}|{start.isoformat()}|{end.isoformat()}"
    now = time.monotonic()
    with _PRICE_CACHE_LOCK:
        hit = _PRICE_CACHE.get(key)
        if hit and now < hit[0]:
            return hit[1]
        miss = _PRICE_MISS_CACHE.get(key)
        if miss and now < miss:
            return None

    from tradingagents.dataflows.interface import route_to_vendor

    try:
        csv_str = route_to_vendor(
            "get_stock_data",
            ticker,
            start.strftime("%Y-%m-%d"),
            end.strftime("%Y-%m-%d"),
        )
    except Exception as e:
        logger.warning("quality: price fetch failed %s: %s", ticker, e)
        with _PRICE_CACHE_LOCK:
            _PRICE_MISS_CACHE[key] = now + _PRICE_MISS_TTL_SEC
        return None

    if not isinstance(csv_str, str):
        with _PRICE_CACHE_LOCK:
            _PRICE_MISS_CACHE[key] = now + _PRICE_MISS_TTL_SEC
        return None
    header_end = csv_str.find("\n\n")
    data_section = csv_str[header_end + 2 :] if header_end != -1 else csv_str
    if "No " in csv_str[:200] and "data" in csv_str[:200]:
        with _PRICE_CACHE_LOCK:
            _PRICE_MISS_CACHE[key] = now + _PRICE_MISS_TTL_SEC
        return None
    try:
        df = pd.read_csv(io.StringIO(data_section))
    except Exception:
        with _PRICE_CACHE_LOCK:
            _PRICE_MISS_CACHE[key] = now + _PRICE_MISS_TTL_SEC
        return None
    if df.empty or "Close" not in df.columns:
        with _PRICE_CACHE_LOCK:
            _PRICE_MISS_CACHE[key] = now + _PRICE_MISS_TTL_SEC
        return None

    date_col = None
    for cand in ("Date", "date", "Unnamed: 0"):
        if cand in df.columns:
            date_col = cand
            break
    if date_col is None:
        df = df.reset_index().rename(columns={"index": "Date"})
        date_col = "Date"
    try:
        df[date_col] = pd.to_datetime(df[date_col]).dt.date
    except Exception:
        with _PRICE_CACHE_LOCK:
            _PRICE_MISS_CACHE[key] = now + _PRICE_MISS_TTL_SEC
        return None
    series = pd.Series(df["Close"].astype(float).values, index=df[date_col])
    series = series[~series.index.duplicated(keep="last")].sort_index()
    with _PRICE_CACHE_LOCK:
        _PRICE_CACHE[key] = (now + _PRICE_CACHE_TTL_SEC, series)
    return series


def _price_at_or_after(series: pd.Series, target: date) -> Optional[tuple[date, float]]:
    """First (date, close) at-or-after ``target``. None if the series ends earlier."""
    if series is None or series.empty:
        return None
    forward = series[series.index >= target]
    if forward.empty:
        return None
    d = forward.index[0]
    return d, float(forward.iloc[0])


def _price_at_or_before(series: pd.Series, target: date) -> Optional[tuple[date, float]]:
    """Last (date, close) at-or-before ``target``. None if series starts later."""
    if series is None or series.empty:
        return None
    backward = series[series.index <= target]
    if backward.empty:
        return None
    d = backward.index[-1]
    return d, float(backward.iloc[-1])


@dataclass
class EvalRow:
    """One analysis row enriched with realized-return metrics."""
    id: str
    ticker: str
    trade_date: str
    signal: Optional[str]
    confidence: Optional[float]
    created_at: str
    analysts: list[str] = field(default_factory=list)
    llm_provider: Optional[str] = None
    deep_think_llm: Optional[str] = None
    raw_return: Optional[float] = None      # pct, e.g. 0.034 = +3.4 %
    bench_return: Optional[float] = None
    alpha: Optional[float] = None
    horizon_used: Optional[int] = None
    evaluable: bool = False
    win: Optional[bool] = None              # only set for directional signals


def _parse_analyst_combo(analysts_json: str) -> list[str]:
    try:
        v = json.loads(analysts_json or "[]")
        if isinstance(v, list):
            return sorted(str(x) for x in v)
    except Exception:
        pass
    return []


def _parse_config(config_json: str) -> dict:
    try:
        v = json.loads(config_json or "{}")
        if isinstance(v, dict):
            return v
    except Exception:
        pass
    return {}


def _build_eval_rows(
    rows: list[dict],
    horizon: int,
    *,
    today: Optional[date] = None,
) -> list[EvalRow]:
    """Annotate each analysis row with realised return / alpha at ``horizon``.

    Rows whose trade_date is too recent for the horizon to have elapsed
    are returned with ``evaluable=False`` so the UI can show them as
    pending. Vendor failures degrade gracefully — those rows stay
    non-evaluable rather than killing the whole response.
    """
    today = today or date.today()
    out: list[EvalRow] = []

    # Group rows by ticker so we fetch each price series once across the
    # full span (oldest trade_date through today).
    by_ticker: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_ticker[r["ticker"]].append(r)

    # Cache benchmark series per benchmark symbol too.
    bench_series: dict[str, Optional[pd.Series]] = {}

    for ticker, group in by_ticker.items():
        try:
            min_date = min(_safe_parse_date(r["trade_date"]) for r in group)
        except Exception:
            continue
        # Pull a generous span: from earliest trade_date - a bit, to today
        # (plus horizon to absorb forward bars if trade_date is recent).
        start = min_date - timedelta(days=5)
        end = today + timedelta(days=1)
        price_series = _fetch_close_series(ticker, start, end)
        bench_sym = _benchmark_for(ticker)
        if bench_sym not in bench_series:
            bench_series[bench_sym] = _fetch_close_series(bench_sym, start, end)
        bench = bench_series[bench_sym]

        for r in group:
            er = EvalRow(
                id=r["id"],
                ticker=r["ticker"],
                trade_date=r["trade_date"],
                signal=r.get("signal"),
                confidence=r.get("confidence"),
                created_at=r.get("created_at", ""),
                analysts=_parse_analyst_combo(r.get("analysts", "[]")),
            )
            cfg = _parse_config(r.get("config_json", "{}"))
            er.llm_provider = cfg.get("llm_provider")
            er.deep_think_llm = cfg.get("deep_think_llm")

            try:
                td = _safe_parse_date(r["trade_date"])
            except Exception:
                out.append(er)
                continue
            target = td + timedelta(days=horizon)
            # If trade_date+horizon hasn't happened yet, mark non-evaluable.
            if target > today:
                out.append(er)
                continue
            if price_series is None or bench is None:
                out.append(er)
                continue

            entry = _price_at_or_after(price_series, td)
            exit_ = _price_at_or_after(price_series, target)
            b_entry = _price_at_or_after(bench, td)
            b_exit = _price_at_or_after(bench, target)
            if not (entry and exit_ and b_entry and b_exit):
                out.append(er)
                continue
            if entry[1] <= 0 or b_entry[1] <= 0:
                out.append(er)
                continue
            raw = (exit_[1] - entry[1]) / entry[1]
            bench_ret = (b_exit[1] - b_entry[1]) / b_entry[1]
            alpha = raw - bench_ret
            er.raw_return = raw
            er.bench_return = bench_ret
            er.alpha = alpha
            er.horizon_used = horizon
            er.evaluable = True
            sig = (er.signal or "").upper()
            if sig in _LONG_SIGNALS:
                er.win = raw > 0
            elif sig in _SHORT_SIGNALS:
                er.win = raw < 0
            out.append(er)
    return out


def _safe_parse_date(s: str) -> date:
    # trade_date may be either "YYYY-MM-DD" or an ISO datetime.
    if not s:
        raise ValueError("empty date")
    s = s.strip()
    if "T" in s:
        return datetime.fromisoformat(s.replace("Z", "")).date()
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


def _summarise(rows: list[EvalRow]) -> dict:
    evals = [r for r in rows if r.evaluable]
    directional = [r for r in evals if r.win is not None]
    wins = [r for r in directional if r.win]
    raws = [r.raw_return for r in evals if r.raw_return is not None]
    alphas = [r.alpha for r in evals if r.alpha is not None]

    return {
        "total": len(rows),
        "evaluable": len(evals),
        "directional": len(directional),
        "win_rate": (len(wins) / len(directional)) if directional else None,
        "avg_raw_return": (sum(raws) / len(raws)) if raws else None,
        "avg_alpha": (sum(alphas) / len(alphas)) if alphas else None,
        "median_alpha": _median(alphas),
        "best_alpha": max(alphas) if alphas else None,
        "worst_alpha": min(alphas) if alphas else None,
        "alpha_sharpe": _alpha_sharpe(alphas),
    }


def _median(xs: list[float]) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    mid = n // 2
    if n % 2:
        return s[mid]
    return 0.5 * (s[mid - 1] + s[mid])


def _alpha_sharpe(alphas: list[float]) -> Optional[float]:
    """Per-decision Sharpe of alpha (mean / stdev). Not annualised — the
    horizon is fixed per request, so the unit is "alpha per N-day signal"."""
    if len(alphas) < 2:
        return None
    mean = sum(alphas) / len(alphas)
    var = sum((a - mean) ** 2 for a in alphas) / (len(alphas) - 1)
    sd = math.sqrt(var)
    if sd == 0:
        return None
    return mean / sd


def _all_eval_rows(horizon: int) -> list[EvalRow]:
    """Fetch every completed analysis and enrich. Used by overview and
    dimension breakdowns. Synchronous (called inside run_in_executor)."""
    with db.get_db() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, ticker, trade_date, signal, confidence, analysts, "
            "config_json, created_at FROM analyses "
            "WHERE status = 'complete' AND signal IS NOT NULL "
            "ORDER BY trade_date ASC"
        ).fetchall()]
    return _build_eval_rows(rows, horizon=horizon)


def _dimension_key(r: EvalRow, dim: str) -> Optional[str]:
    if dim == "ticker":
        return r.ticker
    if dim == "signal":
        return (r.signal or "").upper() or None
    if dim == "analyst_combo":
        return ", ".join(r.analysts) if r.analysts else None
    if dim == "analyst":
        # Special: caller iterates each analyst tag separately.
        return None
    if dim == "llm":
        if r.llm_provider and r.deep_think_llm:
            return f"{r.llm_provider} · {r.deep_think_llm}"
        return r.llm_provider or r.deep_think_llm
    return None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/overview")
async def overview(horizon: int = Query(30, description="Lookforward in days")):
    if horizon not in _VALID_HORIZONS:
        horizon = 30
    loop = asyncio.get_running_loop()
    rows = await loop.run_in_executor(None, _all_eval_rows, horizon)

    summary = _summarise(rows)

    # Signal mix — how many of each Buy/Sell/Hold appear in evaluables.
    sig_counts: dict[str, int] = defaultdict(int)
    sig_alpha_sum: dict[str, float] = defaultdict(float)
    sig_alpha_n: dict[str, int] = defaultdict(int)
    for r in rows:
        if not r.signal:
            continue
        key = r.signal.upper()
        sig_counts[key] += 1
        if r.alpha is not None:
            sig_alpha_sum[key] += r.alpha
            sig_alpha_n[key] += 1
    signal_mix = []
    for sig, cnt in sorted(sig_counts.items(), key=lambda x: -x[1]):
        avg_alpha = (sig_alpha_sum[sig] / sig_alpha_n[sig]) if sig_alpha_n[sig] else None
        signal_mix.append({
            "signal": sig,
            "count": cnt,
            "avg_alpha": avg_alpha,
        })

    return {
        "horizon": horizon,
        "summary": summary,
        "signal_mix": signal_mix,
    }


@router.get("/by-dimension")
async def by_dimension(
    dim: str = Query("ticker", description="ticker | analyst_combo | analyst | llm | signal"),
    horizon: int = Query(30),
    min_count: int = Query(1, description="Min directional decisions to include a group"),
):
    if horizon not in _VALID_HORIZONS:
        horizon = 30
    loop = asyncio.get_running_loop()
    rows = await loop.run_in_executor(None, _all_eval_rows, horizon)

    groups: dict[str, list[EvalRow]] = defaultdict(list)
    if dim == "analyst":
        # Each analyst tag becomes its own group; one row contributes to
        # every analyst it used. Lets us answer "which analyst's presence
        # correlates with the best alpha?".
        for r in rows:
            for a in r.analysts:
                groups[a].append(r)
    else:
        for r in rows:
            k = _dimension_key(r, dim)
            if k is None:
                continue
            groups[k].append(r)

    items = []
    for k, group in groups.items():
        s = _summarise(group)
        if (s["directional"] or 0) < min_count and dim != "ticker":
            continue
        items.append({"key": k, **s})
    items.sort(key=lambda x: (x["avg_alpha"] is None, -(x["avg_alpha"] or -9e9)))
    return {"dim": dim, "horizon": horizon, "items": items}


@router.get("/calibration")
async def calibration(horizon: int = Query(30)):
    """Confidence-bucket calibration curve.

    Buckets directional decisions by reported confidence (0.0-1.0) into
    fixed bins, and reports the realized win-rate per bucket. A well-
    calibrated agent has win-rate ≈ bucket center.
    """
    if horizon not in _VALID_HORIZONS:
        horizon = 30
    loop = asyncio.get_running_loop()
    rows = await loop.run_in_executor(None, _all_eval_rows, horizon)

    buckets = [
        ("0.0-0.2", 0.0, 0.2),
        ("0.2-0.4", 0.2, 0.4),
        ("0.4-0.6", 0.4, 0.6),
        ("0.6-0.8", 0.6, 0.8),
        ("0.8-1.0", 0.8, 1.0001),
    ]
    out = []
    for label, lo, hi in buckets:
        bucket = [r for r in rows
                  if r.win is not None
                  and r.confidence is not None
                  and lo <= r.confidence < hi]
        wins = [r for r in bucket if r.win]
        alphas = [r.alpha for r in bucket if r.alpha is not None]
        out.append({
            "bucket": label,
            "lo": lo,
            "hi": hi if hi <= 1.0 else 1.0,
            "count": len(bucket),
            "win_rate": (len(wins) / len(bucket)) if bucket else None,
            "avg_alpha": (sum(alphas) / len(alphas)) if alphas else None,
        })
    return {"horizon": horizon, "buckets": out}


@router.get("/heatmap")
async def heatmap(horizon: int = Query(30)):
    """Decision-day heatmap: per-day average alpha across all decisions.

    Returns a flat list ``[{date, count, avg_alpha}]`` ordered by date.
    The frontend grids it into a github-style year heatmap.
    """
    if horizon not in _VALID_HORIZONS:
        horizon = 30
    loop = asyncio.get_running_loop()
    rows = await loop.run_in_executor(None, _all_eval_rows, horizon)

    per_day_count: dict[str, int] = defaultdict(int)
    per_day_alpha_sum: dict[str, float] = defaultdict(float)
    per_day_alpha_n: dict[str, int] = defaultdict(int)
    for r in rows:
        d = r.trade_date[:10] if r.trade_date else None
        if not d:
            continue
        per_day_count[d] += 1
        if r.alpha is not None:
            per_day_alpha_sum[d] += r.alpha
            per_day_alpha_n[d] += 1
    out = []
    for d in sorted(per_day_count.keys()):
        avg = (per_day_alpha_sum[d] / per_day_alpha_n[d]) if per_day_alpha_n[d] else None
        out.append({"date": d, "count": per_day_count[d], "avg_alpha": avg})
    return {"horizon": horizon, "days": out}


@router.get("/decisions")
async def decisions(
    horizon: int = Query(30),
    ticker: Optional[str] = None,
    signal: Optional[str] = None,
    only_evaluable: bool = False,
    limit: int = Query(500, le=2000),
):
    """Per-decision table feeding the Quality page's lower section."""
    if horizon not in _VALID_HORIZONS:
        horizon = 30
    loop = asyncio.get_running_loop()
    rows = await loop.run_in_executor(None, _all_eval_rows, horizon)

    if ticker:
        t = ticker.upper()
        rows = [r for r in rows if r.ticker.upper() == t]
    if signal:
        s = signal.upper()
        rows = [r for r in rows if (r.signal or "").upper() == s]
    if only_evaluable:
        rows = [r for r in rows if r.evaluable]
    # Newest first for the UI.
    rows.sort(key=lambda r: r.created_at, reverse=True)
    rows = rows[:limit]

    return {
        "horizon": horizon,
        "items": [
            {
                "id": r.id,
                "ticker": r.ticker,
                "trade_date": r.trade_date,
                "signal": r.signal,
                "confidence": r.confidence,
                "analysts": r.analysts,
                "llm_provider": r.llm_provider,
                "deep_think_llm": r.deep_think_llm,
                "raw_return": r.raw_return,
                "bench_return": r.bench_return,
                "alpha": r.alpha,
                "evaluable": r.evaluable,
                "win": r.win,
                "created_at": r.created_at,
            }
            for r in rows
        ],
    }
