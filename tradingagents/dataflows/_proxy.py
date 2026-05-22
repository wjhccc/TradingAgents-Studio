"""Auto-bypass HTTP proxies for A-share / Chinese data domains.

Why this exists: in mainland CN development environments it's common to
run a global proxy (Clash, V2Ray, etc.) for overseas access. The proxy
intercepts ALL outbound HTTP via the ``HTTP_PROXY`` / ``HTTPS_PROXY``
env vars — including calls to eastmoney/sina/tushare which the proxy
then refuses or fails because it only routes overseas traffic.

Symptom: ``ProxyError('Unable to connect to proxy', RemoteDisconnected)``
when AKShare/Tushare fetch fails out of nowhere even though the same URL
works fine in the browser.

Fix: append CN data domains to ``NO_PROXY`` at startup so ``requests``
bypasses the proxy for them. Only applied when a proxy is actually set —
no behavior change for users without a proxy.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Hosts and parent domains used by Studio's data layer. Both bare host
# and ``.parent`` form so ``requests``' wildcard matching (via env_no_proxy)
# catches subdomains like push2his.eastmoney.com without an explicit entry.
_CN_DATA_DOMAINS = [
    # 东方财富 — AKShare 的主力数据源 (行情/资金流/北向/股吧/研报/龙虎榜)
    "eastmoney.com", "push2his.eastmoney.com", "datacenter-web.eastmoney.com",
    "push2.eastmoney.com", "guba.eastmoney.com", "data.eastmoney.com",
    # 新浪财经
    "sina.com.cn", "sina.com",
    "finance.sina.com.cn", "vip.stock.finance.sina.com.cn",
    "hq.sinajs.cn",
    # Tushare
    "tushare.pro", "api.tushare.pro",
    # 雪球(spot 备用源)
    "xueqiu.com", "stock.xueqiu.com",
    # 巨潮资讯
    "cninfo.com.cn", "static.cninfo.com.cn",
    # 同花顺
    "10jqka.com.cn",
    # 财联社
    "cls.cn",
    # AKShare 文档/资源 — 不直接拉数据,但本地 dev 也容易踩坑
    "akshare.akfamily.xyz", "akshare.xyz",
]


def ensure_no_proxy_for_cn_data() -> None:
    """Append CN data domains to NO_PROXY, unconditionally.

    Always sets NO_PROXY (even when no proxy is currently configured) so
    that a proxy started *after* the project boots also gets bypassed for
    CN data. Idempotent — safe to call multiple times. Sets both upper-
    and lower-case forms because different libraries read different
    variants.

    Caveat: only helps when the proxy is HTTP-based and respects env
    vars. If the proxy is in TUN / TAP / system-wide transparent mode,
    Python can't bypass it from inside the process — the user must add a
    direct rule in their proxy software.
    """
    proxy_vars = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy")
    proxy_seen = [k for k in proxy_vars if os.environ.get(k)]

    existing = os.environ.get("NO_PROXY", "") or os.environ.get("no_proxy", "")
    existing_parts = [p.strip() for p in existing.split(",") if p.strip()]
    existing_set = {p.lower() for p in existing_parts}
    additions = [d for d in _CN_DATA_DOMAINS if d.lower() not in existing_set]
    if additions:
        merged = ",".join(existing_parts + additions)
        os.environ["NO_PROXY"] = merged
        os.environ["no_proxy"] = merged

    if proxy_seen:
        logger.info(
            "HTTP proxy detected (%s); CN data domains added to NO_PROXY "
            "(%d new). If A-share data fetches still fail with ProxyError, "
            "your proxy is likely in TUN/transparent mode — add eastmoney.com / "
            "sina.com.cn / tushare.pro to its direct/bypass rules.",
            ",".join(proxy_seen), len(additions),
        )
    else:
        logger.info(
            "No HTTP proxy env vars detected. NO_PROXY pre-populated with "
            "%d CN data domains (for safety if a proxy is set later).",
            len(additions),
        )
