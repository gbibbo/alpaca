#!/usr/bin/env python3
"""
scripts/sim_random.py
Quick backtesting script (off-bus) for rapid strategy validation
Implements ChatGPT's recommended simple backtester with visualization
FIXED: Added Dict to typing imports
"""

import os
import sys
import csv
import math
import random
import argparse
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Tuple, Optional, Dict  # FIXED: Added Dict import
from dataclasses import dataclass
from pathlib import Path

# Add lib to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.settings import get_settings

# Try to import plotting (optional)
try:
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    PLOTTING_AVAILABLE = True
except ImportError:
    PLOTTING_AVAILABLE = False
    plt = None

@dataclass
class Bar:
    """Simple bar data structure for backtesting"""
    timestamp: datetime
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: int = 0

@dataclass 
class Trade:
    """Represents a completed trade"""
    entry_time: datetime
    exit_time: datetime
    symbol: str
    side: str  # 'BUY' or 'SELL'
    entry_price: float
    exit_price: float
    quantity: int
    pnl: float
    return_pct: float

class AlpacaDataDownloader:
    """Downloads historical data from Alpaca for backtesting"""
    
    def __init__(self):
        self.settings = get_settings()
        self.client = None
        
        if self.settings.has_alpaca_credentials:
            try:
                from alpaca.data.historical import StockHistoricalDataClient
                from alpaca.data.requests import StockBarsRequest
                from alpaca.data.timeframe import TimeFrame as AlpacaTimeFrame
                
                self.client = StockHistoricalDataClient(
                    api_key=self.settings.apca_api_key_id,
                    secret_key=self.settings.apca_api_secret_key
                )
                self.StockBarsRequest = StockBarsRequest
                self.AlpacaTimeFrame = AlpacaTimeFrame
                
                print(f"✅ Alpaca client initialized for data download")
            except Exception as e:
                print(f"❌ Failed to initialize Alpaca client: {e}")
                self.client = None
    
    def download_data(self, symbol: str, start_date: str, end_date: str = None, 
                     timeframe: str = "1Day", feed: str = "iex") -> List[Bar]:
        """Download historical data from Alpaca"""
        if not self.client:
            raise ValueError("❌ Alpaca client not available - check credentials")
        
        try:
            # Parse dates
            start_dt = datetime.fromisoformat(start_date)
            end_dt = datetime.fromisoformat(end_date) if end_date else datetime.now()
            
            print(f"📥 Downloading {symbol} data from {start_dt.date()} to {end_dt.date()}")
            
            # FIXED: Correct Alpaca TimeFrame mapping with fallbacks
            ATF = self.AlpacaTimeFrame
            
            try:
                if timeframe == "1Min":
                    alpaca_tf = ATF.Minute
                elif timeframe == "5Min":
                    # Try different API versions for 5-minute timeframe
                    try:
                        # New API format
                        alpaca_tf = ATF(5, ATF.Unit.Minute)
                    except AttributeError:
                        try:
                            # Alternative format for some versions
                            alpaca_tf = ATF.Minute
                            print("⚠️ Using 1Min as fallback for 5Min request")
                        except:
                            alpaca_tf = ATF.Day
                elif timeframe == "1Hour":
                    alpaca_tf = ATF.Hour
                elif timeframe == "1Day":
                    alpaca_tf = ATF.Day
                else:
                    # Default fallback
                    alpaca_tf = ATF.Day
                    
            except Exception as tf_error:
                print(f"⚠️ TimeFrame mapping error: {tf_error}")
                print("Using daily timeframe as fallback")
                alpaca_tf = ATF.Day
            
            request = self.StockBarsRequest(
                symbol_or_symbols=[symbol],
                timeframe=alpaca_tf,
                start=start_dt,
                end=end_dt,
                feed=feed,
                limit=10000,
                adjustment='all'
            )
            
            response = self.client.get_stock_bars(request)
            
            if response.df is None or response.df.empty:
                print(f"⚠️ No data returned for {symbol}")
                return []
            
            # Convert to Bar objects
            bars = []
            df = response.df.reset_index()
            
            for _, row in df.iterrows():
                bar = Bar(
                    timestamp=row['timestamp'].to_pydatetime(),
                    symbol=symbol,
                    open=float(row['open']),
                    high=float(row['high']),
                    low=float(row['low']),
                    close=float(row['close']),
                    volume=int(row['volume'])
                )
                bars.append(bar)
            
            print(f"✅ Downloaded {len(bars)} bars for {symbol}")
            return sorted(bars, key=lambda x: x.timestamp)
            
        except Exception as e:
            print(f"❌ Error downloading {symbol}: {e}")
            return []

