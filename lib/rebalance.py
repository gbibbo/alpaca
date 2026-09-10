#!/usr/bin/env python3
"""
lib/rebalance.py
RebalancePlanner: current portfolio vs target weights -> whole-share trades.

The plan is an ATOMIC decision: every target value is computed once from the same equity and
prices, then all deltas are derived, then trades are ordered sells-first (they free cash) and
buys are scaled DOWN proportionally in a single pass if cash would not cover them. Nothing
depends on the order symbols are listed in, and one-way turnover is reported.
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import Dict, List, Mapping, Optional


@dataclass(frozen=True)
class Trade:
    symbol: str
    side: str          # "BUY" | "SELL"
    quantity: int
    target_weight: Decimal
    pretrade_weight: Decimal


@dataclass
class RebalancePlan:
    sells: List[Trade]
    buys: List[Trade]
    turnover_one_way: Decimal         # 0.5 * sum |w_target - w_pretrade|
    scaled_buys: bool                  # True if buys were reduced to fit available cash

    @property
    def trades(self) -> List[Trade]:
        return self.sells + self.buys


def one_way_turnover(target: Mapping[str, Decimal], pretrade: Mapping[str, Decimal]) -> Decimal:
    keys = set(target) | set(pretrade)
    return sum((abs(Decimal(str(target.get(k, 0))) - Decimal(str(pretrade.get(k, 0)))) for k in keys),
               Decimal(0)) / 2


def plan_rebalance(equity: Decimal, cash: Decimal, prices: Mapping[str, Decimal],
                   positions: Mapping[str, Decimal], target_weights: Mapping[str, Decimal],
                   buy_cost_factor: Decimal = Decimal(1)) -> RebalancePlan:
    """Build the trade list for one rebalance.

    equity/cash/prices/positions are the pre-trade snapshot at decision time. `buy_cost_factor`
    (e.g. 1 + slippage + commission) inflates the cash needed per bought share so the plan
    fits. Symbols without a price are left untouched.
    """
    equity, cash = Decimal(str(equity)), Decimal(str(cash))
    symbols = sorted(set(positions) | set(target_weights))
    pretrade_w: Dict[str, Decimal] = {}
    for s in symbols:
        px = prices.get(s)
        qty = Decimal(str(positions.get(s, 0)))
        pretrade_w[s] = (qty * Decimal(str(px)) / equity) if (px and equity > 0) else Decimal(0)

    sells: List[Trade] = []
    buys: List[Trade] = []
    for s in symbols:
        px = prices.get(s)
        if not px:
            continue
        px = Decimal(str(px))
        tw = Decimal(str(target_weights.get(s, 0)))
        held = Decimal(str(positions.get(s, 0)))
        target_qty = int((equity * tw / px).to_integral_value(rounding=ROUND_DOWN))
        delta = target_qty - int(held)
        if delta < 0:
            sells.append(Trade(s, "SELL", -delta, tw, pretrade_w[s]))
        elif delta > 0:
            buys.append(Trade(s, "BUY", delta, tw, pretrade_w[s]))

    # Cash after sells (at decision prices), then proportional scaling of buys if short.
    freed = sum((Decimal(t.quantity) * Decimal(str(prices[t.symbol])) for t in sells), Decimal(0))
    available = cash + freed
    need = sum((Decimal(t.quantity) * Decimal(str(prices[t.symbol])) * buy_cost_factor for t in buys), Decimal(0))
    scaled = False
    if buys and need > available and need > 0:
        ratio = available / need
        scaled = True
        buys = [Trade(t.symbol, "BUY", int((Decimal(t.quantity) * ratio).to_integral_value(rounding=ROUND_DOWN)),
                      t.target_weight, t.pretrade_weight) for t in buys]
        buys = [t for t in buys if t.quantity > 0]

    return RebalancePlan(sells=sells, buys=buys,
                         turnover_one_way=one_way_turnover(target_weights, pretrade_w), scaled_buys=scaled)
