#!/usr/bin/env python3
"""
Data Ingestor - Alpaca Market Data to Redis Bus
Downloads historical and live data, publishes to message bus
"""

import os
import asyncio
import logging
from datetime import datetime, timedelta
from typing import List
import sys

# Add lib to path
sys.path.append(os.path.join(os.path.dirname(__file__), '../..'))

from lib.models import Bar, TimeFrame
from lib.bus import get_bus, connect_bus
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame as AlpacaTimeFrame
from dotenv import load_dotenv

load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class AlpacaDataIngestor:
    """Ingests market data from Alpaca and publishes to Redis bus"""
    
    def __init__(self, symbols: List[str]):
        self.symbols = symbols
        self.data_client = None
        self.bus = get_bus()
        self.running = False
        
        # Initialize Alpaca client
        api_key = os.getenv('APCA_API_KEY_ID')
        secret_key = os.getenv('APCA_API_SECRET_KEY')
        
        if not api_key or not secret_key:
            raise ValueError("Missing Alpaca API credentials")
        
        self.data_client = StockHistoricalDataClient(
            api_key=api_key,
            secret_key=secret_key
        )
        
        logger.info(f"Initialized data ingestor for symbols: {symbols}")
    
    async def ingest_historical_data(self, days_back: int = 30):
        """Download and publish historical data"""
        logger.info(f"Ingesting {days_back} days of historical data...")
        
        end_time = datetime.now() - timedelta(days=1)  # Yesterday
        start_time = end_time - timedelta(days=days_back)
        
        for symbol in self.symbols:
            try:
                logger.info(f"Downloading historical data for {symbol}")
                
                # Request 1-minute bars
                request = StockBarsRequest(
                    symbol_or_symbols=[symbol],
                    timeframe=AlpacaTimeFrame.Minute,
                    start=start_time,
                    end=end_time
                )
                
                bars_response = self.data_client.get_stock_bars(request)
                
                if bars_response.df is not None and not bars_response.df.empty:
                    df = bars_response.df.reset_index()
                    
                    # Convert to Bar objects and publish
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
                    
                    logger.info(f"Published {len(df)} bars for {symbol}")
                else:
                    logger.warning(f"No historical data for {symbol}")
                    
            except Exception as e:
                logger.error(f"Error downloading data for {symbol}: {e}")
                continue
    
    async def ingest_live_data(self):
        """Simulate live data ingestion (1-minute updates)"""
        logger.info("Starting live data ingestion...")
        
        while self.running:
            try:
                # Get latest bars (last 2 minutes to ensure we get recent data)
                end_time = datetime.now()
                start_time = end_time - timedelta(minutes=5)
                
                for symbol in self.symbols:
                    try:
                        request = StockBarsRequest(
                            symbol_or_symbols=[symbol],
                            timeframe=AlpacaTimeFrame.Minute,
                            start=start_time,
                            end=end_time
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
                            logger.debug(f"Published live bar for {symbol}: ${bar.close}")
                            
                    except Exception as e:
                        logger.error(f"Error getting live data for {symbol}: {e}")
                        continue
                
                # Wait 60 seconds for next minute
                await asyncio.sleep(60)
                
            except Exception as e:
                logger.error(f"Error in live data loop: {e}")
                await asyncio.sleep(10)
    
    async def start(self, historical_days: int = 7):
        """Start the data ingestor"""
        logger.info("Starting Alpaca Data Ingestor...")
        
        # Connect to message bus
        if not connect_bus():
            logger.error("Failed to connect to Redis")
            return False
        
        # Publish system start event
        self.bus.publish_system_event(
            event_type="service_start",
            source="data_ingestor",
            data={"symbols": self.symbols, "historical_days": historical_days}
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
        finally:
            await self.stop()
    
    async def stop(self):
        """Stop the data ingestor"""
        logger.info("Stopping data ingestor...")
        self.running = False
        
        # Publish system stop event
        if self.bus:
            self.bus.publish_system_event(
                event_type="service_stop",
                source="data_ingestor",
                data={"reason": "graceful_shutdown"}
            )
            self.bus.disconnect()

async def main():
    """Main entry point"""
    # Default symbols - can be configured via environment
    symbols = os.getenv('SYMBOLS', 'AAPL,MSFT,GOOGL,TSLA,NVDA').split(',')
    historical_days = int(os.getenv('HISTORICAL_DAYS', '7'))
    
    ingestor = AlpacaDataIngestor(symbols)
    
    try:
        await ingestor.start(historical_days)
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
    except Exception as e:
        logger.error(f"Unhandled error: {e}")

if __name__ == "__main__":
    asyncio.run(main())