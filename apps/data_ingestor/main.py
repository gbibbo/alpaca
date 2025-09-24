#!/usr/bin/env python3
"""
apps/data_ingestor/main.py
Data Ingestor - Alpaca Market Data to Redis Bus (Fixed Version with IEX Feed)
Downloads historical and live data, publishes to message bus with unified configuration
"""

import os
import asyncio
import logging
from datetime import datetime, timedelta
from typing import List
import sys
from pathlib import Path

# Add lib to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from lib.models import Bar, TimeFrame
from lib.bus import get_bus, connect_bus
from lib.settings import get_settings
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame as AlpacaTimeFrame

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
        
        # Data feed configuration - use IEX for paper trading accounts
        self.data_feed = os.getenv("ALPACA_DATA_FEED", "iex")
        
        # Initialize Alpaca client
        if not self.settings.has_alpaca_credentials:
            raise ValueError("Missing Alpaca API credentials in configuration")
        
        self.data_client = StockHistoricalDataClient(
            api_key=self.settings.apca_api_key_id,
            secret_key=self.settings.apca_api_secret_key
        )
        
        logger.info(f"Initialized data ingestor for symbols: {self.symbols}")
        logger.info(f"Historical days: {self.settings.historical_days}")
        logger.info(f"Paper trading mode: {self.settings.is_paper_trading}")
        logger.info(f"Data feed: {self.data_feed}")
    
    async def ingest_historical_data(self, days_back: int = None):
        """Download and publish historical data"""
        if days_back is None:
            days_back = self.settings.historical_days
            
        logger.info(f"Ingesting {days_back} days of historical data...")
        
        end_time = datetime.now() - timedelta(days=1)  # Yesterday
        start_time = end_time - timedelta(days=days_back)
        
        total_bars_published = 0
        
        for symbol in self.symbols:
            try:
                logger.info(f"Downloading historical data for {symbol}")
                
                # Request 1-minute bars with IEX feed
                request = StockBarsRequest(
                    symbol_or_symbols=[symbol],
                    timeframe=AlpacaTimeFrame.Minute,
                    start=start_time,
                    end=end_time,
                    feed=self.data_feed  # Use IEX feed to avoid SIP subscription errors
                )
                
                bars_response = self.data_client.get_stock_bars(request)
                
                if bars_response.df is not None and not bars_response.df.empty:
                    df = bars_response.df.reset_index()
                    
                    # Convert to Bar objects and publish
                    bars_count = 0
                    for _, row in df.iterrows():
                        bar = Bar(
                            symbol=symbol,
                            timestamp=row['timestamp'],
                            open=float(row['open']),
                            high=float(row['high']),
                            low=float(row['low']),
                            close=float(row['close']),
                            volume=int(row['volume']),
                            timeframe=TimeFrame.MINUTE
                        )
                        
                        # Publish to bus
                        self.bus.publish_bar(bar)
                        bars_count += 1
                        total_bars_published += 1
                    
                    logger.info(f"Published {bars_count} historical bars for {symbol}")
                else:
                    logger.warning(f"No historical data for {symbol}")
                    
            except Exception as e:
                logger.error(f"Error downloading historical data for {symbol}: {e}")
                continue
        
        logger.info(f"Historical data ingestion complete: {total_bars_published} total bars published")
        
        # Publish system event
        self.bus.publish_system_event(
            event_type="historical_data_complete",
            source="data_ingestor",
            data={
                "symbols": self.symbols,
                "days_back": days_back,
                "total_bars": total_bars_published,
                "data_feed": self.data_feed
            }
        )
    
    async def ingest_live_data(self):
        """Simulate live data ingestion (1-minute updates)"""
        logger.info("Starting live data ingestion...")
        
        consecutive_errors = 0
        max_consecutive_errors = 5
        
        while self.running:
            try:
                # Get latest bars (last 5 minutes to ensure we get recent data)
                end_time = datetime.now()
                start_time = end_time - timedelta(minutes=5)
                
                bars_published = 0
                
                for symbol in self.symbols:
                    try:
                        # Request latest bars with IEX feed
                        request = StockBarsRequest(
                            symbol_or_symbols=[symbol],
                            timeframe=AlpacaTimeFrame.Minute,
                            start=start_time,
                            end=end_time,
                            feed=self.data_feed  # Use IEX feed to avoid SIP subscription errors
                        )
                        
                        bars_response = self.data_client.get_stock_bars(request)
                        
                        if bars_response.df is not None and not bars_response.df.empty:
                            df = bars_response.df.reset_index()
                            
                            # Get the most recent bar
                            latest_row = df.iloc[-1]
                            
                            bar = Bar(
                                symbol=symbol,
                                timestamp=latest_row['timestamp'],
                                open=float(latest_row['open']),
                                high=float(latest_row['high']),
                                low=float(latest_row['low']),
                                close=float(latest_row['close']),
                                volume=int(latest_row['volume']),
                                timeframe=TimeFrame.MINUTE
                            )
                            
                            # Publish latest bar
                            self.bus.publish_bar(bar)
                            bars_published += 1
                            logger.debug(f"Published live bar for {symbol}: ${bar.close}")
                            
                    except Exception as e:
                        logger.error(f"Error getting live data for {symbol}: {e}")
                        continue
                
                if bars_published > 0:
                    logger.info(f"Published {bars_published} live bars")
                    consecutive_errors = 0  # Reset error counter on success
                
                # Wait 60 seconds for next minute
                await asyncio.sleep(60)
                
            except Exception as e:
                consecutive_errors += 1
                logger.error(f"Error in live data loop (#{consecutive_errors}): {e}")
                
                if consecutive_errors >= max_consecutive_errors:
                    logger.error(f"Too many consecutive errors ({consecutive_errors}), stopping live data ingestion")
                    break
                
                # Back off exponentially on errors
                await asyncio.sleep(min(60, 10 * consecutive_errors))
    
    async def start(self, historical_days: int = None):
        """Start the data ingestor"""
        logger.info("Starting Alpaca Data Ingestor...")
        
        # Connect to message bus
        if not connect_bus():
            logger.error("Failed to connect to message bus")
            return False
        
        # Publish service start event
        self.bus.publish_system_event(
            event_type="service_start",
            source="data_ingestor",
            data={
                "symbols": self.symbols,
                "historical_days": historical_days or self.settings.historical_days,
                "paper_trading": self.settings.is_paper_trading,
                "data_feed": self.data_feed
            }
        )
        
        self.running = True
        
        try:
            # Ingest historical data first
            await self.ingest_historical_data(historical_days)
            
            # Start live data ingestion
            await self.ingest_live_data()
            
        except KeyboardInterrupt:
            logger.info("Received shutdown signal")
        except Exception as e:
            logger.error(f"Fatal error: {e}")
            
            # Publish error event
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
        
        # Publish service stop event
        if self.bus:
            self.bus.publish_system_event(
                event_type="service_stop",
                source="data_ingestor",
                data={"reason": "graceful_shutdown"}
            )
            self.bus.disconnect()

async def main():
    """Main entry point"""
    try:
        # Get settings
        settings = get_settings()
        
        # Create and start ingestor
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