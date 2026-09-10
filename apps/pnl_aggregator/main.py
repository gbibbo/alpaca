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
    """Ledger reconstructed from durable fills, marked by incoming bars."""
    def __init__(self, initial_cash=Decimal('100000'), journal_path=None):
        from lib.portfolio import Portfolio
        import sqlite3
        self.ledger = Portfolio(initial_cash)
        self.equity_history = []
        self.metrics = ServiceMetrics('pnl_aggregator')
        self.db = sqlite3.connect(journal_path or ':memory:')
        self.db.execute('CREATE TABLE IF NOT EXISTS fills (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        for (payload,) in self.db.execute('SELECT payload FROM fills ORDER BY rowid'):
            self._apply(OrderFill.model_validate_json(payload))

    def __getattr__(self, name):
        return getattr(self.ledger, name)

    def _apply(self, fill):
        return self.ledger.fill(fill.fill_id, fill.symbol, fill.side, fill.fill_quantity,
                                fill.fill_price, fill.commission, fill.timestamp.isoformat())

    def process_fill(self, fill):
        if str(fill.fill_id) in self.ledger.seen:
            return
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO fills VALUES (?, ?)',
                            (str(fill.fill_id), fill.model_dump_json()))
        self._apply(fill)
        self.equity_history.append(self.ledger.snapshot(fill.timestamp.isoformat()))

    def process_bar(self, bar):
        self.ledger.mark(bar.symbol, bar.close)
        self.equity_history.append(self.ledger.snapshot(bar.timestamp.isoformat()))

    def get_stats(self):
        snapshot = self.ledger.snapshot()
        return {**snapshot, 'total_value': snapshot['equity'],
                'total_realized_pnl': float(self.total_realized), 'total_trades': len(self.closed_trades),
                'active_positions': len(snapshot['positions']),
                'return_pct': float((self.equity / self.initial_cash - 1) * 100)}

    def export_results(self, output_dir='data/pnl_results'):
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        summary = {**self.get_stats(), 'initial_cash': float(self.initial_cash),
                   'final_cash': float(self.cash), 'final_positions': self.get_stats()['positions'],
                   'trades': self.trade_stats()}
        (output / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
        (output / 'fills.json').write_text(json.dumps(self.fills, indent=2), encoding='utf-8')
        (output / 'equity.json').write_text(json.dumps(self.equity_history, indent=2), encoding='utf-8')
        return summary


async def main():
    """Main PnL aggregator loop"""
    logger.info("Starting PnL Aggregator...")

    os.environ["SERVICE_NAME"] = "pnl_aggregator"
    # Connect to message bus
    if not connect_bus():
        logger.error("Failed to connect to message bus")
        return

    bus = get_bus()
    Path('data').mkdir(exist_ok=True)
    aggregator = PnLAggregator(journal_path='data/pnl.sqlite')

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

    async def marks():
        async for bar in bus.subscribe_bars():
            aggregator.process_bar(bar)

    mark_task = asyncio.create_task(marks())
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
        mark_task.cancel()
        await asyncio.gather(mark_task, return_exceptions=True)
        # Export results on shutdown
        logger.info("Exporting final results...")
        summary = aggregator.export_results()

        # Mark service stop
        aggregator.metrics.mark_service_stop()

        logger.info("PnL Aggregator stopped")
        logger.info(f"Final Performance: {summary['return_pct']:.2f}% return, {summary['total_trades']} trades")


if __name__ == "__main__":
    asyncio.run(main())