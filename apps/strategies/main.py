#!/usr/bin/env python3
"""
apps/strategies/main.py
Strategy engine: routes bars to strategies by (symbol, timeframe) and publishes signals.

Strategies themselves live in apps/strategies/library.py (see lib/strategy_base.py for the
contract). The engine keeps one bar history per (symbol, timeframe) so 1m, 5m, 1h and 1d bars
travelling on the same bus never mix.
"""

import os
import errno
import asyncio
import logging
import sys
import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from collections import defaultdict, deque
from pathlib import Path

# Add lib to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from lib.models import Bar, Signal, TimeFrame
from lib.bus import get_bus, connect_bus
from lib.settings import get_settings
from lib.strategy_base import Strategy, create_strategies
from lib.metrics_helpers import (
    StrategyMetrics, start_metrics_server, find_available_port,
    time_bus_processing
)

# Backwards-compatible re-exports (tests and scripts import these from here)
from apps.strategies.library import (  # noqa: F401
    TechnicalIndicators, Random50Strategy, SmartTechnicalStrategy,
    IntradayMomentum5m, HourlyTrendStrategy, DailyTrendStrategy,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class StrategyEngine:
    """Main strategy engine that manages multiple strategies across timeframes"""

    def __init__(self, strategies: Optional[List[Strategy]] = None):
        os.environ["SERVICE_NAME"] = "strategies"
        self.settings = get_settings()
        self.bus = get_bus()
        self.running = False

        # Strategies: explicit list, else ENABLED_STRATEGIES env (comma list), else all registered
        if strategies is None:
            enabled = [s for s in os.getenv("ENABLED_STRATEGIES", "daily_trend").split(",") if s.strip()]
            strategies = create_strategies(enabled)
        if self.settings.trading_mode != "backtest" and len(strategies) != 1:
            raise ValueError("Operational mode requires one strategy; use isolated backtest accounts to compare strategies")
        self.strategies: List[Strategy] = strategies

        # Bars older than this only warm up indicators (no live signals from historical backfill).
        # 0 disables the check (required for historical replays through the simulator).
        self.max_bar_age_seconds = int(os.getenv("STRATEGY_MAX_BAR_AGE_SECONDS", "120"))

        # Initialize metrics for each strategy
        self.strategy_metrics = {}
        for strategy in self.strategies:
            self.strategy_metrics[strategy.name] = StrategyMetrics(strategy.name)

        # Start metrics server
        try:
            metrics_port = int(os.getenv("STRATEGIES_METRICS_PORT", "8013"))
            start_metrics_server(metrics_port)
            logger.info(f"📊 Strategies metrics available at http://localhost:{metrics_port}/metrics")
        except OSError as e:
            if getattr(e, "errno", None) in (errno.EADDRINUSE, 98, 10048):  # Address already in use (Linux/Windows)
                try:
                    metrics_port = find_available_port(metrics_port + 1)
                    start_metrics_server(metrics_port)
                    logger.warning(f"Metrics port busy. Using fallback http://localhost:{metrics_port}/metrics")
                except Exception as fallback_error:
                    logger.warning(f"Failed to start metrics server on fallback port: {fallback_error}")
            else:
                logger.warning(f"Failed to start metrics server: {e}")
        except Exception as e:
            logger.warning(f"Failed to start metrics server: {e}")

        # Bar storage per (symbol, timeframe); depth = largest max_history among strategies on that timeframe
        self._history_depth: Dict[TimeFrame, int] = defaultdict(lambda: 200)
        for s in self.strategies:
            self._history_depth[s.timeframe] = max(self._history_depth[s.timeframe], s.max_history, s.lookback_bars)
        self.bar_history: Dict[Tuple[str, TimeFrame], deque] = {}

        # Cooldown keyed on BAR time (event time) so it works identically live and in replays
        self.last_signal_bar_time: Dict[Tuple[str, str], datetime] = {}

        # Counters
        self.bars_seen: Dict[TimeFrame, int] = defaultdict(int)
        self.signals_published = 0

        logger.info(f"Initialized strategy engine with {len(self.strategies)} strategies:")
        for s in self.strategies:
            logger.info(f"  - {s.name:22} tf={s.timeframe.value:3} lookback={s.lookback_bars:4} "
                        f"cooldown={s.cooldown_seconds}s expiry={s.signal_expiry_seconds}s")
        logger.info(f"Tracking symbols: {self.settings.symbols_list}")
        if self.max_bar_age_seconds:
            logger.info(f"Live guard: bars older than {self.max_bar_age_seconds}s do not generate signals")

    # ------------------------------------------------------------ helpers
    def _history(self, symbol: str, timeframe: TimeFrame) -> deque:
        key = (symbol, timeframe)
        if key not in self.bar_history:
            self.bar_history[key] = deque(maxlen=self._history_depth[timeframe])
        return self.bar_history[key]

    def should_generate_signal(self, symbol: str, strategy: Strategy, bar_time: datetime) -> bool:
        """Per (symbol, strategy) cooldown measured in bar time."""
        key = (symbol, strategy.name)
        last = self.last_signal_bar_time.get(key)
        if last is None:
            return True
        return (bar_time - last) >= timedelta(seconds=strategy.cooldown_seconds)

    def get_strategies_for(self, timeframe: TimeFrame) -> List[Strategy]:
        return [s for s in self.strategies if s.timeframe == timeframe]

    # ------------------------------------------------------------ core
    def process_bar(self, bar: Bar) -> int:
        """Process incoming bar and generate signals. Returns number of signals published."""
        published = 0
        try:
            history = self._history(bar.symbol, bar.timeframe)
            if history and bar.timestamp <= history[-1].timestamp:
                logger.debug(f"Ignoring duplicate/out-of-order {bar.timeframe.value} bar for {bar.symbol} @ {bar.timestamp}")
                return 0
            if not bar.is_complete:
                return 0
            history.append(bar)
            self.bars_seen[bar.timeframe] += 1
            bars = list(history)

            logger.debug(f"{bar.symbol} {bar.timeframe.value} bar ${bar.close:.2f} (history: {len(bars)})")

            # Stale bars (historical backfill on restart) only warm up indicators
            if self.max_bar_age_seconds > 0:
                from lib.backtest import available_at
                age = (datetime.now(timezone.utc) - available_at(bar)).total_seconds()
                if age < 0 or age > self.max_bar_age_seconds:
                    return 0

            for strategy in self.get_strategies_for(bar.timeframe):
                try:
                    if not strategy.applies_to(bar.symbol):
                        continue
                    if len(bars) < strategy.lookback_bars:
                        continue
                    if not self.should_generate_signal(bar.symbol, strategy, bar.timestamp):
                        continue

                    signal = strategy.analyze(bar.symbol, bars)
                    if signal is None or float(signal.confidence) < strategy.min_confidence:
                        continue

                    self.last_signal_bar_time[(bar.symbol, strategy.name)] = bar.timestamp

                    if strategy.name in self.strategy_metrics:
                        self.strategy_metrics[strategy.name].signal_generated(
                            symbol=signal.symbol, side=signal.side.value, source=strategy.name
                        )

                    self.bus.publish_signal(signal)
                    published += 1
                    self.signals_published += 1

                    logger.info(
                        f"Generated signal: {signal.side.value} {signal.symbol} "
                        f"(confidence: {float(signal.confidence):.0%}, tf: {bar.timeframe.value}) from {strategy.name}"
                    )

                    self.bus.publish_system_event(
                        event_type="signal_generated",
                        source="strategies",
                        data={
                            "symbol": signal.symbol,
                            "side": signal.side.value,
                            "confidence": float(signal.confidence),
                            "strategy": strategy.name,
                            "timeframe": bar.timeframe.value,
                            "metadata": signal.metadata
                        }
                    )

                except Exception as e:
                    logger.error(f"Error in strategy {strategy.name} for {bar.symbol}: {e}")

        except Exception as e:
            logger.error(f"Error processing bar for {bar.symbol}: {e}")
        return published

    def handle_strategy_config(self, event_data: dict):
        """Handle strategy configuration events (e.g., seed updates)"""
        try:
            if event_data.get("config_type") == "reproducible_mode":
                seed = event_data.get("random_seed")
                if seed is not None:
                    logger.info(f"Updating strategy seeds to: {seed}")
                    for strategy in self.strategies:
                        strategy.set_seed(seed)
        except Exception as e:
            logger.error(f"Error handling strategy config: {e}")

    def get_stats(self) -> dict:
        return {
            "strategies": [s.describe() for s in self.strategies],
            "bars_seen": {tf.value: n for tf, n in self.bars_seen.items()},
            "signals_published": self.signals_published,
            "histories": {f"{sym}:{tf.value}": len(h) for (sym, tf), h in self.bar_history.items()},
        }

    # ------------------------------------------------------------ consumption
    async def consume_bars(self):
        """Consume bars from message bus with Streams-optimized processing"""
        logger.info("Starting to consume market bars...")

        bars_processed = 0
        signals_generated = 0

        # Check if we're using Streams backend for optimized consumption
        if hasattr(self.bus.backend, 'consume_with_handler') and self.bus.get_stats().get('backend') == 'streams':
            logger.info("Using Redis Streams optimized consumption with safe ACK pattern")

            async def bar_handler(msg_data: dict) -> bool:
                """Handler for Streams-based bar processing"""
                nonlocal bars_processed, signals_generated
                try:
                    if not self.running:
                        return False

                    # Parse bar from message data
                    if msg_data.get("type") != "bar":
                        return True  # ACK non-bar messages

                    bar_data = json.loads(msg_data["data"])
                    bar = Bar.model_validate(bar_data)

                    # Process the bar
                    signals_generated += self.process_bar(bar)
                    bars_processed += 1

                    # Log progress periodically
                    if bars_processed % 100 == 0:
                        logger.info(f"Processed {bars_processed} bars, generated {signals_generated} signals")

                    # Test crash simulation for pending message testing
                    if os.getenv("CRASH_AFTER_READ") == "1":
                        logger.warning("CRASH_AFTER_READ=1 detected, simulating crash after processing...")
                        await asyncio.sleep(0.1)  # Small delay to ensure message is in pending state
                        logger.error("Simulating crash - exiting without ACK!")
                        os._exit(1)  # Hard exit without ACK

                    # Return True to ACK the message (only after successful processing)
                    return True

                except Exception as e:
                    logger.error(f"Error processing bar in handler: {e}")
                    # Return False to NOT ACK the message (it will remain pending for retry)
                    return False

            # Use the safe Streams consumption pattern
            await self.bus.backend.consume_with_handler("bars", bar_handler)

        else:
            # Fallback to Pub/Sub pattern for backward compatibility
            logger.info("Using Pub/Sub consumption pattern")
            async for bar in self.bus.subscribe_bars():
                if not self.running:
                    break

                try:
                    signals_generated += self.process_bar(bar)
                    bars_processed += 1

                    # Log progress periodically
                    if bars_processed % 100 == 0:
                        logger.info(f"Processed {bars_processed} bars, generated {signals_generated} signals")

                except Exception as e:
                    logger.error(f"Error consuming bar: {e}")
                    await asyncio.sleep(1)  # Brief pause on error

    async def consume_strategy_events(self):
        """Consume strategy configuration events"""
        try:
            logger.info("Starting to consume strategy configuration events...")
            async for event in self.bus.subscribe_system_events():
                if not self.running:
                    break

                try:
                    logger.debug(f"Received system event: {event.event_type} from {event.source}")
                    if event.event_type == "strategy_config":
                        logger.info(f"Processing strategy config from {event.source}")
                        self.handle_strategy_config(event.data)
                except Exception as e:
                    logger.error(f"Error processing strategy event: {e}")

        except Exception as e:
            logger.error(f"Error consuming strategy events: {e}")

    async def start(self):
        """Start the strategy engine"""
        logger.info("Starting Strategy Engine...")

        # Connect to message bus
        if not connect_bus():
            logger.error("Failed to connect to message bus")
            return False

        # Mark service start in metrics for all strategies
        for metrics in self.strategy_metrics.values():
            metrics.mark_service_start()

        # Publish service start event
        self.bus.publish_system_event(
            event_type="service_start",
            source="strategies",
            data={
                "strategies": [s.describe() for s in self.strategies],
                "symbols": self.settings.symbols_list,
                "paper_trading": self.settings.is_paper_trading
            }
        )

        self.running = True

        try:
            # Start consuming strategy configuration events in background
            asyncio.create_task(self.consume_strategy_events())

            # Start consuming bars
            await self.consume_bars()

        except KeyboardInterrupt:
            logger.info("Received shutdown signal")
        except Exception as e:
            logger.error(f"Fatal error: {e}")

            # Publish error event
            self.bus.publish_system_event(
                event_type="service_error",
                source="strategies",
                data={"error": str(e)}
            )
        finally:
            await self.stop()

    async def stop(self):
        """Stop the strategy engine"""
        logger.info("Stopping strategy engine...")
        self.running = False

        # Publish service stop event
        if self.bus:
            self.bus.publish_system_event(
                event_type="service_stop",
                source="strategies",
                data={"reason": "graceful_shutdown", "stats": self.get_stats()}
            )
            self.bus.disconnect()


async def main():
    """Main entry point"""
    try:
        engine = StrategyEngine()
        await engine.start()

    except KeyboardInterrupt:
        logger.info("Shutdown requested")
    except Exception as e:
        logger.error(f"Unhandled error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
