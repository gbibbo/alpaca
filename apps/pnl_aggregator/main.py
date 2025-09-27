#!/usr/bin/env python3
"""
apps/pnl_aggregator/main.py
Simple PnL aggregator that consumes order fills and tracks portfolio performance
"""

import asyncio
import logging
import os
import sys
import json
import csv
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Dict

# Add lib to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from lib.bus import connect_bus, get_bus
from lib.models import OrderFill, SignalSide
from lib.metrics_helpers import (
    ServiceMetrics, start_metrics_server, Gauge, Counter, TRADING_REGISTRY
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# PnL-specific metrics
PNL_REALIZED = Gauge(
    'trading_pnl_realized_usd',
    'Realized P&L in USD',
    ['symbol'],
    registry=TRADING_REGISTRY
)

PNL_UNREALIZED = Gauge(
    'trading_pnl_unrealized_usd',
    'Unrealized P&L in USD (mark-to-market)',
    ['symbol'],
    registry=TRADING_REGISTRY
)

PORTFOLIO_CASH = Gauge(
    'trading_portfolio_cash_usd',
    'Available cash in USD',
    registry=TRADING_REGISTRY
)

POSITION_VALUE = Gauge(
    'trading_position_value_usd',
    'Position value in USD',
    ['symbol'],
    registry=TRADING_REGISTRY
)

TRADES_COUNT = Counter(
    'trading_trades_total',
    'Total number of trades executed',
    ['symbol', 'side'],
    registry=TRADING_REGISTRY
)


class PnLAggregator:
    """Simple PnL aggregator for backtesting and live trading"""

    def __init__(self, initial_cash: Decimal = Decimal('100000')):
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.positions = {}  # symbol -> quantity
        self.avg_cost = {}   # symbol -> average cost per share
        self.realized_pnl = {}  # symbol -> realized PnL
        self.total_realized = Decimal('0')
        self.fills = []  # List of all fills for export

        # Track for CSV export
        self.equity_history = []

        # Metrics
        self.metrics = ServiceMetrics('pnl_aggregator')

        # Start metrics server
        try:
            metrics_port = int(os.getenv("PNL_METRICS_PORT", "8015"))
            start_metrics_server(metrics_port)
            logger.info(f"📊 PnL Aggregator metrics available at http://localhost:{metrics_port}/metrics")
        except OSError as e:
            if getattr(e, "errno", None) == 98:  # Address already in use
                try:
                    from lib.metrics_helpers import find_available_port
                    metrics_port = find_available_port(metrics_port + 1)
                    start_metrics_server(metrics_port)
                    logger.warning(f"Metrics port busy. Using fallback http://localhost:{metrics_port}/metrics")
                except Exception as fallback_error:
                    logger.warning(f"Failed to start metrics server on fallback port: {fallback_error}")
            else:
                logger.warning(f"Failed to start metrics server: {e}")
        except Exception as e:
            logger.warning(f"Failed to start metrics server: {e}")

        logger.info(f"PnL Aggregator initialized with ${initial_cash:,.2f} starting cash")

    def process_fill(self, fill: OrderFill):
        """Process an order fill and update portfolio"""
        symbol = fill.symbol
        quantity = fill.fill_quantity
        price = fill.fill_price
        side = fill.side
        timestamp = fill.timestamp

        # Convert side to signed quantity
        signed_qty = quantity if side == SignalSide.BUY else -quantity
        fill_value = quantity * price

        logger.info(f"Processing fill: {side.value} {quantity} {symbol} @ ${price:.2f}")

        # Initialize position if new
        if symbol not in self.positions:
            self.positions[symbol] = Decimal('0')
            self.avg_cost[symbol] = Decimal('0')
            self.realized_pnl[symbol] = Decimal('0')

        current_position = self.positions[symbol]

        if side == SignalSide.BUY:
            # Buying: update average cost and increase position
            if current_position >= 0:
                # Adding to long position or opening new long
                total_cost = (current_position * self.avg_cost[symbol]) + fill_value
                total_shares = current_position + quantity
                self.avg_cost[symbol] = total_cost / total_shares if total_shares > 0 else price
                self.positions[symbol] = total_shares
                self.cash -= fill_value
            else:
                # Covering short position
                if quantity <= abs(current_position):
                    # Partial or full cover
                    realized = quantity * (self.avg_cost[symbol] - price)
                    self.realized_pnl[symbol] += realized
                    self.total_realized += realized
                    self.positions[symbol] += quantity
                    self.cash -= fill_value
                else:
                    # Cover all short and go long
                    cover_qty = abs(current_position)
                    realized = cover_qty * (self.avg_cost[symbol] - price)
                    self.realized_pnl[symbol] += realized
                    self.total_realized += realized

                    # Go long with remaining
                    remaining_qty = quantity - cover_qty
                    self.positions[symbol] = remaining_qty
                    self.avg_cost[symbol] = price
                    self.cash -= fill_value

        else:  # SELL
            # Selling: realize PnL and decrease position
            if current_position > 0:
                # Selling long position
                if quantity <= current_position:
                    # Partial or full sale
                    realized = quantity * (price - self.avg_cost[symbol])
                    self.realized_pnl[symbol] += realized
                    self.total_realized += realized
                    self.positions[symbol] -= quantity
                    self.cash += fill_value
                else:
                    # Sell all long and go short
                    realized = current_position * (price - self.avg_cost[symbol])
                    self.realized_pnl[symbol] += realized
                    self.total_realized += realized

                    # Go short with remaining
                    remaining_qty = quantity - current_position
                    self.positions[symbol] = -remaining_qty
                    self.avg_cost[symbol] = price
                    self.cash += fill_value
            else:
                # Adding to short position or opening new short
                if current_position <= 0:
                    total_value = (abs(current_position) * self.avg_cost[symbol]) + fill_value
                    total_shares = abs(current_position) + quantity
                    self.avg_cost[symbol] = total_value / total_shares
                    self.positions[symbol] = -(abs(current_position) + quantity)
                    self.cash += fill_value

        # Record fill for export
        self.fills.append({
            'timestamp': timestamp.isoformat(),
            'symbol': symbol,
            'side': side.value,
            'quantity': float(quantity),
            'price': float(price),
            'value': float(fill_value),
            'realized_pnl': float(self.realized_pnl[symbol]),
            'position': float(self.positions[symbol]),
            'cash': float(self.cash)
        })

        # Update metrics
        TRADES_COUNT.labels(symbol=symbol, side=side.value).inc()
        PNL_REALIZED.labels(symbol=symbol).set(float(self.realized_pnl[symbol]))
        POSITION_VALUE.labels(symbol=symbol).set(float(self.positions[symbol] * price))
        PORTFOLIO_CASH.set(float(self.cash))

        # Calculate and record equity snapshot
        total_value = float(self.cash)
        for sym, pos in self.positions.items():
            if pos != 0:
                # Use last price for mark-to-market (simplified)
                last_price = price if sym == symbol else self.avg_cost.get(sym, Decimal('0'))
                total_value += float(pos * last_price)

        self.equity_history.append({
            'timestamp': timestamp.isoformat(),
            'cash': float(self.cash),
            'total_value': total_value,
            'realized_pnl': float(self.total_realized),
            'positions': dict(self.positions)
        })

        # Log summary
        logger.info(f"Portfolio update: Cash=${self.cash:,.2f}, Total PnL=${self.total_realized:,.2f}, Positions={len([p for p in self.positions.values() if p != 0])}")

    def export_results(self, output_dir: str = "data/pnl_results"):
        """Export results to CSV files"""
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Export fills
        fills_file = output_path / f"fills_{timestamp}.csv"
        with open(fills_file, 'w', newline='') as f:
            if self.fills:
                writer = csv.DictWriter(f, fieldnames=self.fills[0].keys())
                writer.writeheader()
                writer.writerows(self.fills)

        # Export equity curve
        equity_file = output_path / f"equity_{timestamp}.csv"
        with open(equity_file, 'w', newline='') as f:
            if self.equity_history:
                writer = csv.DictWriter(f, fieldnames=self.equity_history[0].keys())
                writer.writeheader()
                writer.writerows(self.equity_history)

        # Export final summary
        summary = {
            'initial_cash': float(self.initial_cash),
            'final_cash': float(self.cash),
            'total_realized_pnl': float(self.total_realized),
            'total_trades': len(self.fills),
            'final_positions': {str(k): float(v) for k, v in self.positions.items() if v != 0},
            'return_pct': float((self.cash + self.total_realized - self.initial_cash) / self.initial_cash * 100)
        }

        summary_file = output_path / f"summary_{timestamp}.json"
        with open(summary_file, 'w') as f:
            json.dump(summary, f, indent=2)

        logger.info(f"Results exported to {output_path}")
        logger.info(f"  Fills: {fills_file}")
        logger.info(f"  Equity: {equity_file}")
        logger.info(f"  Summary: {summary_file}")
        logger.info(f"  Total Return: {summary['return_pct']:.2f}%")

        return summary

    def get_stats(self) -> Dict:
        """Get current portfolio statistics"""
        total_value = float(self.cash)
        for symbol, position in self.positions.items():
            if position != 0:
                # Use average cost for simplicity (in real system, use current market price)
                total_value += float(position * self.avg_cost.get(symbol, Decimal('0')))

        return {
            "cash": float(self.cash),
            "total_value": total_value,
            "total_realized_pnl": float(self.total_realized),
            "total_trades": len(self.fills),
            "active_positions": len([p for p in self.positions.values() if p != 0]),
            "positions": {k: float(v) for k, v in self.positions.items() if v != 0},
            "return_pct": (total_value - float(self.initial_cash)) / float(self.initial_cash) * 100
        }


async def main():
    """Main PnL aggregator loop"""
    logger.info("Starting PnL Aggregator...")

    # Connect to message bus
    if not connect_bus():
        logger.error("Failed to connect to message bus")
        return

    bus = get_bus()
    aggregator = PnLAggregator()

    # Mark service start
    aggregator.metrics.mark_service_start()

    async def fill_handler(msg_data: dict) -> bool:
        """Handler for order fills"""
        try:
            if msg_data.get("type") != "order_fill":
                return True  # ACK non-fill messages

            fill_data = json.loads(msg_data["data"])
            fill = OrderFill.model_validate(fill_data)

            aggregator.process_fill(fill)
            return True  # ACK the message

        except Exception as e:
            logger.error(f"Error processing fill: {e}")
            return False  # Don't ACK on error

    try:
        # Check if we're using Streams
        if hasattr(bus.backend, 'consume_with_handler') and bus.get_stats().get('backend') == 'streams':
            logger.info("Using Redis Streams consumption for order fills")
            await bus.backend.consume_with_handler("fills", fill_handler)
        else:
            logger.info("Using Pub/Sub consumption for order fills")
            async for fill in bus.subscribe_order_fills():
                try:
                    aggregator.process_fill(fill)
                except Exception as e:
                    logger.error(f"Error processing fill: {e}")

    except KeyboardInterrupt:
        logger.info("Received shutdown signal")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
    finally:
        # Export results on shutdown
        logger.info("Exporting final results...")
        summary = aggregator.export_results()

        # Mark service stop
        aggregator.metrics.mark_service_stop()

        logger.info("PnL Aggregator stopped")
        logger.info(f"Final Performance: {summary['return_pct']:.2f}% return, {summary['total_trades']} trades")


if __name__ == "__main__":
    asyncio.run(main())