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
from lib.timeframes import parse_timeframe, to_alpaca_timeframe
from lib.metrics_helpers import (
    ServiceMetrics, start_metrics_server, BusMetrics, find_available_port,
    Counter, TRADING_REGISTRY
)

# Import persistence
from apps.simulator.persist import BacktestPersistence

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
            
            # One mapping for both the Alpaca request and our internal Bar.timeframe label
            # (previously '1Hour' was requested correctly but labelled DAY internally).
            internal_tf = parse_timeframe(timeframe)
            alpaca_tf = to_alpaca_timeframe(internal_tf)
            
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
    
    def load_from_csv(self, csv_path: str, symbol: str, timeframe: str = "1Min") -> List[Bar]:
        """Load historical data from CSV file; `timeframe` labels the bars (1Min, 5Min, 1Hour, 1Day)"""
        bars = []
        internal_tf = parse_timeframe(timeframe)
        
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
                        timeframe=internal_tf
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
    
    def __init__(self, speed_multiplier: float = 1.0, enable_persistence: bool = False, run_id: Optional[str] = None):
        self.speed_multiplier = speed_multiplier
        self.data_loader = AlpacaDataLoader()
        self.bus = None
        self.running = False
        self.enable_persistence = enable_persistence
        self.persistence = None

        # Initialize persistence if enabled
        if enable_persistence:
            self.persistence = BacktestPersistence(run_id=run_id)
            logger.info(f"Persistence enabled: {self.persistence.run_dir}")

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
        # Safety: the isolated research engine (lib.backtest via main()) is the only supported
        # backtest path. This legacy operational replay publishes to the SHARED bus, which in
        # backtest mode must never happen (it could reach a live strategies/executor chain).
        if get_settings().trading_mode == "backtest":
            raise RuntimeError(
                "HistoricalSimulator bus replay is disabled in backtest mode; run the isolated "
                "engine via `python apps/simulator/main.py --csv ...` (lib.backtest.run_backtest)"
            )
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

            # Persist bar if enabled
            if self.persistence:
                self.persistence.save_bar({
                    "symbol": bar.symbol,
                    "timestamp": bar.timestamp.isoformat(),
                    "open": float(bar.open),
                    "high": float(bar.high),
                    "low": float(bar.low),
                    "close": float(bar.close),
                    "volume": bar.volume,
                    "timeframe": bar.timeframe.value if hasattr(bar.timeframe, 'value') else str(bar.timeframe)
                })

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
        from collections import Counter
        counts = Counter()
        ordered = sorted((b for values in symbol_data.values() for b in values), key=lambda b: (b.timestamp, b.symbol))
        previous = None
        for bar in ordered:
            if not self.running:
                break
            if real_time_delay and previous:
                await asyncio.sleep(min(5, max(0, (bar.timestamp - previous).total_seconds() / self.speed_multiplier)))
            self.bus.publish_bar(bar)
            counts[bar.symbol] += 1
            previous = bar.timestamp
        self.stats['symbols_processed'].update(counts)
        return dict(counts)

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
                           real_time_delay: bool = True,
                           simulation_params: Optional[Dict] = None) -> Dict:
        """
        Run complete historical simulation
        """
        self.running = True
        self.stats['start_time'] = TimeUtils.utc_now()

        logger.info(f"Starting historical simulation for {len(symbol_data)} symbols")
        logger.info(f"Speed multiplier: {self.speed_multiplier}x")
        logger.info(f"Real-time delays: {'enabled' if real_time_delay else 'disabled'}")
        logger.info(f"Persistence: {'enabled' if self.persistence else 'disabled'}")

        # Save simulation parameters if persistence enabled
        if self.persistence and simulation_params:
            self.persistence.save_metadata("simulation_params", simulation_params)
            self.persistence.save_metadata("start_time", self.stats['start_time'].isoformat())

        try:
            # Run simulation
            results = await self.simulate_multiple_symbols(symbol_data, real_time_delay)

            # Update stats
            self.stats['bars_published'] = sum(results.values())
            self.stats['end_time'] = TimeUtils.utc_now()

            # Save final stats and generate summary if persistence enabled
            if self.persistence:
                self.persistence.save_metadata("end_time", self.stats['end_time'].isoformat())
                self.persistence.save_metadata("results", results)

                summary = self.persistence.save_summary()

                # Export to CSV
                try:
                    self.persistence.export_to_csv()
                    logger.info(f"Results exported to CSV: {self.persistence.run_dir / 'data'}")
                except Exception as e:
                    logger.warning(f"CSV export failed: {e}")

                # Try to export to Parquet
                try:
                    self.persistence.export_to_parquet()
                    logger.info(f"Results exported to Parquet: {self.persistence.run_dir / 'data'}")
                except Exception as e:
                    logger.debug(f"Parquet export skipped: {e}")

                # Compute reproducibility hash
                results_hash = self.persistence.compute_hash()
                logger.info(f"Results hash (for reproducibility): {results_hash}")

                logger.info(f"📁 Backtest results saved to: {self.persistence.run_dir}")
                logger.info(f"📊 Summary: {summary}")

            # Publish completion event
            self.bus.publish_system_event(
                event_type="simulation_completed",
                source="historical_simulator",
                data={
                    "results": results,
                    "total_bars": self.stats['bars_published'],
                    "symbols": list(results.keys()),
                    "duration_seconds": self.get_stats()['duration_seconds'],
                    "persistence_enabled": self.persistence is not None,
                    "run_id": self.persistence.run_id if self.persistence else None
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

            # Close persistence
            if self.persistence:
                self.persistence.close()

async def main():
    from lib.backtest import load_csv, ResearchConfig, run_backtest
    import json
    parser = argparse.ArgumentParser(description="Isolated historical portfolio backtest (never sends broker orders)")
    parser.add_argument('--symbols', required=True)
    parser.add_argument('--start', required=True)
    parser.add_argument('--end')
    parser.add_argument('--csv', required=True, help='Directory with SYMBOL.csv files')
    parser.add_argument('--timeframe', default='1Day')
    parser.add_argument('--strategies', default='daily_trend')
    parser.add_argument('--initial-cash', type=float, default=100000)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--output', default='out/backtest.json')
    parser.add_argument('--risk-params', default='{}', help='JSON risk/cost overrides')
    parser.add_argument('--universe-csv', help='Point-in-time index membership CSV (date,symbol) '
                                               'for portfolio strategies; fixes survivorship bias')
    args = parser.parse_args()
    overrides = json.loads(args.risk_params)
    if args.universe_csv:
        overrides['universe_csv'] = args.universe_csv
    config = ResearchConfig(**{**overrides, 'strategies': args.strategies.split(','),
                              'initial_cash': args.initial_cash, 'seed': args.seed})
    bars = load_csv(args.csv, args.symbols.split(','), args.timeframe, args.start, args.end)
    result = run_backtest(bars, config)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps({name: account['metrics'] for name, account in result['accounts'].items()}, indent=2))
    return 0

if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
