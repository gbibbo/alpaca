"""Decimal ledger shared by historical simulation and fill accounting.

A closed trade is one closing fill (a partial exit is a closed lot). Fees are
allocated between closing/opening quantities; equity never adds realized PnL twice.
"""
from decimal import Decimal


def D(value):
    return Decimal(str(value))


class Portfolio:
    def __init__(self, initial_cash):
        self.initial_cash = D(initial_cash)
        if not self.initial_cash.is_finite() or self.initial_cash <= 0:
            raise ValueError("initial_cash must be finite and positive")
        self.cash = self.initial_cash
        self.positions, self.avg_cost, self.prices = {}, {}, {}
        self.entry_fees, self.realized_pnl = {}, {}
        self.total_realized = D(0)
        self.fees = D(0)
        self.closed_trades, self.fills = [], []
        self.seen = set()

    @property
    def equity(self):
        return self.cash + sum((qty * self.prices[sym] for sym, qty in self.positions.items()), D(0))

    def mark(self, symbol, price):
        price = D(price)
        if not price.is_finite() or price <= 0:
            raise ValueError("Invalid mark price")
        self.prices[symbol] = price

    def fill(self, fill_id, symbol, side, quantity, price, commission=0, timestamp=None):
        if str(fill_id) in self.seen:
            return False
        qty, price, fee = D(quantity), D(price), D(commission)
        if not all(x.is_finite() for x in (qty, price, fee)) or min(qty, price) <= 0 or fee < 0:
            raise ValueError("Invalid fill")
        side = str(getattr(side, "value", side)).upper()
        if side not in ("BUY", "SELL"):
            raise ValueError("Invalid side")
        signed = qty if side == "BUY" else -qty
        old = self.positions.get(symbol, D(0))
        cost = self.avg_cost.get(symbol, D(0))
        old_fee = self.entry_fees.get(symbol, D(0))
        realized = D(0)
        if old and old * signed < 0:
            closing = min(abs(old), qty)
            allocated = old_fee * closing / abs(old)
            realized = closing * (price - cost) * (1 if old > 0 else -1) - allocated - fee * closing / qty
            self.closed_trades.append({"symbol": symbol, "quantity": float(closing),
                                       "pnl": float(realized), "timestamp": timestamp})
            self.entry_fees[symbol] = old_fee - allocated
            if qty > abs(old):
                self.avg_cost[symbol] = price
                self.entry_fees[symbol] = fee * (qty - abs(old)) / qty
        else:
            self.avg_cost[symbol] = (abs(old) * cost + qty * price) / (abs(old) + qty)
            self.entry_fees[symbol] = old_fee + fee
        self.positions[symbol] = old + signed
        self.cash -= signed * price + fee
        self.fees += fee
        self.total_realized += realized
        self.realized_pnl[symbol] = self.realized_pnl.get(symbol, D(0)) + realized
        self.mark(symbol, price)
        self.seen.add(str(fill_id))
        self.fills.append({"fill_id": str(fill_id), "symbol": symbol, "side": side,
                           "quantity": float(qty), "price": float(price), "commission": float(fee),
                           "timestamp": timestamp, "realized_pnl": float(realized)})
        return True

    def snapshot(self, timestamp=None):
        return {"timestamp": timestamp, "cash": float(self.cash), "equity": float(self.equity),
                "positions_value": float(self.equity - self.cash),
                "realized_pnl": float(self.total_realized),
                "unrealized_pnl": float(self.equity - self.initial_cash - self.total_realized),
                "total_pnl": float(self.equity - self.initial_cash),
                "positions": {s: float(q) for s, q in self.positions.items() if q}}

    def trade_stats(self):
        pnls = [trade["pnl"] for trade in self.closed_trades]
        wins = sum(p > 0 for p in pnls)
        gains, losses = sum(p for p in pnls if p > 0), -sum(p for p in pnls if p < 0)
        return {"total": len(pnls), "winning": wins, "win_rate": wins / len(pnls) * 100 if pnls else None,
                "expectancy": sum(pnls) / len(pnls) if pnls else None,
                "profit_factor": gains / losses if losses else None,
                "definition": "closing lots, net of allocated entry and exit fees"}
