# Modified by TradingAgents-Studio contributors (2026) — see CHANGELOG.md
# Original: github.com/TauricResearch/TradingAgents (Apache License 2.0)

from enum import Enum
from typing import List, Optional, Dict
from pydantic import BaseModel


class AnalystType(str, Enum):
    MARKET = "market"
    # Wire value stays "social" for saved-config and string-keyed-caller
    # back-compat; the user-facing label is "Sentiment Analyst".
    SOCIAL = "social"
    NEWS = "news"
    FUNDAMENTALS = "fundamentals"
    CN_SOCIAL = "cn_social"      # A-share CN social sentiment (东方财富股吧 + MediaCrawler)
    EVENT = "event"              # Event-driven analyst (LLM causal chain from news)


class AssetType(str, Enum):
    STOCK = "stock"
    CRYPTO = "crypto"
