#!/usr/bin/env python3
"""
apps/simulator/main.py
Historical Data Simulator - Replay histórico para backtesting end-to-end
Implementa las sugerencias de ChatGPT para validar todo el pipeline de trading
FIXED: Alpaca TimeFrame mapping issue
"""

import os
import sys
import asyncio
import logging
import argparse
import csv
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from pathlib import Path
from decimal import Decimal
import time

# Add lib to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from lib.models import Bar, TimeFrame
from lib.bus import connect_bus, get_bus
from lib.settings import get_settings
from lib.time_utils import TimeUtils
from lib.metrics_helpers import (
    ServiceMetrics, start_metrics_server, BusMetrics,
    Counter, TRADING_REGISTRY
)

# Simulator-specific metrics
BARS_PUBLISHED = Counter(
    'trading_simulator_bars_published_total',
    'Total number of bars published by simulator',
    ['symbol'],
    registry=TRADING_REGISTRY
)

SIM_TICKS_TOTAL = Counter(
    'trading_simulator_ticks_total',
    'Total number of simulation ticks processed',
    registry=TRADING_REGISTRY
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class AlpacaDataLoader:
    """Loads historical data from Alpaca Markets API"""
    
    def __init__(self):
        self.settings = get_settings()
        self.data_client = None
        
        # Initialize Alpaca client if credentials are available
        if self.settings.has_alpaca_credentials:
            try:
                from alpaca.data.historical import StockHistoricalDataClient
                from alpaca.data.requests import StockBarsRequest
                from alpaca.data.timeframe import TimeFrame as AlpacaTimeFrame
                
                self.data_client = StockHistoricalDataClient(
                    api_key=self.settings.apca_api_key_id,
                    secret_key=self.settings.apca_api_secret_key
                )
                self.AlpacaTimeFrame = AlpacaTimeFrame
                self.StockBarsRequest = StockBarsRequest
                logger.info("Alpaca data client initialized")
            except Exception as e:
                logger.error(f"Failed to initialize Alpaca client: {e}")
                self.data_client = None
        else:
            logger.warning("No Alpaca credentials - only CSV mode available")
    
    def load_from_alpaca(self, symbol: str, start_date: str, end_date: str = None, 
                        timeframe: str = "1Min", feed: str = "iex") -> List[Bar]:
        """Load historical data from Alpaca API"""
        if not self.data_client:
            raise ValueError("Alpaca client not initialized - check credentials")
        
        try:
            # Parse dates
            start_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
            end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00')) if end_date else datetime.utcnow()
            
            logger.info(f"Loading {symbol} data from {start_dt} to {end_dt} (timeframe: {timeframe}, feed: {feed})")
            
            # FIXED: Correct Alpaca TimeFrame mapping
            ATF = self.AlpacaTimeFrame  # alias
            tf_in = timeframe
            
            try:
                if tf_in == "1Min":
                    alpaca_tf = ATF.Minute
                elif tf_in == "5Min":
                    # TimeFrame with minute multiples (new API)
                    alpaca_tf = ATF(5, ATF.Unit.Minute)
                elif tf_in == "1Hour":
                    alpaca_tf = ATF.Hour
                elif tf_in == "1Day":
                    alpaca_tf = ATF.Day
                else:
                    # fallback for other formats
                    if tf_in.endswith("Min"):
                        n = int(tf_in[:-3])
                        alpaca_tf = ATF(n, ATF.Unit.Minute)
                    elif tf_in.endswith("Hour"):
                        n = int(tf_in[:-4]) if tf_in[:-4].isdigit() else 1
                        alpaca_tf = ATF(n, ATF.Unit.Hour)
                    else:
                        alpaca_tf = ATF.Day
            except Exception as e:
                logger.error(f"Unsupported timeframe '{tf_in}': {e}")
                alpaca_tf = ATF.Day
            
            # Create request with corrected timeframe
            request = self.StockBarsRequest(
                symbol_or_symbols=[symbol],
                timeframe=alpaca_tf,
                start=start_dt,
                end=end_dt,
                feed=feed,
                limit=10000,
                adjustment='all'
            )
            
            # Get data
            response = self.data_client.get_stock_bars(request)
            
            if response.df is None or response.df.empty:
                logger.warning(f"No data returned for {symbol}")
                return []
            
            # Convert to Bar objects
            bars = []
            df = response.df.reset_index()
            
            for _, row in df.iterrows():
                # Determine internal timeframe for our Bar model
                internal_tf = TimeFrame.MINUTE if 'Min' in timeframe else TimeFrame.DAY
                
                bar = Bar(
                    symbol=symbol,
                    timestamp=row['timestamp'].to_pydatetime(),
                    open=Decimal(str(row['open'])),
                    high=Decimal(str(row['high'])),
                    low=Decimal(str(row['low'])),
                    close=Decimal(str(row['close'])),
                    volume=int(row['volume']),
                    timeframe=internal_tf
                )
                bars.append(bar)
            
            logger.info(f"Loaded {len(bars)} bars for {symbol}")
            return bars
            
        except Exception as e:
            logger.error(f"Error loading data for {symbol}: {e}")
            return []
    
    def load_from_csv(self, csv_path: str, symbol: str) -> List[Bar]:
        """Load historical data from CSV file"""
        bars = []
        
        try:
            with open(csv_path, 'r') as f:
                reader = csv.DictReader(f)
                
                for row in reader:
                    # Expected columns: timestamp, open, high, low, close, volume
                    timestamp_str = row.get('timestamp', row.get('datetime', ''))
                    if not timestamp_str:
                        continue
                    
                    # Parse timestamp
                    try:
                        if 'T' in timestamp_str:
                            timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                        else:
                            timestamp = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')
                    except:
                        try:
                            timestamp = datetime.strptime(timestamp_str, '%Y-%m-%d')
                        except:
                            logger.warning(f"Could not parse timestamp: {timestamp_str}")
                            continue
                    
                    bar = Bar(
                        symbol=symbol,
                        timestamp=timestamp,
                        open=Decimal(str(row['open'])),
                        high=Decimal(str(row['high'])),
                        low=Decimal(str(row['low'])),
                        close=Decimal(str(row['close'])),
                        volume=int(float(row.get('volume', 0))),
                        timeframe=TimeFrame.MINUTE
                    )
                    bars.append(bar)
            
            # Sort by timestamp
            bars.sort(key=lambda b: b.timestamp)
            logger.info(f"Loaded {len(bars)} bars from CSV for {symbol}")
            return bars
            
        except Exception as e:
            logger.error(f"Error loading CSV {csv_path}: {e}")
            return []

class HistoricalSimulator:
    """
    Historical data simulator for end-to-end backtesting
    Replays historical data through the message bus at configurable speed
    """
    
    def __init__(self, speed_multiplier: float = 1.0):
        self.speed_multiplier = speed_multiplier
        self.data_loader = AlpacaDataLoader()
        self.bus = None
        self.running = False

        # Initialize metrics
        self.metrics = ServiceMetrics('simulator')

        # Start metrics server
        try:
            metrics_port = int(os.getenv("SIMULATOR_METRICS_PORT", "8014"))
            start_metrics_server(metrics_port)
            logger.info(f"📊 Simulator metrics available at http://localhost:{metrics_port}/metrics")
        except OSError as e:
            if getattr(e, "errno", None) == 98:  # Address already in use
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
        self.stats = {
            'bars_published': 0,
            'start_time': None,
            'end_time': None,
            'symbols_processed': set()
        }
    
    def connect(self) -> bool:
        """Connect to message bus"""
        if not connect_bus():
            logger.error("Failed to connect to message bus")
            return False
        
        self.bus = get_bus()
        
        # Publish simulator start event
        self.bus.publish_system_event(
            event_type="simulator_started",
            source="historical_simulator",
            data={
                "speed_multiplier": self.speed_multiplier,
                "mode": "historical_replay"
            }
        )
        
        return True

    def set_random_seed(self, seed: int):
        """Set random seed and publish to strategies"""
        if seed is not None:
            logger.info(f"Setting random seed: {seed}")

            # Publish seed configuration to strategies
            self.bus.publish_system_event(
                event_type="strategy_config",
                source="simulator",
                data={
                    "random_seed": seed,
                    "config_type": "reproducible_mode"
                }
            )

    async def simulate_symbol(self, symbol: str, bars: List[Bar], 
                            real_time_delay: bool = True) -> int:
        """
        Simulate historical data for a single symbol
        Returns number of bars published
        """
        if not bars:
            logger.warning(f"No bars to simulate for {symbol}")
            return 0
        
        logger.info(f"Starting simulation for {symbol}: {len(bars)} bars")
        logger.info(f"Date range: {bars[0].timestamp} to {bars[-1].timestamp}")
        
        bars_published = 0
        prev_timestamp = None
        
        for i, bar in enumerate(bars):
            if not self.running:
                break
            
            # Calculate delay for real-time simulation
            if real_time_delay and prev_timestamp:
                time_diff = bar.timestamp - prev_timestamp
                delay_seconds = time_diff.total_seconds() / self.speed_multiplier
                
                # Cap delay to reasonable maximum (e.g., 5 seconds)
                delay_seconds = min(delay_seconds, 5.0)
                
                if delay_seconds > 0:
                    await asyncio.sleep(delay_seconds)
            
            # Publish bar to message bus
            self.bus.publish_bar(bar)
            bars_published += 1

            # Record metrics
            BARS_PUBLISHED.labels(symbol=bar.symbol).inc()
            SIM_TICKS_TOTAL.inc()
            
            # Log progress periodically
            if bars_published % 100 == 0:
                progress = (i + 1) / len(bars) * 100
                logger.info(f"{symbol}: Published {bars_published} bars ({progress:.1f}%)")
            
            prev_timestamp = bar.timestamp
        
        logger.info(f"Completed simulation for {symbol}: {bars_published} bars published")
        return bars_published
    
    async def simulate_multiple_symbols(self, symbol_data: Dict[str, List[Bar]], 
                                      real_time_delay: bool = True) -> Dict[str, int]:
        """
        Simulate multiple symbols in parallel, maintaining chronological order
        """
        results = {}
        
        # Create tasks for each symbol
        tasks = []
        for symbol, bars in symbol_data.items():
            if bars:
                task = asyncio.create_task(
                    self.simulate_symbol(symbol, bars, real_time_delay)
                )
                tasks.append((symbol, task))
        
        # Wait for all tasks to complete
        for symbol, task in tasks:
            try:
                bars_published = await task
                results[symbol] = bars_published
                self.stats['symbols_processed'].add(symbol)
            except Exception as e:
                logger.error(f"Error simulating {symbol}: {e}")
                results[symbol] = 0
        
        return results
    
    def get_stats(self) -> Dict:
        """Get simulation statistics"""
        return {
            **self.stats,
            'symbols_processed': list(self.stats['symbols_processed']),
            'duration_seconds': (
                (self.stats['end_time'] - self.stats['start_time']).total_seconds()
                if self.stats['start_time'] and self.stats['end_time']
                else 0
            )
        }
    
    async def run_simulation(self, symbol_data: Dict[str, List[Bar]], 
                           real_time_delay: bool = True) -> Dict:
        """
        Run complete historical simulation
        """
        self.running = True
        self.stats['start_time'] = TimeUtils.utc_now()
        
        logger.info(f"Starting historical simulation for {len(symbol_data)} symbols")
        logger.info(f"Speed multiplier: {self.speed_multiplier}x")
        logger.info(f"Real-time delays: {'enabled' if real_time_delay else 'disabled'}")
        
        try:
            # Run simulation
            results = await self.simulate_multiple_symbols(symbol_data, real_time_delay)
            
            # Update stats
            self.stats['bars_published'] = sum(results.values())
            self.stats['end_time'] = TimeUtils.utc_now()
            
            # Publish completion event
            self.bus.publish_system_event(
                event_type="simulation_completed",
                source="historical_simulator",
                data={
                    "results": results,
                    "total_bars": self.stats['bars_published'],
                    "symbols": list(results.keys()),
                    "duration_seconds": self.get_stats()['duration_seconds']
                }
            )
            
            logger.info(f"Simulation completed: {self.stats['bars_published']} total bars")
            return results
            
        except Exception as e:
            logger.error(f"Simulation error: {e}")
            self.stats['end_time'] = TimeUtils.utc_now()
            return {}
        finally:
            self.running = False

async def main():
    """Main entry point for historical simulator"""
    parser = argparse.ArgumentParser(description="Historical Data Simulator")
    parser.add_argument("--symbols", required=True, help="Comma-separated list of symbols (e.g., AAPL,GOOGL,TSLA)")
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD or ISO format)")
    parser.add_argument("--end", help="End date (YYYY-MM-DD or ISO format)")
    parser.add_argument("--timeframe", default="1Min", help="Data timeframe (1Min, 5Min, 1Hour, 1Day)")
    parser.add_argument("--feed", default="iex", help="Alpaca data feed (iex, sip)")
    parser.add_argument("--speed", type=float, default=1.0, help="Speed multiplier (1.0 = real time, 10.0 = 10x faster)")
    parser.add_argument("--no-delays", action="store_true", help="Disable real-time delays (publish as fast as possible)")
    parser.add_argument("--csv", help="Load data from CSV files directory instead of Alpaca")
    parser.add_argument("--output", help="Save simulation results to JSON file")
    parser.add_argument("--seed", type=int, help="Random seed for reproducible strategy results")
    
    args = parser.parse_args()
    
    try:
        # Parse symbols
        symbols = [s.strip().upper() for s in args.symbols.split(",")]
        logger.info(f"Simulating symbols: {symbols}")
        
        # Initialize simulator
        simulator = HistoricalSimulator(speed_multiplier=args.speed)
        
        if not simulator.connect():
            logger.error("Failed to connect to message bus")
            return 1

        # Set random seed if provided
        if args.seed is not None:
            simulator.set_random_seed(args.seed)
        
        # Load data for all symbols
        symbol_data = {}
        
        if args.csv:
            # Load from CSV files
            csv_dir = Path(args.csv)
            if not csv_dir.exists():
                logger.error(f"CSV directory not found: {csv_dir}")
                return 1
            
            for symbol in symbols:
                csv_file = csv_dir / f"{symbol}.csv"
                if csv_file.exists():
                    bars = simulator.data_loader.load_from_csv(str(csv_file), symbol)
                    symbol_data[symbol] = bars
                else:
                    logger.warning(f"CSV file not found for {symbol}: {csv_file}")
        else:
            # Load from Alpaca
            for symbol in symbols:
                bars = simulator.data_loader.load_from_alpaca(
                    symbol, args.start, args.end, args.timeframe, args.feed
                )
                symbol_data[symbol] = bars
        
        if not any(symbol_data.values()):
            logger.error("No data loaded for any symbol")
            return 1
        
        # Run simulation
        results = await simulator.run_simulation(
            symbol_data, 
            real_time_delay=not args.no_delays
        )
        
        # Show results
        print("\n" + "="*60)
        print("📊 SIMULATION RESULTS")
        print("="*60)
        
        for symbol, bars_count in results.items():
            print(f"  {symbol:8} {bars_count:8,} bars")
        
        stats = simulator.get_stats()
        print(f"\nTotal bars: {stats['bars_published']:,}")
        print(f"Duration: {stats['duration_seconds']:.1f}s")
        print(f"Speed: {args.speed}x")
        
        # Save results if requested
        if args.output:
            import json
            output_data = {
                "simulation_stats": stats,
                "results": results,
                "parameters": {
                    "symbols": symbols,
                    "start": args.start,
                    "end": args.end,
                    "timeframe": args.timeframe,
                    "feed": args.feed,
                    "speed_multiplier": args.speed
                }
            }
            
            with open(args.output, 'w') as f:
                json.dump(output_data, f, indent=2, default=str)
            
            print(f"\n📁 Results saved to: {args.output}")
        
        print("🎉 Simulation completed successfully!")
        return 0
        
    except KeyboardInterrupt:
        logger.info("Simulation interrupted by user")
        return 0
    except Exception as e:
        logger.error(f"Simulation failed: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))