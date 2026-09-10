"""Offline reproductions for the 2026-09-10 audit; never starts trading services.

Prints observed versus required results. Exit 1 means financial defects remain.
Run from repository root: .venv/Scripts/python scripts/audit_financial_integrity.py
"""
import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.update(APCA_API_KEY_ID="", APCA_API_SECRET_KEY="", USE_FAKE_REDIS="1", BUS_BACKEND="pubsub")

from apps.simulator.persist import BacktestPersistence
from apps.pnl_aggregator.main import PnLAggregator
from apps.executor.main import OrderTracker
from apps.simulator.main import HistoricalSimulator
from lib.models import Bar, OrderFill, OrderIntent, SignalSide, OrderType, OrderStatus


def main():
    results = []

    def record(name, actual, expected):
        results.append(dict(check=name, actual=actual, expected=expected, passed=actual == expected))

    with tempfile.TemporaryDirectory() as directory:
        persistence = BacktestPersistence(run_id="audit", output_dir=directory)
        for index, equity in enumerate((100000, 90000)):
            persistence.save_equity_snapshot(dict(timestamp=f"2024-01-0{index+2}T21:00:00Z", equity=equity, cash=equity))
        persistence.save_fill(dict(fill_id="entry", symbol="AAPL", timestamp="2024-01-02T15:00:00Z", side="buy", quantity=1, price=100))
        persistence.save_fill(dict(fill_id="exit", symbol="AAPL", timestamp="2024-01-03T15:00:00Z", side="sell", quantity=1, price=90))
        summary = persistence.get_summary_stats()
        record("losing_equity_return_pct", round(summary["equity"]["return_pct"], 6), -10.0)
        record("losing_round_trip_win_rate_pct", summary["trades"]["win_rate"], 0.0)
        persistence.close()

        with patch("apps.pnl_aggregator.main.start_metrics_server"), patch("apps.pnl_aggregator.main.ServiceMetrics"):
            pnl = PnLAggregator(Decimal("1000"))
        for side, price in ((SignalSide.BUY, 100), (SignalSide.SELL, 110)):
            pnl.process_fill(OrderFill(symbol="AAPL", side=side, quantity=1, fill_quantity=1,
                fill_price=price, total_value=price, status=OrderStatus.FILLED,
                broker_order_id=side.value, client_order_id="audit-" + side.value))
        record("closed_round_trip_return_pct", pnl.export_results(directory)["return_pct"], 1.0)

    tracker = OrderTracker()
    intent = OrderIntent(symbol="AAPL", side=SignalSide.BUY, quantity=10, order_type=OrderType.MARKET,
                         signal_source="audit", client_order_id="audit-partials")
    tracker.add_pending_order(intent, "audit-broker")
    first = tracker.update_order_status("audit-broker", "partially_filled", Decimal(5), Decimal(100))
    second = tracker.update_order_status("audit-broker", "filled", Decimal(10), Decimal(110))
    record("partial_fills_total_notional", float(first.fill_quantity * first.fill_price + second.fill_quantity * second.fill_price), 1100.0)

    simulator = HistoricalSimulator.__new__(HistoricalSimulator)
    simulator.running, simulator.persistence = True, None
    simulator.stats = {"symbols_processed": set()}
    simulator.bus = Mock()
    published = []
    simulator.bus.publish_bar.side_effect = lambda bar: published.append(bar.timestamp)
    start = datetime(2024, 1, 2, 15, tzinfo=timezone.utc)
    data = {symbol: [Bar(symbol=symbol, timestamp=start + timedelta(minutes=i), open=100,
                        high=101, low=99, close=100, volume=100) for i in range(2)] for symbol in ("AAPL", "MSFT")}
    asyncio.run(simulator.simulate_multiple_symbols(data, real_time_delay=False))
    record("global_replay_chronological", published == sorted(published), True)
    print(json.dumps(results, indent=2))
    return int(any(not result["passed"] for result in results))


if __name__ == "__main__":
    raise SystemExit(main())
