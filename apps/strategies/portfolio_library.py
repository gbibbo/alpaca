#!/usr/bin/env python3
"""
apps/strategies/portfolio_library.py
Portfolio (cross-sectional) strategies: joint decisions over a universe, returning target weights.

    xsmom_12_1_long_only   PREREGISTERED 12-1 cross-sectional momentum, top decile, equal weight,
                           monthly rebalance, long only, no brackets, gross exposure 100%.
    xsmom_12_1_abs_filter   xsmom top decile with a positive absolute-momentum filter (else cash):
                           cross-sectional + time-series momentum combined.
    equal_weight_universe  1/N over the same eligible universe on the same dates: the PRIMARY
                           benchmark for xsmom (isolates the value of the ranking from
                           size-weighting and concentration effects).
"""

import math
from datetime import datetime
from decimal import Decimal
from typing import Dict, List

from lib.models import Bar, TimeFrame
from lib.portfolio_strategy import PortfolioStrategy, PortfolioTarget, register_portfolio


@register_portfolio
class CrossSectionalMomentum12_1(PortfolioStrategy):
    """12-1 momentum (Jegadeesh & Titman 1993; Fama-French/Kenneth French 'Mom' construction):

        M_i = P_{i,t-21} / P_{i,t-252} - 1        (months 2..12; the most recent month is
                                                    skipped to avoid short-term reversal)

    Each month rank all eligible symbols by M_i, hold the top decile equal-weighted, cash for
    the rest. Long only (momentum crashes come mostly from the short leg rebounding; Daniel &
    Moskowitz 2016). No absolute-momentum filter yet: that would be a different (combined)
    strategy and belongs in a later ablation.

    Preregistered: skip=21, lookback=252, decile=top 10%, equal weight, monthly. Do not tune.
    """
    name = "xsmom_12_1_long_only"
    timeframe = TimeFrame.DAY
    skip_sessions = 21               # preregistered
    lookback_sessions = 252          # preregistered
    top_fraction = 0.10              # preregistered (top decile)
    lookback_bars = 253
    max_history = 300
    rebalance = "monthly"
    description = "12-1 cross-sectional momentum, top decile, equal weight, monthly, long only"

    def score(self, bars: List[Bar]) -> float:
        closes = [float(b.close) for b in bars]
        return closes[-(self.skip_sessions + 1)] / closes[-(self.lookback_sessions + 1)] - 1.0

    def target(self, as_of: datetime, histories: Dict[str, List[Bar]], universe: List[str]) -> PortfolioTarget:
        scores = {s: self.score(histories[s]) for s in universe
                  if len(histories.get(s, [])) >= self.lookback_bars}
        if not scores:
            return PortfolioTarget(timestamp=as_of, strategy=self.name, weights={},
                                   metadata={"scores": {}, "n_eligible": 0})
        # Deterministic ranking: score desc, then symbol asc (ties never depend on input order).
        ranked = sorted(scores, key=lambda s: (-scores[s], s))
        k = max(1, math.ceil(len(ranked) * self.top_fraction))
        winners = ranked[:k]
        w = Decimal(1) / Decimal(k)
        return PortfolioTarget(
            timestamp=as_of, strategy=self.name,
            weights={s: w for s in winners},
            metadata={"scores": scores, "n_eligible": len(ranked), "k": k, "winners": winners})


@register_portfolio
class EqualWeightUniverse(PortfolioStrategy):
    """1/N across every eligible symbol, rebalanced monthly. Shares xsmom's eligibility window
    (same lookback) so the two accounts are live over exactly the same dates and stocks."""
    name = "equal_weight_universe"
    timeframe = TimeFrame.DAY
    lookback_bars = 253
    max_history = 300
    rebalance = "monthly"
    description = "equal-weight benchmark over the same eligible universe and dates"

    def target(self, as_of: datetime, histories: Dict[str, List[Bar]], universe: List[str]) -> PortfolioTarget:
        eligible = sorted(s for s in universe if len(histories.get(s, [])) >= self.lookback_bars)
        if not eligible:
            return PortfolioTarget(timestamp=as_of, strategy=self.name, weights={})
        w = Decimal(1) / Decimal(len(eligible))
        return PortfolioTarget(timestamp=as_of, strategy=self.name, weights={s: w for s in eligible},
                               metadata={"n_eligible": len(eligible)})


@register_portfolio
class CrossSectionalMomentum12_1AbsFilter(CrossSectionalMomentum12_1):
    """xsmom 12-1 top decile PLUS an absolute-momentum filter (cross-sectional + time-series):
    a top-decile winner is held only if its own 12-1 momentum is also positive; otherwise that
    slice goes to cash. Each held name keeps weight 1/k of the ORIGINAL decile size, so gross
    exposure = (names passing) / k and the remainder is cash. In a broad drawdown, when even the
    relative winners have negative absolute momentum, the book de-risks toward cash instead of
    buying "the ones that fell least". This is the natural ablation of xsmom_12_1_long_only;
    momentum crashes are tied to buying beaten-down names, so the filter targets exactly that.
    """
    name = "xsmom_12_1_abs_filter"
    description = "xsmom 12-1 top decile with a positive absolute-momentum filter (else cash)"

    def target(self, as_of, histories, universe):
        base = super().target(as_of, histories, universe)
        scores = base.metadata.get("scores") or {}
        k = base.metadata.get("k") or 0
        held = [s for s in base.weights if scores.get(s, 0.0) > 0.0]
        weights = {}
        if k > 0 and held:
            w = Decimal(1) / Decimal(k)          # keep decile sizing; dropped slices become cash
            weights = {s: w for s in held}
        return PortfolioTarget(
            timestamp=as_of, strategy=self.name, weights=weights,
            metadata={**base.metadata, "abs_filtered": True, "held": held,
                      "n_passing_abs": len(held), "gross_exposure": float(len(held) / k) if k else 0.0})
