"""Market-universe data layer for the stock screener.

Everything here scans the *whole* A-share market (or a board) in one
deterministic call and returns a normalized DataFrame / list. No LLM, no
per-ticker fan-out. Results are cached in-process with a short TTL so a
screening run (and rapid re-runs while the user tweaks filters) collapses
onto one upstream fetch — same rationale as ``paper.py``'s ``_PRICE_CACHE``.

Column names from AKShare's eastmoney endpoints drift between versions, so
every accessor maps columns by *keyword* (``_pick``) rather than exact
match — a 1.14 vs 1.16 rename won't silently produce all-NaN factors.

Note: ``import tradingagents.dataflows`` (done lazily inside the fetchers
via the package's side-effect import chain) installs the NO_PROXY
bootstrap that A-share fetches need; callers that import this module after
the web backend has started already have it in effect.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

import pandas as pd

logger = logging.getLogger(__name__)


class MarketDataUnavailable(RuntimeError):
    """Raised when an upstream A-share feed can't be reached after retries.

    Carries a user-facing Chinese message so the screener WS can show
    something actionable instead of a raw urllib3 stack trace.
    """


# Transient connection failures from eastmoney (RemoteDisconnected / RST /
# proxy hiccup) — worth retrying. ImportError etc. are not caught here.
_RETRYABLE = (ConnectionError, OSError)


def _fetch_with_retry(fn: Callable[[], object], *, label: str,
                      attempts: int = 3, base_sleep: float = 1.0) -> object:
    """Call ``fn`` with exponential backoff on transient network errors.

    eastmoney's full-market ``clist`` endpoint RST-throttles bursty clients;
    a short backoff usually clears it. Raises ``MarketDataUnavailable`` with
    a friendly message once attempts are exhausted.
    """
    last: Optional[Exception] = None
    for i in range(attempts):
        try:
            return fn()
        except _RETRYABLE as e:  # noqa: PERF203
            last = e
            wait = base_sleep * (2 ** i)
            logger.warning("%s fetch attempt %d/%d failed (%s); retrying in %.1fs",
                           label, i + 1, attempts, type(e).__name__, wait)
            if i < attempts - 1:
                time.sleep(wait)
    raise MarketDataUnavailable(
        f"行情接口暂时不可用（{label}：东方财富连接被重置）。"
        f"这是该接口的限流，请稍后重试；若持续失败，检查网络/代理是否放行 eastmoney.com。"
    ) from last

# --- tiny TTL cache -------------------------------------------------------

_CACHE: dict[str, tuple[float, object]] = {}
_CACHE_LOCK = threading.Lock()
_DEFAULT_TTL = 300.0  # 5 min — market snapshot doesn't need to be tick-fresh


def _cached(key: str, ttl: float, producer: Callable[[], object]) -> object:
    """Return ``producer()`` memoized under ``key`` for ``ttl`` seconds.

    The producer runs *outside* the lock so a slow network fetch doesn't
    block readers of other keys; a brief double-fetch on a cold cache is
    acceptable and far cheaper than serializing every screen run.
    """
    now = time.monotonic()
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if entry and now < entry[0]:
            return entry[1]
    value = producer()
    with _CACHE_LOCK:
        _CACHE[key] = (now + ttl, value)
    return value


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


# --- column helpers -------------------------------------------------------

def _pick(df: pd.DataFrame, *keywords: str) -> Optional[str]:
    """First column whose name contains any of ``keywords`` (in order)."""
    for kw in keywords:
        for col in df.columns:
            if kw in str(col):
                return col
    return None


def _num(series: Optional[pd.Series]) -> pd.Series:
    if series is None:
        return pd.Series(dtype="float64")
    return pd.to_numeric(series, errors="coerce")


# --- akshare access (lazy import so module import stays cheap) -------------

def _ak():
    import tradingagents.dataflows  # noqa: F401 — NO_PROXY bootstrap
    import akshare as ak
    return ak


# --- snapshot: multi-source with sticky failover --------------------------

# Metadata about the snapshot the last successful fetch came from. The runner
# reads this to tell the user which vendor served the data and whether factor
# coverage was degraded.
last_snapshot_meta: dict = {"source": None, "coverage": None}

# Index of the source that last succeeded — tried first next time so we don't
# keep hammering a throttled primary ("来回换": stick to what works, fall
# through again only when it too starts failing).
_last_good_source = 0


def _normalize_em(raw: pd.DataFrame) -> pd.DataFrame:
    """Normalize an eastmoney spot frame (full or per-exchange — same fields)."""
    out = pd.DataFrame()
    out["code"] = raw[_pick(raw, "代码")].astype(str).str.replace(r"\D", "", regex=True).str.zfill(6)
    out["name"] = raw[_pick(raw, "名称")].astype(str)
    out["price"] = _num(raw[_pick(raw, "最新价")])
    out["change_pct"] = _num(raw[_pick(raw, "涨跌幅")])
    tc = _pick(raw, "换手率")
    out["turnover"] = _num(raw[tc]) if tc else float("nan")
    out["amount"] = _num(raw[_pick(raw, "成交额")])
    pe = _pick(raw, "市盈率")
    out["pe"] = _num(raw[pe]) if pe else float("nan")
    pb = _pick(raw, "市净率")
    out["pb"] = _num(raw[pb]) if pb else float("nan")
    mc_col = _pick(raw, "总市值")
    mc = _num(raw[mc_col]) if mc_col else pd.Series(index=raw.index, dtype="float64")
    cmc_col = _pick(raw, "流通市值")
    cmc = _num(raw[cmc_col]) if cmc_col else pd.Series(index=raw.index, dtype="float64")
    out["market_cap"] = (mc / 1e8).round(2)
    out["circ_market_cap"] = (cmc / 1e8).round(2)
    # Period returns eastmoney ships in the spot payload — free, no extra
    # fetch. 5d/20d are NOT here (computed separately from history).
    c60 = _pick(raw, "60日涨跌幅")
    out["change_60d"] = _num(raw[c60]) if c60 else float("nan")
    cytd = _pick(raw, "年初至今涨跌幅")
    out["change_ytd"] = _num(raw[cytd]) if cytd else float("nan")
    return out


def _snap_em_full() -> pd.DataFrame:
    """Primary: one shot for the whole market (richest, most RST-prone)."""
    raw = _fetch_with_retry(lambda: _ak().stock_zh_a_spot_em(), label="全市场快照(东财)")
    return _normalize_em(raw)


def _snap_em_by_exchange() -> pd.DataFrame:
    """Fallback: same eastmoney data, split into 3 lighter per-exchange calls.

    The single full-market ``clist`` is what eastmoney throttles hardest;
    the per-exchange endpoints page less and often succeed when it doesn't.
    """
    ak = _ak()
    parts = []
    for fn_name, label in (("stock_sh_a_spot_em", "沪A"),
                           ("stock_sz_a_spot_em", "深A"),
                           ("stock_bj_a_spot_em", "北A")):
        fn = getattr(ak, fn_name, None)
        if fn is None:
            continue
        parts.append(_normalize_em(_fetch_with_retry(fn, label=f"快照{label}", attempts=2)))
    if not parts:
        raise MarketDataUnavailable("分交易所快照接口均不可用")
    return pd.concat(parts, ignore_index=True).drop_duplicates(subset="code")


def _snap_sina() -> pd.DataFrame:
    """Last resort: 新浪 spot. DEGRADED — no PE/PB/市值/换手.

    Only price/change/amount are available, so value/size filters can't run.
    Marked ``coverage='partial'`` so the runner warns the user.
    """
    raw = _fetch_with_retry(lambda: _ak().stock_zh_a_spot(), label="全市场快照(新浪)", attempts=2)
    out = pd.DataFrame()
    out["code"] = raw[_pick(raw, "代码", "symbol")].astype(str).str.replace(r"\D", "", regex=True).str.zfill(6)
    out["name"] = raw[_pick(raw, "名称", "name")].astype(str)
    out["price"] = _num(raw[_pick(raw, "最新价", "trade")])
    out["change_pct"] = _num(raw[_pick(raw, "涨跌幅", "changepercent")])
    out["amount"] = _num(raw[_pick(raw, "成交额", "amount")])
    for col in ("turnover", "pe", "pb", "market_cap", "circ_market_cap",
                "change_60d", "change_ytd"):
        out[col] = float("nan")
    return out


# (name, producer, coverage) in failover order.
_SNAPSHOT_SOURCES = [
    ("东方财富(全量)", _snap_em_full, "full"),
    ("东方财富(分交易所)", _snap_em_by_exchange, "full"),
    ("新浪(降级)", _snap_sina, "partial"),
]


# --- public accessors -----------------------------------------------------

def get_market_snapshot(ttl: float = _DEFAULT_TTL) -> pd.DataFrame:
    """Full A-share spot snapshot, normalized, with multi-source failover.

    Columns: ``code, name, price, change_pct, turnover, amount,
    pe, pb, market_cap, circ_market_cap`` — market caps in 亿元 (1e8 CNY).
    Tries sources in ``_SNAPSHOT_SOURCES`` order (starting from the last
    one that worked), retrying each; raises ``MarketDataUnavailable`` only
    when every source is exhausted. Suspended rows (no price) are dropped.
    """
    def _produce() -> pd.DataFrame:
        global _last_good_source
        n = len(_SNAPSHOT_SOURCES)
        order = [(_last_good_source + i) % n for i in range(n)]
        errors = []
        for idx in order:
            name, producer, coverage = _SNAPSHOT_SOURCES[idx]
            try:
                out = producer()
            except Exception as e:  # noqa: BLE001 — try the next source
                logger.warning("snapshot source %s failed: %s", name, e)
                errors.append(f"{name}: {type(e).__name__}")
                continue
            out = out[out["price"].notna() & (out["price"] > 0)].reset_index(drop=True)
            _last_good_source = idx
            last_snapshot_meta["source"] = name
            last_snapshot_meta["coverage"] = coverage
            logger.info("market snapshot: %d rows via %s (%s)", len(out), name, coverage)
            return out
        raise MarketDataUnavailable(
            "所有行情源均不可用（" + "；".join(errors) + "）。"
            "多为东方财富/新浪限流，请稍后重试，或检查网络/代理是否放行 eastmoney.com、sina.com.cn。"
        )

    return _cached("snapshot", ttl, _produce)  # type: ignore[return-value]


def list_concepts(ttl: float = _DEFAULT_TTL) -> pd.DataFrame:
    """Concept boards ranked by today's change, with fund-flow if present.

    Columns: ``name, change_pct, turnover, market_cap`` (亿元 where available).
    """
    def _produce() -> pd.DataFrame:
        raw = _fetch_with_retry(lambda: _ak().stock_board_concept_name_em(), label="概念板块")
        out = pd.DataFrame()
        out["name"] = raw[_pick(raw, "板块名称", "名称")].astype(str)
        out["change_pct"] = _num(raw[_pick(raw, "涨跌幅")])
        tc = _pick(raw, "换手率")
        out["turnover"] = _num(raw[tc]) if tc else None
        mc = _pick(raw, "总市值")
        out["market_cap"] = (_num(raw[mc]) / 1e8).round(2) if mc else None
        return out.sort_values("change_pct", ascending=False).reset_index(drop=True)

    return _cached("concepts", ttl, _produce)  # type: ignore[return-value]


def list_industries(ttl: float = _DEFAULT_TTL) -> pd.DataFrame:
    """Industry boards ranked by today's change. Same shape as concepts."""
    def _produce() -> pd.DataFrame:
        raw = _fetch_with_retry(lambda: _ak().stock_board_industry_name_em(), label="行业板块")
        out = pd.DataFrame()
        out["name"] = raw[_pick(raw, "板块名称", "名称")].astype(str)
        out["change_pct"] = _num(raw[_pick(raw, "涨跌幅")])
        tc = _pick(raw, "换手率")
        out["turnover"] = _num(raw[tc]) if tc else None
        mc = _pick(raw, "总市值")
        out["market_cap"] = (_num(raw[mc]) / 1e8).round(2) if mc else None
        return out.sort_values("change_pct", ascending=False).reset_index(drop=True)

    return _cached("industries", ttl, _produce)  # type: ignore[return-value]