def load_csv_data(csv_path: str, symbol: str) -> List[Bar]:
    """Load data from CSV file"""
    bars = []
    
    try:
        with open(csv_path, 'r') as f:
            reader = csv.DictReader(f)
            
            for row in reader:
                # Handle different timestamp formats
                timestamp_str = row.get('timestamp', row.get('datetime', row.get('date', '')))
                if not timestamp_str:
                    continue
                
                try:
                    if 'T' in timestamp_str:
                        timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                    else:
                        timestamp = datetime.strptime(timestamp_str, '%Y-%m-%d')
                except:
                    continue
                
                bar = Bar(
                    timestamp=timestamp,
                    symbol=symbol,
                    open=float(row['open']),
                    high=float(row['high']),
                    low=float(row['low']),
                    close=float(row['close']),
                    volume=int(float(row.get('volume', 0)))
                )
                bars.append(bar)
        
        bars.sort(key=lambda x: x.timestamp)
        print(f"✅ Loaded {len(bars)} bars from {csv_path}")
        return bars
        
    except Exception as e:
        print(f"❌ Error loading CSV {csv_path}: {e}")
        return []

def generate_test_data(symbol: str, start_date: str, end_date: str) -> List[Bar]:
    """Generate test data if no CSV or Alpaca data available"""
    try:
        start_dt = datetime.fromisoformat(start_date)
        end_dt = datetime.fromisoformat(end_date)
        
        bars = []
        current_date = start_dt
        price = 100.0  # Starting price
        
        print(f"🎲 Generating synthetic data for {symbol} from {start_dt.date()} to {end_dt.date()}")
        
        while current_date <= end_dt:
            # Generate random price movement
            change = random.uniform(-0.05, 0.05)  # ±5% daily change
            price *= (1 + change)
            
            # Generate OHLC data
            open_price = price
            close_price = price * (1 + random.uniform(-0.03, 0.03))
            high_price = max(open_price, close_price) * (1 + random.uniform(0, 0.02))
            low_price = min(open_price, close_price) * (1 - random.uniform(0, 0.02))
            volume = random.randint(1000000, 10000000)
            
            bar = Bar(
                timestamp=current_date,
                symbol=symbol,
                open=round(open_price, 2),
                high=round(high_price, 2),
                low=round(low_price, 2),
                close=round(close_price, 2),
                volume=volume
            )
            bars.append(bar)
            
            # Move to next day
            current_date = current_date.replace(
                day=current_date.day + 1
            ) if current_date.day < 28 else current_date.replace(
                month=current_date.month + 1 if current_date.month < 12 else 1,
                year=current_date.year + 1 if current_date.month == 12 else current_date.year,
                day=1
            )
            
            price = close_price  # Update price for next bar
        
        # Save to CSV for future use
        csv_dir = Path("data/csv")
        csv_dir.mkdir(parents=True, exist_ok=True)
        csv_file = csv_dir / f"{symbol}.csv"
        
        with open(csv_file, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            for bar in bars:
                writer.writerow([
                    bar.timestamp.strftime('%Y-%m-%d'),
                    bar.open, bar.high, bar.low, bar.close, bar.volume
                ])
        
        print(f"✅ Created test data: {csv_file}")
        print(f"📊 Generated {len(bars)} daily bars for {symbol}")
        
        return bars
        
    except Exception as e:
        print(f"❌ Error generating test data: {e}")
        return []

class RandomStrategy:
    """Simple random 50/50 strategy for testing"""
    
    def __init__(self, signal_probability: float = 0.05, seed: int = 42):
        self.signal_probability = signal_probability
        random.seed(seed)
    
    def should_buy(self, bar: Bar, position_size: int) -> bool:
        """Should we buy? (only if flat)"""
        return position_size == 0 and random.random() < self.signal_probability
    
    def should_sell(self, bar: Bar, position_size: int) -> bool:
        """Should we sell? (only if long)"""
        return position_size > 0 and random.random() < self.signal_probability

class SimpleBacktester:
    """Simple backtester for rapid strategy validation"""
    
    def __init__(self, initial_cash: float = 100000, position_size_pct: float = 0.1):
        self.initial_cash = initial_cash
        self.position_size_pct = position_size_pct
        self.reset()
    
    def reset(self):
        """Reset backtest state"""
        self.cash = self.initial_cash
        self.position_size = 0
        self.position_value = 0.0
        self.entry_price = 0.0
        self.trades: List[Trade] = []
        self.equity_curve: List[Tuple[datetime, float]] = []
    
    def get_portfolio_value(self, current_price: float) -> float:
        """Calculate current portfolio value"""
        return self.cash + (self.position_size * current_price)
    
    def buy(self, bar: Bar) -> bool:
        """Execute buy order"""
        if self.position_size > 0:
            return False  # Already long
        
        # Calculate position size
        portfolio_value = self.get_portfolio_value(bar.close)
        target_value = portfolio_value * self.position_size_pct
        shares = int(target_value / bar.close)
        
        if shares < 1:
            return False
        
        cost = shares * bar.close
        if cost > self.cash:
            return False
        
        # Execute trade
        self.cash -= cost
        self.position_size = shares
        self.entry_price = bar.close
        
        print(f"🟢 BUY  {bar.timestamp.date()} | {shares:4} shares @ ${bar.close:7.2f} | Cash: ${self.cash:10,.2f}")
        return True
    
    def sell(self, bar: Bar) -> bool:
        """Execute sell order"""
        if self.position_size <= 0:
            return False  # No position
        
        # Execute trade
        proceeds = self.position_size * bar.close
        self.cash += proceeds
        
        # Calculate trade PnL
        pnl = (bar.close - self.entry_price) * self.position_size
        return_pct = (bar.close - self.entry_price) / self.entry_price * 100
        
        # Record trade
        trade = Trade(
            entry_time=datetime.now(),  # Would need to store actual entry time
            exit_time=bar.timestamp,
            symbol=bar.symbol,
            side='LONG',
            entry_price=self.entry_price,
            exit_price=bar.close,
            quantity=self.position_size,
            pnl=pnl,
            return_pct=return_pct
        )
        self.trades.append(trade)
        
        print(f"🔴 SELL {bar.timestamp.date()} | {self.position_size:4} shares @ ${bar.close:7.2f} | PnL: ${pnl:8.2f} ({return_pct:+5.1f}%)")
        
        # Reset position
        self.position_size = 0
        self.entry_price = 0.0
        
        return True
    
    def update_equity_curve(self, bar: Bar):
        """Update equity curve"""
        portfolio_value = self.get_portfolio_value(bar.close)
        self.equity_curve.append((bar.timestamp, portfolio_value))
    
    def run_backtest(self, bars: List[Bar], strategy) -> Dict:
        """Run backtest with given strategy"""
        self.reset()
        
        print(f"\n📊 Starting backtest: {len(bars)} bars from {bars[0].timestamp.date()} to {bars[-1].timestamp.date()}")
        print(f"💰 Initial capital: ${self.initial_cash:,.2f}")
        print(f"📏 Position size: {self.position_size_pct:.1%} of portfolio")
        print("-" * 80)
        
        for bar in bars:
            # Update equity curve
            self.update_equity_curve(bar)
            
            # Check strategy signals
            if strategy.should_buy(bar, self.position_size):
                self.buy(bar)
            elif strategy.should_sell(bar, self.position_size):
                self.sell(bar)
        
        # Close any remaining position
        if self.position_size > 0:
            final_bar = bars[-1]
            self.sell(final_bar)
        
        # Calculate final results
        final_value = self.get_portfolio_value(bars[-1].close)
        total_return = (final_value - self.initial_cash) / self.initial_cash * 100
        
        # Calculate metrics
        returns = []
        for i in range(1, len(self.equity_curve)):
            prev_value = self.equity_curve[i-1][1]
            curr_value = self.equity_curve[i][1]
            daily_return = (curr_value - prev_value) / prev_value
            returns.append(daily_return)
        
        # Maximum drawdown
        peak = self.initial_cash
        max_drawdown = 0
        for _, value in self.equity_curve:
            if value > peak:
                peak = value
            drawdown = (peak - value) / peak
            if drawdown > max_drawdown:
                max_drawdown = drawdown
        
        # Sharpe ratio (simplified)
        if len(returns) > 1:
            avg_return = sum(returns) / len(returns)
            volatility = math.sqrt(sum((r - avg_return) ** 2 for r in returns) / (len(returns) - 1))
            sharpe = (avg_return / volatility * math.sqrt(252)) if volatility > 0 else 0
        else:
            sharpe = 0
        
        # Win rate
        winning_trades = [t for t in self.trades if t.pnl > 0]
        win_rate = len(winning_trades) / len(self.trades) * 100 if self.trades else 0
        
        results = {
            'initial_capital': self.initial_cash,
            'final_value': final_value,
            'total_return_pct': total_return,
            'total_pnl': final_value - self.initial_cash,
            'num_trades': len(self.trades),
            'winning_trades': len(winning_trades),
            'win_rate_pct': win_rate,
            'max_drawdown_pct': max_drawdown * 100,
            'sharpe_ratio': sharpe,
            'equity_curve': self.equity_curve,
            'trades': self.trades
        }
        
        return results

def print_results(results: Dict, symbol: str):
    """Print backtest results"""
    print("\n" + "="*60)
    print(f"📈 BACKTEST RESULTS - {symbol}")
    print("="*60)
    print(f"Initial Capital:     ${results['initial_capital']:>12,.2f}")
    print(f"Final Value:         ${results['final_value']:>12,.2f}")
    print(f"Total Return:        {results['total_return_pct']:>12.2f}%")
    print(f"Total P&L:           ${results['total_pnl']:>12,.2f}")
    print(f"Number of Trades:    {results['num_trades']:>12}")
    print(f"Winning Trades:      {results['winning_trades']:>12}")
    print(f"Win Rate:            {results['win_rate_pct']:>12.1f}%")
    print(f"Max Drawdown:        {results['max_drawdown_pct']:>12.2f}%")
    print(f"Sharpe Ratio:        {results['sharpe_ratio']:>12.2f}")
    print("="*60)

def plot_results(results: Dict, symbol: str, save_path: str = None):
    """Plot backtest results"""
    if not PLOTTING_AVAILABLE:
        print("⚠️ Matplotlib not available - skipping chart generation")
        return
    
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), height_ratios=[2, 1])
    
    # Extract data
    dates = [item[0] for item in results['equity_curve']]
    values = [item[1] for item in results['equity_curve']]
    
    # Plot equity curve
    ax1.plot(dates, values, linewidth=2, color='blue', label='Portfolio Value')
    ax1.axhline(y=results['initial_capital'], color='gray', linestyle='--', alpha=0.7, label='Initial Capital')
    ax1.set_title(f'{symbol} Backtest Results - Random 50/50 Strategy', fontsize=14, fontweight='bold')
    ax1.set_ylabel('Portfolio Value ($)', fontsize=12)
    ax1.legend()
    ax1.grid(True, alpha=0.3)
    ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'${x:,.0f}'))
    
    # Plot drawdown
    peak_values = []
    peak = results['initial_capital']
    for value in values:
        if value > peak:
            peak = value
        peak_values.append(peak)
    
    drawdowns = [(peak - value) / peak * 100 for peak, value in zip(peak_values, values)]
    ax2.fill_between(dates, drawdowns, alpha=0.3, color='red')
    ax2.plot(dates, drawdowns, color='red', linewidth=1)
    ax2.set_title('Drawdown %', fontsize=12)
    ax2.set_xlabel('Date', fontsize=12)
    ax2.set_ylabel('Drawdown (%)', fontsize=12)
    ax2.grid(True, alpha=0.3)
    
    # Format x-axis
    for ax in [ax1, ax2]:
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45)
    
    plt.tight_layout()
    
    if save_path:
        plt.savefig(save_path, dpi=300, bbox_inches='tight')
        print(f"📊 Chart saved to: {save_path}")
    else:
        plt.show()

