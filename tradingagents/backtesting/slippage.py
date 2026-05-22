"""Slippage and fee models for the backtest engine.

A ``CostModel`` decides what price a buy/sell actually fills at, and
what fees come out of the proceeds. The default model approximates
A-share retail costs:

- Commission: 0.025% per side, ¥5 minimum (most discount brokers)
- Stamp duty: 0.05% on sell only (transferred to seller; recent 2023
  policy)
- Slippage: 0.05% adverse on both sides (1bp on each "side" doubles into
  a 2bp round-trip impact)

US tickers default to commission-free + 5bp slippage (close to most
retail brokerages now). HK and others fall back to the same.

Override per-run by passing a custom ``CostModel`` to ``BacktestConfig``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CostModel:
    """Generic cost model. Returns adjusted fill price + total fee.

    ``slippage_bps`` is applied symmetrically (buys fill slightly above
    the bar price; sells fill slightly below). ``commission_rate`` is
    a fraction of notional (e.g. 0.00025 = 0.025%). ``stamp_duty_rate``
    only applies to sells. ``min_commission`` is the per-trade floor.
    """

    slippage_bps: float = 5.0
    commission_rate: float = 0.00025
    min_commission: float = 5.0
    stamp_duty_rate: float = 0.0  # default 0 — caller picks A-share preset

    def adjust_buy_price(self, raw_price: float) -> float:
        return raw_price * (1 + self.slippage_bps / 10_000.0)

    def adjust_sell_price(self, raw_price: float) -> float:
        return raw_price * (1 - self.slippage_bps / 10_000.0)

    def buy_fee(self, shares: float, price: float) -> float:
        notional = shares * price
        return max(self.min_commission, notional * self.commission_rate)

    def sell_fee(self, shares: float, price: float) -> float:
        notional = shares * price
        commission = max(self.min_commission, notional * self.commission_rate)
        stamp = notional * self.stamp_duty_rate
        return commission + stamp


# Convenient presets.

A_SHARE_COST = CostModel(
    slippage_bps=5.0,
    commission_rate=0.00025,
    min_commission=5.0,
    stamp_duty_rate=0.0005,
)

US_COST = CostModel(
    slippage_bps=5.0,
    commission_rate=0.0,
    min_commission=0.0,
    stamp_duty_rate=0.0,
)


def pick_cost_model(ticker: str) -> CostModel:
    """Heuristic: pick a reasonable cost model from the ticker shape.

    Anything matching A-share patterns (6-digit / .SH / .SS / .SZ / sh.
    / sz. prefix) uses A_SHARE_COST. Everything else uses US_COST. The
    engine calls this per fill, so a backtest spanning A-share + US
    tickers still gets reasonable costs per leg.
    """
    t = (ticker or "").upper().strip()
    digits = "".join(ch for ch in t if ch.isdigit())
    if len(digits) >= 6 and ("SH" in t or "SZ" in t or "SS" in t or t == digits[:6]):
        return A_SHARE_COST
    return US_COST
