"""Screener run orchestration — the deterministic pipeline behind /api/screen.

Mirrors ``graph_runner.GraphRunner``: takes an ``asyncio.Queue``, pushes
progress events the WebSocket relays, and persists the result. The heavy
work (snapshot fetch, filtering, scoring) runs in an executor thread so the
event loop stays responsive; only the two LLM touch-points are optional.

Pipeline stages (each emits a progress event):
  strategy_parsed → screening → ranking → screen_complete (or error)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional

from . import database as db

logger = logging.getLogger(__name__)

# Absolute ceiling on returned candidates — a runaway guardrail, not the
# normal limit (the user picks ``top_n``, default 20). When AI rationale is
# on, ranking is chunked (see stock_screener.rank_candidates) so a large
# top_n stays reliable instead of one giant LLM prompt.
_MAX_CANDIDATES = 500


class ScreenerRunner:
    def __init__(self, run_id: str, text: str, filters: Optional[dict],
                 top_n: int, use_llm: bool, queue: asyncio.Queue):
        self.run_id = run_id
        self.text = text or ""
        self.filters = filters or {}
        self.top_n = max(1, min(int(top_n or 20), _MAX_CANDIDATES))
        self.use_llm = use_llm
        self.queue = queue
        self._loop = asyncio.get_event_loop()

    async def _emit(self, event_type: str, content: str = "", extra: Optional[dict] = None):
        event = {
            "type": event_type,
            "agent": "screener",
            "content": content,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
        if extra:
            event.update(extra)
        await self.queue.put(event)

    async def run(self):
        try:
            await self._emit("screen_start", f"开始选股: {self.text or '(默认因子)'}")
            result = await self._loop.run_in_executor(None, self._run_sync_with_events)
            await self._emit(
                "screen_complete",
                f"完成，命中 {result['matched']} 只，返回 Top {len(result['candidates'])}",
                extra={
                    "strategy": result["strategy"],
                    "candidates": result["candidates"],
                    "matched": result["matched"],
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("screen run %s failed", self.run_id)
            db.update_screen_run(self.run_id, status="error", error_msg=str(e))
            await self._emit("error", str(e))

    # The synchronous body runs in an executor thread. It emits its
    # intermediate progress via thread-safe enqueue.
    def _enqueue(self, event_type: str, content: str = "", extra: Optional[dict] = None):
        event = {
            "type": event_type, "agent": "screener", "content": content,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
        if extra:
            event.update(extra)
        self._loop.call_soon_threadsafe(self.queue.put_nowait, event)

    def _run_sync(self) -> dict:
        from tradingagents.agents.stock_screener import compile_strategy, rank_candidates
        from tradingagents.screener import universe, factors

        llm = self._build_llm() if self.use_llm else None

        # 1. Strategy ---------------------------------------------------------
        spec, provenance = compile_strategy(self.text, llm=llm)
        self._apply_filter_overrides(spec)
        # Resolve a sector hint into a concrete code universe.
        if spec.sector_query and spec.universe_codes is None:
            codes = universe.get_concept_constituents(spec.sector_query)
            if not codes:
                codes = universe.get_industry_constituents(spec.sector_query)
            if codes:
                spec.universe_codes = codes
            else:
                provenance.setdefault("notes", "")
                provenance["notes"] += f" 板块「{spec.sector_query}」未匹配到成分股，已忽略板块限定。"
        strategy_dict = {**spec.to_dict(), "provenance": provenance}
        db.update_screen_run(self.run_id, status="running", strategy=strategy_dict)
        self._enqueue("strategy_parsed", "策略已编译", extra={"strategy": strategy_dict})

        # 2. Deterministic screening -----------------------------------------
        self._enqueue("screening", "扫描全市场并粗筛中…")
        snapshot = universe.get_market_snapshot()
        meta = dict(universe.last_snapshot_meta)
        # Degraded source (e.g. 新浪) lacks PE/PB/市值/换手 — value & size
        # filters would empty the result and the value factor is meaningless.
        # Drop them and tell the user rather than returning nothing.
        if meta.get("coverage") == "partial":
            for f in ("pe_min", "pe_max", "pb_min", "pb_max",
                      "market_cap_min", "market_cap_max",
                      "turnover_min", "turnover_max"):
                setattr(spec, f, None)
            spec.weights = {"value": 0.0,
                            "momentum": spec.weights.get("momentum", 1.0) or 1.0,
                            "capital_flow": spec.weights.get("capital_flow", 0.0)}
            self._enqueue("warning",
                          f"行情来自{meta.get('source')}，缺少估值/市值数据，"
                          f"本次按动量/资金流选股（估值类条件已忽略）")
        filtered = factors.apply_filters(snapshot, spec)
        matched = len(filtered)
        cap_flow = None
        # Only fetch capital flow if it actually influences the ranking.
        if spec.weights.get("capital_flow", 0):
            cap_flow = universe.rank_capital_flow()

        # Resolve which column feeds the momentum factor for the chosen period.
        momentum_col = self._resolve_momentum_column(spec, filtered, universe, meta)

        scored = factors.score_and_rank(
            filtered, spec.weights, capital_flow=cap_flow, top_n=self.top_n,
            momentum_col=momentum_col, momentum_direction=spec.momentum_direction,
        )
        candidates = factors.to_candidates(scored)
        self._enqueue("screened",
                      f"粗筛命中 {matched} 只（数据源：{meta.get('source')}），取 Top {len(candidates)}",
                      extra={"matched": matched, "data_source": meta.get("source"),
                             "coverage": meta.get("coverage")})

        # 3. LLM ranking / rationale -----------------------------------------
        self._enqueue("ranking", "生成入选理由中…")
        candidates = rank_candidates(
            candidates, self.text, llm=llm,
            momentum_period=spec.momentum_period,
            momentum_direction=spec.momentum_direction,
        )

        db.update_screen_run(self.run_id, status="complete", candidates=candidates)
        return {"strategy": strategy_dict, "candidates": candidates, "matched": matched}

    def _run_sync_with_events(self) -> dict:
        return self._run_sync()

    _PERIOD_LABELS = {"today": "当日", "5d": "近一周", "20d": "近一月",
                      "60d": "60日", "ytd": "年初至今"}

    def _resolve_momentum_column(self, spec, filtered, universe, meta) -> str:
        """Return the dataframe column the momentum factor should rank on.

        today/60d/ytd come straight from the spot snapshot. 5d/20d aren't in
        the snapshot, so we fetch per-stock history for the *filtered* set
        (capped) and write the returns into a ``mom_period`` column. Mutates
        ``filtered`` in place for the 5d/20d case. Degrades to 当日 with a
        warning when the data isn't available.
        """
        period = spec.momentum_period or "today"
        label = self._PERIOD_LABELS.get(period, "当日")
        if period == "today":
            return "change_pct"
        if period in ("60d", "ytd"):
            col = "change_60d" if period == "60d" else "change_ytd"
            if col in filtered.columns and filtered[col].notna().any():
                return col
            self._enqueue("warning", f"{label}涨跌数据缺失（多为降级数据源），本次改用当日动量")
            return "change_pct"
        # 5d / 20d — compute from history for the filtered candidates.
        if filtered.empty:
            return "change_pct"
        days = 5 if period == "5d" else 20
        codes = filtered["code"].tolist()
        cap = universe.PERIOD_RETURN_CAP
        if len(codes) > cap:
            # Pre-narrow by today's |change| so we fetch history for the most
            # active names, and tell the user we capped.
            pre = filtered.reindex(
                filtered["change_pct"].abs().sort_values(ascending=False).index
            )
            codes = pre["code"].head(cap).tolist()
            self._enqueue("warning",
                          f"{label}动量需逐只拉历史，候选 {len(filtered)} 只超出上限，"
                          f"仅对最活跃的 {cap} 只计算（其余按当日动量）")
        self._enqueue("ranking", f"计算{label}涨跌幅中（{len(codes)} 只）…")
        returns = universe.compute_period_returns(codes, days)
        filtered["mom_period"] = filtered["code"].map(returns)
        # Rows we didn't compute (capped out / fetch failed) fall back to today.
        filtered["mom_period"] = filtered["mom_period"].fillna(filtered["change_pct"])
        return "mom_period"

    # --- helpers ---

    def _apply_filter_overrides(self, spec) -> None:
        """Explicit UI filters win over the compiled strategy."""
        allowed = {
            "pe_min", "pe_max", "pb_min", "pb_max", "market_cap_min",
            "market_cap_max", "change_pct_min", "change_pct_max",
            "turnover_min", "turnover_max", "sector_query", "exclude_st",
            "momentum_period", "momentum_direction",
        }
        for k, v in self.filters.items():
            if k in allowed and v is not None:
                setattr(spec, k, v)
        # Explicitly choosing a momentum period/direction means the user wants
        # momentum to matter — give it weight if the strategy left it at 0.
        if (self.filters.get("momentum_period") or self.filters.get("momentum_direction")) \
                and not spec.weights.get("momentum"):
            spec.weights["momentum"] = 1.5

    def _build_llm(self):
        from .routers.settings import get_effective_config
        from tradingagents.llm_clients import create_llm_client
        try:
            config = get_effective_config()
            client = create_llm_client(
                provider=config["llm_provider"],
                model=config.get("quick_think_llm") or config.get("deep_think_llm"),
                base_url=config.get("backend_url"),
            )
            return client.get_llm()
        except Exception as e:  # noqa: BLE001 — degrade to rule-only screening
            logger.warning("screener LLM unavailable, falling back to rules: %s", e)
            self._enqueue("warning", "LLM 不可用，已退回纯因子选股")
            return None