def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description="Simple Random Strategy Backtester")
    parser.add_argument("--symbol", required=True, help="Symbol to backtest (e.g., AAPL)")
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", help="End date (YYYY-MM-DD)")
    parser.add_argument("--timeframe", default="1Day", help="Timeframe (1Min, 5Min, 1Hour, 1Day)")
    parser.add_argument("--feed", default="iex", help="Alpaca data feed (iex, sip)")
    parser.add_argument("--csv", help="Load data from CSV file instead of Alpaca")
    parser.add_argument("--initial-cash", type=float, default=100000, help="Initial capital")
    parser.add_argument("--position-size", type=float, default=0.1, help="Position size as fraction of portfolio (0.1 = 10%)")
    parser.add_argument("--signal-prob", type=float, default=0.05, help="Signal probability per bar (0.05 = 5%)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility")
    parser.add_argument("--plot", help="Save plot to file (e.g., results.png)")
    parser.add_argument("--no-plot", action="store_true", help="Skip plotting")
    parser.add_argument("--output", help="Save results to JSON file")
    
    args = parser.parse_args()
    
    try:
        symbol = args.symbol.upper()
        
        # Load data
        bars = []
        
        if args.csv:
            bars = load_csv_data(args.csv, symbol)
        else:
            # Try Alpaca first, then fallback to test data generation
            try:
                downloader = AlpacaDataDownloader()
                bars = downloader.download_data(symbol, args.start, args.end, args.timeframe, args.feed)
            except Exception as e:
                print(f"⚠️ Alpaca download failed: {e}")
                print("🎲 Generating synthetic test data...")
        
        # Generate test data if no bars loaded
        if not bars:
            end_date = args.end or datetime.now().strftime('%Y-%m-%d')
            bars = generate_test_data(symbol, args.start, end_date)
        
        if not bars:
            print("❌ No data loaded - check symbol and date range")
            return 1
        
        # Run backtest
        strategy = RandomStrategy(signal_probability=args.signal_prob, seed=args.seed)
        backtester = SimpleBacktester(
            initial_cash=args.initial_cash,
            position_size_pct=args.position_size
        )
        
        results = backtester.run_backtest(bars, strategy)
        
        # Print results
        print_results(results, symbol)
        
        # Save results if requested
        if args.output:
            import json
            output_data = {
                "backtest_results": results,
                "parameters": {
                    "symbol": symbol,
                    "start": args.start,
                    "end": args.end,
                    "initial_cash": args.initial_cash,
                    "position_size": args.position_size,
                    "signal_probability": args.signal_prob,
                    "seed": args.seed
                }
            }
            
            with open(args.output, 'w') as f:
                json.dump(output_data, f, indent=2, default=str)
            
            print(f"\n📁 Results saved to: {args.output}")
        
        # Plot results
        if not args.no_plot:
            plot_results(results, symbol, args.plot)
        
        return 0
        
    except KeyboardInterrupt:
        print("\n⚠️ Backtest interrupted by user")
        return 0
    except Exception as e:
        print(f"❌ Backtest failed: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    import sys
    sys.exit(main())