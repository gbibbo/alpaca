#!/usr/bin/env python3
"""
apps/data_ingestor/main.py
Data Ingestor - Alpaca Market Data to Redis Bus

Canonical sources: 1-minute bars and daily bars from Alpaca (IEX feed by default).
Derived: 5m and 1h bars are aggregated locally from the 1m stream (lib.resampler), so a single
download feeds every strategy timeframe. All bars go to the same "bars" stream; consumers route
by Bar.timeframe.

Env knobs:
  HISTORICAL_DAYS            1m backfill window (default from settings, 7)
  DAILY_HISTORY_DAYS         daily backfill window in calendar days (default 550 ~ 380 sessions; 12m momentum needs 253)
  RESAMPLE_TIMEFRAMES        comma list derived from 1m (default "5m,1h"; empty disables)
  INGEST_DAILY               "1" (default) to also publish daily bars
  ALPACA_DATA_FEED           iex (default) or sip
"""

import os
import errno
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
import sys
from pathlib import Path

# Add lib to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from lib.models import Bar, TimeFrame
from lib.bus import get_bus, connect_bus
from lib.settings import get_settings
from lib.resampler import BarResampler
from lib.timeframes import parse_timeframe, to_alpaca_timeframe
from lib.metrics_helpers import (
    ServiceMetrics, start_metrics_server, find_available_port,
    BusMetrics
)
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class AlpacaDataIngestor:
    """Ingests market data from Alpaca and publishes to Redis bus with unified configuration"""

    def __init__(self):
        # Load settings
        self.settings = get_settings()
        self.symbols = self.settings.symbols_list
        self.data_client = None
        self.bus = get_bus()
        self.running = False

        # Initialize metrics
        self.metrics = ServiceMetrics('data_ingestor')

        # Start metrics server
        try:
            metrics_port = int(os.getenv("DATA_INGESTOR_METRICS_PORT", "8014"))
            start_metrics_server(metrics_port)
            logger.info(f"📊 Data Ingestor metrics available at http://localhost:{metrics_port}/metrics")
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

        # Performance tracking
        self.bars_published: Dict[str, int] = {tf.value: 0 for tf in TimeFrame}
        self.historical_bars_published = 0
        self.live_bars_published = 0

        # Data feed configuration - use IEX for paper trading accounts
        self.data_feed = os.getenv("ALPACA_DATA_FEED", "iex")

        # Derived timeframes from the 1m stream
        targets = [t for t in os.getenv("RESAMPLE_TIMEFRAMES", "5m,1h").split(",") if t.strip()]
        self.resampler = BarResampler([parse_timeframe(t) for t in targets])

        # Daily bars
        self.ingest_daily = os.getenv("INGEST_DAILY", "1") == "1"
        self.daily_history_days = int(os.getenv("DAILY_HISTORY_DAYS", "550"))
        self.daily_refresh_seconds = int(os.getenv("DAILY_REFRESH_SECONDS", "3600"))
        self._last_daily_refresh = 0.0

        # Last published timestamp per (symbol, timeframe) -> never publish the same bar twice
        self._last_ts: Dict[tuple, datetime] = {}

        # Initialize Alpaca client
        if not self.settings.has_alpaca_credentials:
            raise ValueError("Missing Alpaca API credentials in configuration")

        self.data_client = StockHistoricalDataClient(
            api_key=self.settings.apca_api_key_id,
            secret_key=self.settings.apca_api_secret_key
        )

        logger.info(f"Initialized data ingestor for symbols: {self.symbols}")
        logger.info(f"Historical 1m days: {self.settings.historical_days} | daily days: {self.daily_history_days if self.ingest_daily else 'off'}")
        logger.info(f"Derived timeframes: {[t.value for t in self.resampler.targets] or 'none'}")
        logger.info(f"Paper trading mode: {self.settings.is_paper_trading}")
        logger.info(f"Data feed: {self.data_feed}")

    # ------------------------------------------------------------ publishing
    def _publish(self, bar: Bar, live: bool) -> bool:
        """Publish a bar (any timeframe) once; feeds the resampler for 1m bars."""
        from lib.backtest import available_at
        if not bar.is_complete or available_at(bar) > datetime.now(timezone.utc):
            return False
        key = (bar.symbol, bar.timeframe)
        last = self._last_ts.get(key)
        if last is not None and bar.timestamp <= last:
            return False
        self._last_ts[key] = bar.timestamp

        self.bus.publish_bar(bar)
        BusMetrics.message_published("bars", "bar", "data_ingestor")
        self.bars_published[bar.timeframe.value] += 1
        if live:
            self.live_bars_published += 1
        else:
            self.historical_bars_published += 1

        if bar.timeframe == TimeFrame.MINUTE:
            for derived in self.resampler.add(bar):
                self._publish(derived, live)
        return True

    def _fetch_bars(self, symbol: str, timeframe: TimeFrame, start: datetime, end: datetime) -> List[Bar]:
        request = StockBarsRequest(
            symbol_or_symbols=[symbol],
            timeframe=to_alpaca_timeframe(timeframe),
            start=start,
            end=end,
            feed=self.data_feed,
            adjustment="all",
        )
        response = self.data_client.get_stock_bars(request)
        if response.df is None or response.df.empty:
            return []
        df = response.df.reset_index()
        bars = []
        for _, row in df.iterrows():
            bars.append(Bar(
                symbol=symbol,
                timestamp=row['timestamp'].to_pydatetime(),
                open=float(row['open']),
                high=float(row['high']),
                low=float(row['low']),
                close=float(row['close']),
                volume=int(row['volume']),
                timeframe=timeframe,
            ))
        return bars

    # ------------------------------------------------------------ historical
    async def ingest_historical_data(self, days_back: int = None):
        """Download and publish historical 1m bars (plus derived 5m/1h) and daily bars"""
        if days_back is None:
            days_back = self.settings.historical_days

        end_time = datetime.now(timezone.utc) - timedelta(minutes=16)  # IEX: recent minutes may be delayed
        start_time = end_time - timedelta(days=days_back)

        # Daily first so long-horizon strategies are warm before intraday signals start
        if self.ingest_daily:
            await self.ingest_daily_bars(self.daily_history_days)

        logger.info(f"Ingesting {days_back} days of historical 1m data...")
        total_bars_published = 0

        for symbol in self.symbols:
            try:
                logger.info(f"Downloading historical 1m data for {symbol}")
                bars = self._fetch_bars(symbol, TimeFrame.MINUTE, start_time, end_time)
                count = sum(1 for b in bars if self._publish(b, live=False))
                total_bars_published += count
                if count:
                    logger.info(f"Published {count} historical 1m bars for {symbol}")
                else:
                    logger.warning(f"No historical data for {symbol}")
            except Exception as e:
                logger.error(f"Error downloading historical data for {symbol}: {e}")
                continue
            await asyncio.sleep(0)  # let other tasks breathe

        logger.info(f"Historical data ingestion complete: {total_bars_published} 1m bars, "
                    f"derived {self.resampler.bars_out} bars ({self.resampler.get_stats()['targets']})")

        # Publish system event
        self.bus.publish_system_event(
            event_type="historical_data_complete",
            source="data_ingestor",
            data={
                "symbols": self.symbols,
                "days_back": days_back,
                "total_bars": total_bars_published,
                "bars_by_timeframe": dict(self.bars_published),
                "data_feed": self.data_feed
            }
        )

    async def ingest_daily_bars(self, days_back: int):
        """Download and publish daily bars (session-aligned, straight from Alpaca)"""
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=days_back)
        logger.info(f"Ingesting {days_back} days of daily bars...")
        total = 0
        for symbol in self.symbols:
            try:
                bars = self._fetch_bars(symbol, TimeFrame.DAY, start_time, end_time)
                count = sum(1 for b in bars if self._publish(b, live=False))
                total += count
                logger.info(f"Published {count} daily bars for {symbol}")
            except Exception as e:
                logger.error(f"Error downloading daily data for {symbol}: {e}")
        logger.info(f"Daily ingestion complete: {total} bars")
        return total

    # ------------------------------------------------------------ live
    async def ingest_live_data(self):
        """Poll latest 1m bars every minute; refresh daily bars periodically"""
        logger.info("Starting live data ingestion...")

        consecutive_errors = 0
        max_consecutive_errors = 5

        while self.running:
            try:
                end_time = datetime.now(timezone.utc)
                start_time = end_time - timedelta(minutes=5)
                published = 0

                for symbol in self.symbols:
                    try:
                        bars = self._fetch_bars(symbol, TimeFrame.MINUTE, start_time, end_time)
                        # Publish every new minute we have not seen yet (not just the last one)
                        for bar in bars:
                            if self._publish(bar, live=True):
                                published += 1
                                logger.debug(f"Published live bar for {symbol}: ${bar.close} @ {bar.timestamp}")
                    except Exception as e:
                        logger.error(f"Error getting live data for {symbol}: {e}")
                        continue

                if published > 0:
                    logger.info(f"Published {published} live 1m bars")
                    consecutive_errors = 0

                # Periodic daily refresh (new session bar appears after the close)
                now = asyncio.get_event_loop().time()
                if self.ingest_daily and now - self._last_daily_refresh >= self.daily_refresh_seconds:
                    self._last_daily_refresh = now
                    try:
                        await self.ingest_daily_bars(days_back=7)
                    except Exception as e:
                        logger.error(f"Daily refresh failed: {e}")

                await asyncio.sleep(60)

            except Exception as e:
                consecutive_errors += 1
                logger.error(f"Error in live data loop (#{consecutive_errors}): {e}")

                if consecutive_errors >= max_consecutive_errors:
                    logger.error(f"Too many consecutive errors ({consecutive_errors}), stopping live data ingestion")
                    break

                await asyncio.sleep(min(60, 10 * consecutive_errors))

    # ------------------------------------------------------------ lifecycle
    async def start(self, historical_days: int = None):
        """Start the data ingestor"""
        logger.info("Starting Alpaca Data Ingestor...")

        if not connect_bus():
            logger.error("Failed to connect to message bus")
            return False

        self.metrics.mark_service_start()

        self.bus.publish_system_event(
            event_type="service_start",
            source="data_ingestor",
            data={
                "symbols": self.symbols,
                "historical_days": historical_days or self.settings.historical_days,
                "daily_history_days": self.daily_history_days if self.ingest_daily else 0,
                "derived_timeframes": [t.value for t in self.resampler.targets],
                "paper_trading": self.settings.is_paper_trading,
                "data_feed": self.data_feed
            }
        )

        self.running = True

        try:
            await self.ingest_historical_data(historical_days)
            self._last_daily_refresh = asyncio.get_event_loop().time()
            await self.ingest_live_data()

        except KeyboardInterrupt:
            logger.info("Received shutdown signal")
        except Exception as e:
            logger.error(f"Fatal error: {e}")
            self.bus.publish_system_event(
                event_type="service_error",
                source="data_ingestor",
                data={"error": str(e)}
            )
        finally:
            await self.stop()

    async def stop(self):
        """Stop the data ingestor"""
        logger.info("Stopping data ingestor...")
        self.running = False
        self.metrics.mark_service_stop()

        logger.info("Final Statistics:")
        logger.info(f"  Historical bars published: {self.historical_bars_published}")
        logger.info(f"  Live bars published: {self.live_bars_published}")
        logger.info(f"  By timeframe: {dict(self.bars_published)}")
        logger.info(f"  Resampler: {self.resampler.get_stats()}")

        if self.bus:
            self.bus.publish_system_event(
                event_type="service_stop",
                source="data_ingestor",
                data={"reason": "graceful_shutdown", "bars_by_timeframe": dict(self.bars_published)}
            )
            self.bus.disconnect()


async def main():
    """Main entry point"""
    try:
        ingestor = AlpacaDataIngestor()
        await ingestor.start()

    except KeyboardInterrupt:
        logger.info("Shutdown requested")
    except Exception as e:
        logger.error(f"Unhandled error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
