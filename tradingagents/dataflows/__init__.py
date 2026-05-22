# Modified by TradingAgents-Studio contributors (2026) — see CHANGELOG.md
# Original: github.com/TauricResearch/TradingAgents (Apache License 2.0)

# Apply NO_PROXY bypass for CN data domains at first import so a globally
# configured HTTP proxy (Clash / V2Ray / VPN) doesn't intercept calls to
# eastmoney/sina/tushare and fail them with ``ProxyError``.
from ._proxy import ensure_no_proxy_for_cn_data as _ensure_no_proxy_for_cn_data

_ensure_no_proxy_for_cn_data()
