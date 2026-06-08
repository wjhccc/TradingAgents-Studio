# Changelog

All notable changes to **TradingAgents-Studio** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Breaking changes within the 0.x line are called out explicitly.

> Versions ≤ `0.2.5` were released upstream as
> [TradingAgents](https://github.com/TauricResearch/TradingAgents) by Tauric
> Research and are listed below for historical context. Version `0.3.0` and
> later are released under the **TradingAgents-Studio** name as an
> independent fork distributed under Apache License 2.0.

## [Unreleased]

_No changes yet._

## [Studio 0.5.0] — 2026-06-08

Concurrency overhaul. The headline change is that analysts within a single
analysis now run **in parallel** instead of one after another, and the whole
stack was hardened for running **many tickers at once** (batch analyze, the
screener's "→ analyze" handoff, and interval auto-trading schedules).

### Added

- **Parallel analysts.** The analyst stage used to be a serial chain
  (`Market → Sentiment → News → …`), so a 6-analyst run paid the sum of all
  six LLM round-trips. They now fan out and run concurrently inside one graph
  step, converging on the Bull Researcher — single-analysis wall time drops by
  roughly 50–70% depending on how many analysts are selected. Each analyst's
  ReAct loop runs over an isolated message workspace (`graph/analyst_runner.py`)
  so their tool calls no longer need the old serial "Msg Clear" separation;
  only the report keys are merged back. Per-analyst completion still streams to
  the UI in real time, now as each one finishes rather than all at the end.
- **Global LLM concurrency throttle + 429 backoff** (`llm_clients/throttle.py`).
  A single process-wide semaphore caps *total* simultaneous LLM requests across
  every analyst of every concurrently-running analysis, so a multi-ticker burst
  can't trip the provider's rate limit. Rate-limit (HTTP 429) errors retry with
  exponential backoff + jitter, with the backoff happening outside the
  semaphore so a waiting retry doesn't hold a slot. Wrapped at the chat model's
  `_generate` so it covers both direct calls and the tool-calling path without
  disturbing callback-based token tracking.
- **New concurrency env knobs** (documented in `.env.example`):
  `TRADINGAGENTS_LLM_CONCURRENCY` (default 16),
  `TRADINGAGENTS_LLM_MAX_RETRIES` (default 5), and
  `TRADINGAGENTS_ANALYST_CONCURRENCY` (default 0 = no cap, all selected
  analysts at once).

### Changed

- **`analyst_concurrency_limit` is now live.** Previously threaded through the
  config and execution plan but never used (dead code); it now caps the analyst
  thread pool. `0` means unbounded (run every selected analyst at once) and is
  the new default.
- **Web SQLite uses per-thread connection reuse** (`web/backend/database.py`).
  Request-path DB calls run on the default executor's thread pool; each call
  used to open a fresh connection, run `PRAGMA journal_mode=WAL`, then close.
  WAL (a persistent DB property) is now set once at `init_db()`; connections are
  reused per thread (keyed by DB path) with `synchronous=NORMAL` and a 30s busy
  timeout, cutting per-call setup cost.

### Fixed

- **Checkpoint DB "database is locked" under concurrent same-ticker runs.** The
  per-ticker checkpoint connection (`graph/checkpointer.py`) had no timeout and
  no WAL, so a scheduled fire overlapping a manual trigger (or interval
  re-fires) on the same ticker could fail outright. Now opens with WAL,
  `synchronous=NORMAL`, and a 30s busy timeout.
- **Background analysis tasks could be garbage-collected mid-run.** The
  scheduler, the analyze router, and the screener spawned fire-and-forget
  `asyncio.create_task()` without retaining the task; asyncio only holds a weak
  reference, so a mid-flight run could be dropped by the GC. All such spawns now
  go through a helper that keeps a strong ref until completion.
- **Alpha Vantage fetches reuse one HTTP session** instead of opening a fresh
  TCP/TLS connection per request — relevant now that analysts hit it from
  multiple threads.


## [Studio 0.4.0] — 2026-05-22

This section accumulates changes made on top of `Studio 0.3.0`. They will
be rolled into a versioned release when the surface stabilises.

### Added

- **Decision-quality dashboard (`/quality`).** Replays every completed
  Agent decision against real historical prices and answers the question
  the rest of the UI dodges: *"is the Agent actually making good calls?"*
  For each analysis, the page pulls the close on the analysis date and N
  trading days later (5 / 30 / 60, user-toggled) from the same vendor the
  agents used, subtracts the regional benchmark return over the same
  window, and reports **alpha**. KPIs include overall win-rate, average
  & median alpha, alpha-Sharpe, best/worst alpha; the page also surfaces:
  - **Confidence calibration curve** — buckets directional decisions by
    reported confidence and plots actual win-rate vs. the bucket center.
    Lets users spot over- or under-confident agents at a glance.
  - **Breakdown by dimension** — ticker / signal / single analyst /
    analyst combo / LLM model, each with win-rate + avg alpha + per-
    decision Sharpe so users can see which configuration of analysts +
    LLM actually wins. The "by analyst" mode counts a single analysis
    once per analyst it used, so e.g. "did adding `capital_flow` to my
    runs improve alpha?" gets a direct answer.
  - **Per-day heatmap** — calendar-grid color-coded by that day's
    average alpha. Red = positive (CN convention), green = negative,
    saturation clips at ±10%.
  - **Decision list** — every analysis with realised return / alpha /
    win-loss, filterable by ticker + signal + only-evaluable, deep-
    linked to the source Report Detail.
  Benchmarks are auto-picked per market (CSI 300 for A-share, HSI for
  HK, SPY for US, N225 for Japan, etc.), matching the same logic the
  reflection layer uses. Computed on demand — no new tables; price
  series cached in-process (10 min TTL) so even a full-history rebuild
  only hits the vendor a few times per ticker. HOLD decisions are listed
  but excluded from the win-rate aggregates since there's no implied
  position.
  - New: `web/backend/routers/quality.py`
  - New: `web/frontend/src/pages/Quality.vue`
  - Modified: `web/backend/main.py` (register router)
  - Modified: `web/frontend/src/router.ts`, `src/App.vue` (route + menu)
  - Modified: `web/frontend/src/i18n/locales/zh-CN.ts`, `en-US.ts`
- **Backtesting engine — Phase 1: Agent historical decision replay.** A
  self-contained event-driven backtest framework written from scratch
  (no vnpy / backtrader / zipline / AI-Trader code; engine architecture
  is original, only the pattern of "bar advance → portfolio book →
  metrics" is borrowed). The default signal source replays Studio's
  persisted Agent decisions from the web SQLite layer, so every backtest
  answers the question "if I'd followed the Agents' Buy/Sell signals
  over this period, what would my net worth look like?" — with zero LLM
  cost.
  - Engine: `tradingagents/backtesting/engine.py` (BacktestEngine,
    BacktestConfig, BacktestResult; daily bar advance with fills at
    next-open).
  - Portfolio bookkeeping: `tradingagents/backtesting/portfolio.py`
    (PortfolioBook with cash, positions, FIFO round-trip pairing).
  - Cost model: `tradingagents/backtesting/slippage.py` (A-share preset
    with 0.025% commission + 0.05% stamp duty on sells + 5bp slippage;
    US preset commission-free + 5bp slippage; auto-picked per ticker).
  - Metrics: `tradingagents/backtesting/metrics.py` (total / annualised
    return, max drawdown, Sharpe, Sortino, Calmar, volatility, win rate,
    avg win/loss %, profit factor; alpha vs configurable benchmark).
  - Signal source: `tradingagents/backtesting/signals/from_memory_log.py`
    queries the `analyses` table for completed runs and emits one Signal
    per row, with the analysis_id surfaced in trade metadata so the UI
    can drill back to the originating decision.
  - REST: `web/backend/routers/backtest.py` (POST /run synchronous,
    GET list / detail / curve / trades, DELETE; universe discovery).
  - DB tables: `backtest_runs`, `backtest_trades`, `backtest_nav` with
    cascading deletes.
  - UI: `web/frontend/src/pages/Backtest.vue` + `BacktestDetail.vue`
    — config modal, runs table with inline metrics, detail page with
    8 KPI cards + NAV curve overlaying benchmark + trade-by-trade
    table that links back to source analyses.

- **Scheduled analyses.** Recurring background analyses (interval / daily /
  weekly) that auto-run on the configured cadence, reuse the analyst /
  LLM config from creation time, and auto-disable after 3 consecutive
  failures. The scheduler is an asyncio loop bound to the FastAPI
  lifespan (no external job queue). Schedules can be bulk-created from
  the Holdings page in one click. Failed runs don't roll forward forever
  — a >24h missed fire is skipped, not retried, to avoid LLM spam after
  an outage.
  - New: `web/backend/scheduler.py`, `web/backend/routers/schedule.py`
  - New: `web/frontend/src/pages/Schedule.vue`
  - Modified: `web/backend/database.py` (new `schedules` table + CRUD)
  - Modified: `web/backend/main.py` (lifespan starts/stops the scheduler)
  - Modified: `web/backend/models.py` (`ScheduleCreate`/`Update`/`FromHoldings`)
  - Modified: `web/frontend/src/pages/Holdings.vue` (one-click "加入定时分析")
  - Modified: `web/frontend/src/App.vue`, `router.ts`
- **Paper trading (模拟交易).** A virtual account with cash, positions,
  orders, and daily NAV snapshots. Three entry points: manual order,
  one-click "按此决策模拟下单" from a completed analysis (parses the
  trader proposal's Action + Entry Price + position-sizing flags), and
  the existing scheduled analyses. Position sizing supports both fixed
  share count and cash-fraction. Order history records the source so
  decision-driven orders link back to their analysis. Account reset
  wipes positions/orders/NAV and restores initial cash.
  - New: `web/backend/routers/paper.py`
  - New: `web/frontend/src/pages/Paper.vue`
  - Modified: `web/backend/database.py` (new `paper_accounts`,
    `paper_positions`, `paper_orders`, `paper_nav` tables)
  - Modified: `web/backend/models.py` (`PaperOrderRequest`,
    `PaperOrderFromDecision`, `PaperAccountReset`)
  - Modified: `web/frontend/src/pages/ReportDetail.vue` (按决策下单 modal)
- **K-line chart panel** (基于 klinecharts). Per-ticker drawer launched
  from Holdings and Paper Trading rows, with:
  - **Daily bars** for any market (AKShare for A-share, yfinance for
    overseas), default 60-day lookback with 30 / 60 / 120 / 250-day
    toggles.
  - **Intraday 1 / 5 / 15 / 30 / 60-minute bars** for A-share via
    AKShare's `stock_zh_a_hist_min_em` endpoint (free, ~30-60s lag).
  - **Live refresh** — auto-refreshes every 30s on minute bars and 60s
    on daily bars during A-share trading hours; manual mode also
    available. Refresh stops outside trading sessions to save bandwidth.
  - **A-share spot patching** for the rightmost daily bar: during the
    trading session the bar's High/Low/Close/Volume are merged with
    `stock_bid_ask_em` quotes (~3-5s lag) so today's bar matches the
    current tape.
  - **Time-zone correctness** — A-share bars are localised to
    Asia/Shanghai so klinecharts in a CST browser renders the date
    correctly (not UTC midnight = 08:00 CST).
  - **MA(5/10/20) + Volume overlays**, **fullscreen toggle** (ESC to
    exit), and optional **entry/target/stop reference lines** when
    launched from a position with cost-basis context.
  - New: `web/frontend/src/components/KLineChart.vue`
  - New: `web/backend/routers/quote.py`
  - Modified: `web/frontend/package.json` (adds `klinecharts`)
- **Capital flow analyst (`capital_flow`).** New A-share analyst that
  surfaces institutional capital movement: per-ticker main-force net
  flow, northbound (沪深港通) net buy, margin balance (融资融券余额),
  and the daily top-stocks billboard (龙虎榜). Pre-fetch pattern (no
  tool calling) — defensive: each data source can fail independently
  without blocking the report.
  - New: `tradingagents/agents/analysts/capital_flow_analyst.py`
  - New: `tradingagents/dataflows/capital_flow.py`
- **Macro analyst (`macro`).** New top-down analyst that pre-fetches
  China CPI / PPI / M2 / PMI (manufacturing + non-manufacturing) / LPR /
  USDCNY and US 10-year treasury yield, then maps the regime to sector
  preferences and the analysed ticker's tilt.
  - New: `tradingagents/agents/analysts/macro_analyst.py`
  - New: `tradingagents/dataflows/macro.py`
- **Decision card fields** — `target_price`, `stop_loss`, and `core_risk`
  added to `TraderProposal` and `PortfolioDecision` schemas. These power
  the paper-trading "from decision" flow and let the K-line chart draw
  reference lines for each price level.
  - Modified: `tradingagents/agents/schemas.py`
- **Bilingual disclaimer in README.** Top-of-file pinned banner plus a
  dedicated "Disclaimer / 完整免责声明" section at the bottom, making
  explicit that the project does not recommend any stock, is for research
  / education only, and must not be used for public investment advisory
  services.
  - Modified: `README.md`

### Changed

- **Bull/Bear researchers and Portfolio Manager** now see the macro and
  capital-flow reports (when present) in their prompt context, so the
  debate can reference flow / macro signals.
  - Modified: `tradingagents/agents/researchers/bull_researcher.py`,
    `bear_researcher.py`
- **GraphRunner** maps the two new analyst report keys
  (`capital_flow_report`, `macro_report`) and emits the corresponding
  `agent_complete` events, so the live progress page surfaces them too.
  - Modified: `web/backend/graph_runner.py`
- **AgentState** declares the two new report fields, and
  `Propagator.create_initial_state` initialises them.
  - Modified: `tradingagents/agents/utils/agent_states.py`,
    `tradingagents/graph/propagation.py`
- **Report Detail page** tabs reordered to lead with `macro` →
  `market` → ... → `capital_flow` → `event` → debates → decisions.
  - Modified: `web/frontend/src/pages/ReportDetail.vue`
- **New Analysis page** checkbox grid adds the two new analysts.
  - Modified: `web/frontend/src/pages/NewAnalysis.vue`

### Fixed

- **HTTP-proxy interception of CN data fetches.** When Clash / V2Ray /
  VPN set `HTTP_PROXY` globally, A-share data calls to eastmoney /
  sina / tushare were being routed through the proxy and dropped with
  `ProxyError`. The fix unconditionally appends a curated list of CN
  financial domains to `NO_PROXY` at first import of
  `tradingagents.dataflows`, so `requests` bypasses the proxy for those
  hosts. Doesn't affect overseas API calls (yfinance, OpenAI, etc.).
  Diagnostic endpoint `GET /api/quote/_diagnose` returns the effective
  proxy state and a direct-connect smoke test, so users can self-
  diagnose TUN-mode proxy interception (which Python can't bypass from
  inside the process).
  - New: `tradingagents/dataflows/_proxy.py`
  - Modified: `tradingagents/dataflows/__init__.py` (apply at import)
  - Modified: `web/backend/main.py` (import order)
- **K-line timestamps off by 8 hours on A-share** — historical bars were
  being stamped at the timezone-naive 00:00 which a CST browser then
  rendered as the previous day's 16:00. A-share bars are now explicitly
  localised to `Asia/Shanghai` before being serialised as ms-since-epoch.


## [Studio 0.3.0] — 2026-05-21

Initial release of **TradingAgents-Studio**, a community fork of
[TradingAgents 0.2.5](https://github.com/TauricResearch/TradingAgents) with
A-share localization and a visual web workbench. All upstream functionality
through 0.2.5 is preserved.

### Added

- **A-share sentiment analyst (`cn_social`).** A new LangGraph analyst node
  that aggregates Chinese retail-investor sentiment for A-share tickers.
  Pulls discussions from 东方财富股吧 (HTTP-only, no credentials required)
  and optionally from Weibo / Xiaohongshu / Douyin via a
  [MediaCrawler](https://github.com/NanmiCoder/MediaCrawler) MySQL backend.
  Degrades gracefully to 股吧-only when MySQL is unreachable. Emits a
  Chinese-language sentiment report grounded in real posts.
  - New: `tradingagents/agents/analysts/cn_sentiment_analyst.py`
  - New: `tradingagents/dataflows/cn_sentiment.py`
  - New: `tradingagents/dataflows/eastmoney_guba.py`
  - New: `tradingagents/dataflows/mediacrawler_wrapper.py`
- **Event-driven analyst (`event`).** A new analyst that pre-fetches news
  around the trade date and lets the LLM reason about causal chains
  (event → sector → instrument). No hardcoded keyword dictionary; the
  causal logic is generated by the model from real headlines.
  - New: `tradingagents/agents/analysts/event_analyst.py`
  - New: `tradingagents/dataflows/event_intelligence.py`
- **Web Studio.** A Vue 3 + Naive UI workbench backed by FastAPI for
  launching analyses, streaming live agent progress over WebSocket,
  browsing the decision history, and viewing per-run Markdown reports.
  - Backend: `web/backend/` (FastAPI + SQLite + `graph_runner` bridge to
    the LangGraph engine; routers for `analyze`, `history`, `dashboard`,
    `settings`).
  - Frontend: `web/frontend/` (Vue 3 + TypeScript + Vite + Naive UI +
    Pinia + Vue Router + Chart.js + marked).
  - Pages: Dashboard · New Analysis · Analysis Progress (live) · History
    · Report Detail · Settings.
- **SQLite-backed run persistence for the web layer.** Stores runs, per-
  agent events, and user settings independently from the upstream
  `~/.tradingagents/memory/trading_memory.md` decision log so the web UI
  can browse historical analyses without touching CLI state.
- **`tradingagents-web` entry point** in `pyproject.toml` for launching
  the backend (`python -m web.backend.run`).
- **`[web]` optional dependency group** (FastAPI, Uvicorn, websockets).
- **MediaCrawler dependencies** (`pymysql`, `beautifulsoup4`, `lxml`) in
  the base install for A-share data ingestion.
- **Apache 2.0 §4(b) Modified-by notices** on every source file changed
  relative to upstream, so the provenance of each modification is
  recoverable from the source itself.

### Changed

- **Package name.** `tradingagents` → `tradingagents-studio` in
  `pyproject.toml`. The installed CLI command remains `tradingagents` for
  user-facing compatibility.
- **Version baseline.** Reset to `0.3.0` to mark the fork point off
  upstream `0.2.5`.
- **README rewritten** as TradingAgents-Studio with clear fork
  attribution, tech-stack section, CLI + Web + Docker quick-start, and
  the original arXiv citation preserved under "Upstream credits."
- **`graph/setup.py`** registers the two new analyst keys (`cn_social`,
  `event`) in the analyst factory.
- **`graph/trading_graph.py` / `graph/propagation.py`** accept
  `cn_sentiment_config` for the MediaCrawler connection and thread it to
  the analyst factory.
- **Researcher prompts** (`bull_researcher.py`, `bear_researcher.py`)
  surface the new analyst reports when present, so the bull/bear debate
  can reference 股吧/事件 signals.

### Notes on this fork

- TradingAgents-Studio is **not affiliated with, endorsed by, or
  sponsored by Tauric Research**. The upstream "TradingAgents" name and
  logo remain the property of their respective owners; this fork is
  published under a distinct, derivative name to avoid confusion.
- The upstream agent architecture, paper, and decision log are
  attributed in `README.md` under "Upstream credits."
- Third-party data sources called by the new analysts (东方财富,
  MediaCrawler, Weibo/Xiaohongshu/Douyin) retain their own licenses and
  terms of service. Users are responsible for compliance.

---

## [0.2.5] — 2026-05-11

### Added

- **Grounded Sentiment Analyst.** The renamed `sentiment_analyst` now reads
  real Yahoo News, StockTwits, and Reddit data before generating its report,
  replacing the prior flow that could fabricate social posts under prompt
  pressure. (#557, #607)
- **MiniMax provider** with the full M2.x catalog (M2.7 / M2.5 / M2.1 / M2
  plus highspeed variants, 204K context). Dual-region: Global
  (`MINIMAX_API_KEY`) and China (`MINIMAX_CN_API_KEY`).
- **Dual-region Qwen and GLM** with separate keys per region — international
  (`DASHSCOPE_API_KEY`, `ZHIPU_API_KEY`) and China (`DASHSCOPE_CN_API_KEY`,
  `ZHIPU_CN_API_KEY`), selectable via a secondary region prompt. (#758)
- **`TRADINGAGENTS_*` env-var configurability for `DEFAULT_CONFIG`.** Override
  `llm_provider`, deep/quick model IDs, `backend_url`, `output_language`,
  debate-round counts, checkpoint flag, and benchmark ticker via `.env` with
  type-aware coercion (string / int / bool). (#602)
- **Interactive API-key detection in the CLI.** When the selected provider's
  key is missing, the CLI prompts for it and persists the value to `.env`
  so the analysis run continues without restart.
- **Remote Ollama support.** `OLLAMA_BASE_URL` points the CLI and the
  programmatic client at a remote `ollama-serve`. The CLI surfaces the
  resolved endpoint and warns on common malformed inputs. Adds a
  `"Custom model ID"` option for models pulled via `ollama pull`. (#648, #768)
- **Configurable news-fetch parameters** in `DEFAULT_CONFIG` — per-ticker
  article limit, macro headline limit, lookback window, and macro search
  queries. (#606, #683)
- **Configurable alpha benchmark** for non-US tickers. Replaces hardcoded
  SPY with regional indices for `.NS` (^NSEI), `.T` (^N225), `.HK` (^HSI),
  `.L` (^FTSE), `.TO` (^GSPTSE), `.AX` (^AXJO), `.BO` (^BSESN); explicit
  `benchmark_ticker` override available. Eliminates FX drift dominating
  alpha for non-USD listings. (#628, #684)
- **Multi-language output covers every user-facing agent** — researchers,
  risk debators, research manager, and trader, ending the previous
  partial-localization reports. (#575)
- **Model catalog refresh.** OpenAI GPT-5.5 frontier, Anthropic Claude Opus
  4.7, Gemini 3.1 Flash-Lite GA, xAI Grok 4.20, Qwen 3.6 line. Versioned IDs
  only; auto-shifting aliases moved to the `"Custom model ID"` option.

### Changed

- **Sentiment Analyst** is now consistently named across the CLI dropdown,
  status panel, and final reports (previously the backend was renamed but
  the CLI still said "Social Analyst"). The `AnalystType.SOCIAL = "social"`
  wire value is kept for saved-config back-compat.

### Fixed

- **Structured output works on DeepSeek V4 / reasoner and MiniMax M2.x.**
  Those providers reject `tool_choice` per their tool-calling docs; the
  binding flow now skips it automatically via a capability table.
- **`pip install .` installations pick up the project `.env`** when running
  the CLI as a console script. (#747)
- **Reports save end-to-end** — streamed chunks were previously dropped from
  `complete_report.md`. (#719, #736)
- **Ticker prompt preserves exchange suffixes** (`.SH`, `.SZ`, `.SS`, `.HK`,
  `.T`, etc.) for A-share, HK, Tokyo, and other non-US flows. (#770)
- **Docker permission errors** no longer block first-run write to
  `~/.tradingagents/`. (#519, #627, #672, #771)
- **Config state no longer leaks between runs** when sub-dicts are mutated;
  `set_config` partial updates preserve sibling defaults. (#788)
- **`max_recur_limit` config actually applies** — previously read but not
  forwarded to the propagator. (#764)
- **Missing-API-key error** names the exact env var to set. (#680)
- **Quieter startup** — suppressed the noisy upstream
  `LangChainPendingDeprecationWarning` from langgraph-checkpoint; will be
  removed once that package ships its fix.

### Security

- **Ticker path-traversal validation** at every filesystem-path site (cache,
  checkpoint database, results) so a malicious ticker cannot escape its
  intended directory. (#618)

## [0.2.4] — 2026-04-25

### Added

- **Structured-output decision agents.** Research Manager, Trader, and Portfolio
  Manager now use `llm.with_structured_output(Schema)` on their primary call
  and return typed Pydantic instances. Each provider's native structured-output
  mode is used (`json_schema` for OpenAI / xAI, `response_schema` for Gemini,
  tool-use for Anthropic, function-calling for OpenAI-compatible providers).
  Render helpers preserve the existing markdown shape so memory log, CLI
  display, and saved reports keep working unchanged. (#434)
- **LangGraph checkpoint resume** — opt-in via `--checkpoint`. State is saved
  after each node so crashed or interrupted runs resume from the last
  successful step. Per-ticker SQLite databases under
  `~/.tradingagents/cache/checkpoints/`. `--clear-checkpoints` resets them. (#594)
- **Persistent decision log** replacing the per-agent BM25 memory. Decisions
  are stored automatically at the end of `propagate()`; the next same-ticker
  run resolves prior pending entries with realised return, alpha vs SPY, and
  a one-paragraph reflection. Override path with `TRADINGAGENTS_MEMORY_LOG_PATH`.
  Optional `memory_log_max_entries` config caps resolved entries; pending
  entries are never pruned. (#578, #563, #564, #579)
- **DeepSeek, Qwen (Alibaba DashScope), GLM (Zhipu), and Azure OpenAI**
  providers, plus dynamic OpenRouter model selection.
- **Docker support** — multi-stage build with separate dev and runtime images.
- **`scripts/smoke_structured_output.py`** — diagnostic that exercises the
  three structured-output agents against any provider so contributors can
  verify their setup with one command.
- **5-tier rating scale** (Buy / Overweight / Hold / Underweight / Sell) used
  consistently by Research Manager, Portfolio Manager, signal processor, and
  the memory log; Trader keeps 3-tier (Buy / Hold / Sell) since transaction
  direction is naturally ternary.
- **Pytest fixtures** — lazy LLM client imports plus placeholder API keys so
  the test suite runs cleanly without credentials. (#588)

### Changed

- **`backend_url` default is now `None`** rather than the OpenAI URL. Each
  provider client falls back to its native default. The previous default
  leaked the OpenAI URL into non-OpenAI clients (e.g. Gemini), producing
  malformed request URLs for Python users who switched providers without
  overriding `backend_url`. The CLI flow is unaffected.
- All file I/O passes explicit `encoding="utf-8"` so Windows users no longer
  hit `UnicodeEncodeError` with the cp1252 default. (#543, #550, #576)
- Cache and log directories moved to `~/.tradingagents/` to resolve Docker
  permission issues. (#519)
- `SignalProcessor` reads the rating from the Portfolio Manager's rendered
  markdown via a deterministic heuristic — no extra LLM call.
- OpenAI structured-output calls default to `method="function_calling"` to
  avoid noisy `PydanticSerializationUnexpectedValue` warnings emitted by
  langchain-openai's Responses-API parse path. Same typed result, no warnings.

### Fixed

- Empty memory no longer triggers fabricated past-lessons in agent prompts;
  the memory-log redesign makes this structurally impossible since only the
  Portfolio Manager consults memory and only when entries exist. (#572)
- Tool-call logging processes every chunk message, not just the last one, and
  memory score normalization handles empty score arrays. (#534, #531)

### Removed

- `FinancialSituationMemory` (the per-agent BM25 system) and the dead
  `reflect_and_remember()` plumbing; subsumed by the persistent decision log.
- Hardcoded Google endpoint that caused 404 when `langchain-google-genai`
  changed its API path. (#493, #496)

### Contributors

Thanks to everyone who shaped this release through code, design, and reports:

- [@claytonbrown](https://github.com/claytonbrown) — checkpoint resume (#594), test fixtures (#588), design feedback on cost tracking (#582) and structured validation (#583)
- [@Bcardo](https://github.com/Bcardo) — memory-log redesign (#579), empty-memory hallucination report (#572), encoding fix proposal (#570)
- [@voidborne-d](https://github.com/voidborne-d) — memory persistence design (#564), portfolio manager state fix (#503)
- [@mannubaveja007](https://github.com/mannubaveja007) — structured-output feature request (#434)
- [@kelder66](https://github.com/kelder66) — RAM-only memory issue (#563)
- [@Gujiassh](https://github.com/Gujiassh) — tool-call logging fix (#534), test stub PR (#533)
- [@iuyup](https://github.com/iuyup) — memory score normalization fix (#531)
- [@kaihg](https://github.com/kaihg) — Google base_url fix (#496)
- [@32ryh98yfe](https://github.com/32ryh98yfe) — Gemini 404 report (#493)
- [@uppb](https://github.com/uppb) — OpenRouter dynamic model selection (#482)
- [@guoz14](https://github.com/guoz14) — OpenRouter limited-model report (#337)
- [@samchenku](https://github.com/samchenku) — indicator name normalization (#490)
- [@JasonOA888](https://github.com/JasonOA888) — y_finance pandas import fix (#488)
- [@tiffanychum](https://github.com/tiffanychum) — stale import cleanup (#499)
- [@zaizou](https://github.com/zaizou) — Docker permission issue (#519)
- [@Stosman123](https://github.com/Stosman123), [@mauropuga](https://github.com/mauropuga), [@hotwind2015](https://github.com/hotwind2015) — Windows encoding bug reports (#543, #550, #576)
- [@nnishad](https://github.com/nnishad), [@atharvajoshi01](https://github.com/atharvajoshi01) — encoding fix proposals (#568, #549)

## [0.2.3] — 2026-03-29

### Added

- **Multi-language output** for analyst reports and final decisions, with a
  CLI selector. Internal agent debate stays in English for reasoning quality. (#472)
- **GPT-5.4 family models** in the default catalog, with deep/quick model split.
- **Unified model catalog** as a single source of truth for CLI options and
  provider validation.

### Changed

- `base_url` is forwarded to Google and Anthropic clients so corporate proxies
  work consistently across providers. (#427)
- Standardised the Google `api_key` parameter to the unified `api_key` form.

### Fixed

- Backtesting fetchers no longer leak look-ahead data when `curr_date` is in
  the middle of a fetched window. (#475)
- Invalid indicator names from the LLM are caught at the tool boundary instead
  of crashing the run. (#429)
- yfinance news fetchers respect the same exponential-backoff retry as price
  fetchers. (#445)

### Contributors

- [@ahmedk20](https://github.com/ahmedk20) — multi-language output (#472)
- [@CadeYu](https://github.com/CadeYu) — model catalog typing (#464)
- [@javierdejesusda](https://github.com/javierdejesusda) — unified Google API key parameter (#453)
- [@voidborne-d](https://github.com/voidborne-d) — yfinance news retry (#445)
- [@kostakost2](https://github.com/kostakost2) — look-ahead bias report (#475)
- [@lu-zhengda](https://github.com/lu-zhengda) — proxy/base_url support request (#427)
- [@VamsiKrishna2021](https://github.com/VamsiKrishna2021) — invalid indicator crash report (#429)

## [0.2.2] — 2026-03-22

### Added

- **Five-tier rating scale** (Buy / Overweight / Hold / Underweight / Sell)
  introduced for the Portfolio Manager.
- **Anthropic effort level** support for Claude models.
- **OpenAI Responses API** path for native OpenAI models.

### Changed

- `risk_manager` renamed to `portfolio_manager` to match the role description
  shown in the CLI display.
- Exchange-qualified tickers (e.g. `7203.T`, `BRK.B`) preserved across all
  agent prompts and tool calls.
- Process-level UTF-8 default attempted for cross-platform consistency
  (note: this approach did not actually take effect; replaced in v0.2.4 with
  explicit per-call `encoding="utf-8"` arguments).

### Fixed

- yfinance rate-limit errors are retried with exponential backoff. (#426)
- HTTP client SSL customisation is supported for environments that need
  custom certificate bundles. (#379)
- Report-section writes handle list-of-string content gracefully.

### Contributors

- [@CadeYu](https://github.com/CadeYu) — exchange-qualified ticker preservation (#413)
- [@yang1002378395-cmyk](https://github.com/yang1002378395-cmyk) — HTTP client SSL customisation (#379)

## [0.2.1] — 2026-03-15

### Security

- Patched `langchain-core` vulnerability (LangGrinch). (#335)
- Removed `chainlit` dependency affected by CVE-2026-22218.

### Added

- `pyproject.toml` build-system configuration; the project now installs via
  modern packaging tooling.

### Removed

- `setup.py` — dependencies consolidated to `pyproject.toml`.

### Fixed

- Risk manager reads the correct fundamental report source. (#341)
- All `open()` calls receive an explicit UTF-8 encoding (initial pass).
- `get_indicators` tool handles comma-separated indicator names from the LLM. (#368)
- `Propagation` initialises every debate-state field so risk debaters never
  see missing keys.
- Stock data parsing tolerates malformed CSVs and NaN values.
- Conditional debate logic respects the configured round count. (#361)

### Contributors

- [@RinZ27](https://github.com/RinZ27) — `langchain-core` security patch (#335)
- [@Ljx-007](https://github.com/Ljx-007) — risk manager fundamental-report fix (#341)
- [@makk9](https://github.com/makk9) — debate-rounds config issue (#361)

## [0.2.0] — 2026-02-04

This is the largest release since the initial public version. The framework
moved from single-provider to a multi-provider architecture and grew several
production-ready surfaces.

### Added

- **Multi-provider LLM support** (OpenAI, Google, Anthropic, xAI, OpenRouter,
  Ollama) via a factory pattern, with provider-specific thinking configurations.
- **Alpha Vantage** integration as a configurable primary data provider, with
  yfinance as a community-stability fallback.
- **Footer statistics** in the CLI: real-time tracking of LLM calls, tool
  calls, and token usage via LangChain callbacks.
- **Post-analysis report saving** — the framework writes per-section markdown
  files (analyst reports, debate transcripts, final decision) when a run
  completes.
- **Announcements panel** — fetches updates from `api.tauric.ai/v1/announcements`
  for the CLI welcome screen.
- **Tool fallbacks** so a single vendor outage does not stop the pipeline.

### Changed

- Risky / Safe risk debaters renamed to **Aggressive / Conservative** for
  consistency with the displayed agent labels.
- Default data vendor switched to balance reliability and quota across
  community deployments.
- Ollama and OpenRouter model lists updated; default endpoints clarified.

### Fixed

- Analyst status tracking and message deduplication in the live display.
- Infinite-loop guard in the agent loop; reflection and logging hardened.
- Various data-vendor implementation bugs and tool-signature mismatches.

### Contributors

This release is the first with substantial outside contributions; many community
PRs from late 2025 also landed here.

- [@luohy15](https://github.com/luohy15) — Alpha Vantage data-vendor integration (#235)
- [@EdwardoSunny](https://github.com/EdwardoSunny) — yfinance fetching optimisations (#245)
- [@Mirza-Samad-Ahmed-Baig](https://github.com/Mirza-Samad-Ahmed-Baig) — infinite-loop guard, reflection, and logging fixes (#89)
- [@ZeroAct](https://github.com/ZeroAct) — saved results path support (#29)
- [@Zhongyi-Lu](https://github.com/Zhongyi-Lu) — `.env` gitignore (#49)
- [@csoboy](https://github.com/csoboy) — local Ollama setup (#53)
- [@chauhang](https://github.com/chauhang) — initial Docker support attempt (#47, later reverted; the merged Docker support shipped in v0.2.4)

## [0.1.1] — 2025-06-07

### Removed

- Static site assets that had been bundled with v0.1.0; the public site now
  lives separately.

## [0.1.0] — 2025-06-05

### Added

- **Initial public release** of the TradingAgents multi-agent trading
  framework: market / sentiment / news / fundamentals analysts; bull and bear
  researchers; trader; aggressive, conservative, and neutral risk debaters;
  portfolio manager. LangGraph orchestration, yfinance data, per-agent
  BM25 memory, single-provider OpenAI integration, interactive CLI.

[0.2.4]: https://github.com/TauricResearch/TradingAgents/compare/v0.2.3...v0.2.4
[0.2.3]: https://github.com/TauricResearch/TradingAgents/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/TauricResearch/TradingAgents/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/TauricResearch/TradingAgents/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/TauricResearch/TradingAgents/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/TauricResearch/TradingAgents/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/TauricResearch/TradingAgents/releases/tag/v0.1.0