def get_concept_constituents(name: str, ttl: float = _DEFAULT_TTL) -> list[str]:
    """6-digit codes belonging to a concept board (empty list on failure)."""
    def _produce() -> list[str]:
        try:
            raw = _fetch_with_retry(
                lambda: _ak().stock_board_concept_cons_em(symbol=name),
                label=f"概念成分:{name}", attempts=2,
            )
        except Exception as e:  # noqa: BLE001 — board name typos are common, non-fatal
            logger.warning("concept constituents fetch failed for %r: %s", name, e)
            return []
        col = _pick(raw, "代码")
        return raw[col].astype(str).str.zfill(6).tolist() if col else []

    return _cached(f"concept_cons:{name}", ttl, _produce)  # type: ignore[return-value]


def get_industry_constituents(name: str, ttl: float = _DEFAULT_TTL) -> list[str]:
    """6-digit codes belonging to an industry board (empty list on failure)."""
    def _produce() -> list[str]:
        try:
            raw = _fetch_with_retry(
                lambda: _ak().stock_board_industry_cons_em(symbol=name),
                label=f"行业成分:{name}", attempts=2,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("industry constituents fetch failed for %r: %s", name, e)
            return []
        col = _pick(raw, "代码")
        return raw[col].astype(str).str.zfill(6).tolist() if col else []

    return _cached(f"industry_cons:{name}", ttl, _produce)  # type: ignore[return-value]


def rank_capital_flow(ttl: float = _DEFAULT_TTL) -> pd.DataFrame:
    """Per-stock main-capital net inflow, today. Indexed for joins.

    Columns: ``code, main_net_inflow`` (净额, 元) and ``main_net_pct``
    (净占比, %). Empty DataFrame on failure so callers can left-join safely.
    """
    def _produce() -> pd.DataFrame:
        try:
            raw = _fetch_with_retry(
                lambda: _ak().stock_individual_fund_flow_rank(indicator="今日"),
                label="个股资金流", attempts=2,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("capital flow rank fetch failed: %s", e)
            return pd.DataFrame(columns=["code", "main_net_inflow", "main_net_pct"])
        out = pd.DataFrame()
        out["code"] = raw[_pick(raw, "代码")].astype(str).str.zfill(6)
        out["main_net_inflow"] = _num(raw[_pick(raw, "主力净流入-净额", "主力净流入")])
        pct = _pick(raw, "主力净流入-净占比", "净占比")
        out["main_net_pct"] = _num(raw[pct]) if pct else None
        return out

    return _cached("capital_flow", ttl, _produce)  # type: ignore[return-value]


# --- N-day return (5d/20d) — computed per stock from history ---------------

# Cap on how many tickers we'll fetch history for in one screen. 5d/20d
# aren't in the spot snapshot, so each needs a hist call — bounded so a
# market-wide momentum screen doesn't fan out to thousands of requests.
PERIOD_RETURN_CAP = 300

_RET_CACHE: dict[str, tuple[float, Optional[float]]] = {}
_RET_CACHE_LOCK = threading.Lock()
_RET_CACHE_TTL = 600.0  # 10 min


def _period_return_one(code: str, days: int) -> Optional[float]:
    """N-trading-day % return for one code via the robust vendor router.

    Reuses ``dataflows.interface.route_to_vendor('get_stock_data', ...)``,
    which already falls back eastmoney → sina → tencent per stock, so this
    works even when the eastmoney spot endpoint is throttled. Cached per
    (code, days). Returns None when history is too short / unavailable.
    """
    key = f"{code}:{days}"
    now = time.monotonic()
    with _RET_CACHE_LOCK:
        hit = _RET_CACHE.get(key)
        if hit and now < hit[0]:
            return hit[1]

    val: Optional[float] = None
    try:
        import io
        from datetime import datetime, timedelta
        import pandas as _pd
        from tradingagents.dataflows.interface import route_to_vendor
        end = datetime.now()
        # Pad calendar days generously to cover weekends/holidays for `days`
        # trading sessions.
        start = end - timedelta(days=days * 2 + 12)
        csv_str = route_to_vendor("get_stock_data", code,
                                  start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
        if isinstance(csv_str, str):
            hdr = csv_str.find("\n\n")
            data = csv_str[hdr + 2:] if hdr != -1 else csv_str
            df = _pd.read_csv(io.StringIO(data))
            if not df.empty and "Close" in df.columns:
                closes = df["Close"].dropna().tolist()
                if len(closes) > days and closes[-1 - days]:
                    val = round((closes[-1] / closes[-1 - days] - 1) * 100, 2)
    except Exception as e:  # noqa: BLE001 — missing history is non-fatal
        logger.debug("period return fetch failed for %s: %s", code, e)

    with _RET_CACHE_LOCK:
        _RET_CACHE[key] = (now + _RET_CACHE_TTL, val)
    return val


def compute_period_returns(codes: list[str], days: int,
                           max_workers: int = 8) -> dict[str, Optional[float]]:
    """Map code → N-day % return, fetched concurrently (capped, cached).

    Caller is responsible for pre-narrowing ``codes`` to a sensible set;
    this hard-caps at ``PERIOD_RETURN_CAP`` regardless and logs if it had
    to truncate (no silent coverage loss).
    """
    from concurrent.futures import ThreadPoolExecutor

    unique = list(dict.fromkeys(codes))
    if len(unique) > PERIOD_RETURN_CAP:
        logger.info("period returns: capping %d → %d codes", len(unique), PERIOD_RETURN_CAP)
        unique = unique[:PERIOD_RETURN_CAP]
    out: dict[str, Optional[float]] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for code, ret in zip(unique, ex.map(lambda c: _period_return_one(c, days), unique)):
            out[code] = ret
    return out
