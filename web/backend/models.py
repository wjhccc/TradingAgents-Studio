from pydantic import BaseModel
from typing import Optional


class AnalyzeRequest(BaseModel):
    ticker: str
    trade_date: str
    asset_type: str = "stock"
    analysts: list[str] = ["market", "social", "news", "fundamentals"]
    max_debate_rounds: int = 1
    max_risk_discuss_rounds: int = 1
    llm_provider: Optional[str] = None
    deep_think_llm: Optional[str] = None
    quick_think_llm: Optional[str] = None
    output_language: Optional[str] = None
    checkpoint_enabled: bool = False


class SettingsUpdate(BaseModel):
    llm_provider: Optional[str] = None
    deep_think_llm: Optional[str] = None
    quick_think_llm: Optional[str] = None
    max_debate_rounds: Optional[int] = None
    max_risk_discuss_rounds: Optional[int] = None
    output_language: Optional[str] = None
    checkpoint_enabled: Optional[bool] = None
    benchmark_ticker: Optional[str] = None


class APIKeysUpdate(BaseModel):
    """Partial map of provider name → API key value.

    Empty string clears the key (both from os.environ and the .env file).
    Provider names are case-insensitive and must match an entry in
    ``tradingagents.llm_clients.api_key_env.PROVIDER_API_KEY_ENV``.
    """
    keys: dict[str, str]


class NLQueryRequest(BaseModel):
    """Natural-language analyze query, e.g. "研究茅台短期"."""
    text: str
    # Optional: ask the backend to fall back to LLM if the rule layer
    # can't pin down a ticker. Off by default to keep the endpoint cheap
    # and synchronous; the frontend can flip it on with a checkbox.
    use_llm_fallback: bool = False


class HoldingCreate(BaseModel):
    ticker: str
    asset_type: str = "stock"
    shares: float
    cost_price: float
    open_date: Optional[str] = None  # YYYY-MM-DD
    notes: Optional[str] = None


class HoldingUpdate(BaseModel):
    shares: Optional[float] = None
    cost_price: Optional[float] = None
    open_date: Optional[str] = None
    notes: Optional[str] = None


class HoldingsImport(BaseModel):
    """Bulk CSV import. Expected columns: ticker, shares, cost_price, [open_date, notes]."""
    csv_text: str
    asset_type: str = "stock"


class ScheduleCreate(BaseModel):
    """Create a recurring analysis schedule.

    ``schedule_type`` picks the recurrence pattern:
      - ``interval`` — every ``interval_minutes`` minutes from creation
      - ``daily`` — every day at ``time_of_day`` (HH:MM, server-local)
      - ``weekly`` — every week on ``day_of_week`` (0=Mon..6=Sun) at ``time_of_day``

    Analysts and LLM config are saved at create-time and reused each fire,
    so changing global Settings later won't silently alter scheduled runs.
    """
    ticker: str
    asset_type: str = "stock"
    name: Optional[str] = None
    schedule_type: str  # 'interval' | 'daily' | 'weekly'
    interval_minutes: Optional[int] = None
    time_of_day: Optional[str] = None  # "HH:MM"
    day_of_week: Optional[int] = None  # 0=Mon..6=Sun
    analysts: list[str] = ["market", "news", "fundamentals"]
    # LLM config overrides; same shape as AnalyzeRequest's optional fields.
    max_debate_rounds: int = 1
    max_risk_discuss_rounds: int = 1
    llm_provider: Optional[str] = None
    deep_think_llm: Optional[str] = None
    quick_think_llm: Optional[str] = None
    output_language: Optional[str] = None


class ScheduleUpdate(BaseModel):
    name: Optional[str] = None
    schedule_type: Optional[str] = None
    interval_minutes: Optional[int] = None
    time_of_day: Optional[str] = None
    day_of_week: Optional[int] = None
    analysts: Optional[list[str]] = None
    status: Optional[str] = None  # 'active' | 'paused' | 'disabled'


class ScheduleFromHoldings(BaseModel):
    """Bulk-create schedules for every current holding."""
    schedule_type: str = "daily"
    interval_minutes: Optional[int] = None
    time_of_day: Optional[str] = "09:30"
    day_of_week: Optional[int] = None
    analysts: list[str] = ["market", "news", "fundamentals"]
    max_debate_rounds: int = 1
    max_risk_discuss_rounds: int = 1


class PaperOrderRequest(BaseModel):
    """Place a paper-trading order.

    If ``price`` is omitted the backend fetches the latest close via the
    same vendor router that holdings use. ``source`` defaults to 'manual';
    set to 'decision' (and pass ``source_analysis_id``) when the order
    derives from an analysis decision card.
    """
    ticker: str
    action: str  # 'buy' | 'sell'
    shares: float
    price: Optional[float] = None
    asset_type: str = "stock"
    source: str = "manual"
    source_analysis_id: Optional[str] = None
    notes: Optional[str] = None


class PaperOrderFromDecision(BaseModel):
    """Open a position based on a completed analysis decision card.

    Reads the analysis's trader_proposal / final_decision markdown, takes
    the recommended Action (Buy/Sell) and Entry Price, and sizes the
    position by either a fixed share count or a fraction of available cash.
    """
    analysis_id: str
    # Sizing options — exactly one must be set.
    shares: Optional[float] = None
    cash_fraction: Optional[float] = None  # 0..1 of available cash
    # Optional override; otherwise use entry_price from the decision card
    # or fall back to the latest close.
    price: Optional[float] = None


class PaperAccountReset(BaseModel):
    confirm: bool = False


class BacktestRunRequest(BaseModel):
    """Create + execute a backtest in one request.

    The backtest runs synchronously inside the request handler (most
    Agent-decision backtests finish in <1 second since they don't make
    any LLM calls — they just replay history). For slower variants
    (rule strategies on long windows) we can later add a background
    task; the data model already supports `pending` / `running`
    statuses.
    """
    name: Optional[str] = None
    signal_source: str = "memory_log"   # 'memory_log' (Phase 1)
    # signal_source-specific kwargs. memory_log accepts none right now.
    source_config: dict = {}
    tickers: Optional[list[str]] = None  # None = derive from signals
    start_date: str                      # YYYY-MM-DD
    end_date: str                        # YYYY-MM-DD
    initial_cash: float = 1_000_000.0
    benchmark: Optional[str] = None
    sizing_mode: str = "equal_weight"
    fixed_cash_per_signal: Optional[float] = None
    confidence_floor: Optional[float] = None
    strict_sell_only: bool = True
