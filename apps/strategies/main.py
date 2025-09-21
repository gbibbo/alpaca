#!/usr/bin/env python3
"""
apps/strategies/main.py
Trading Strategies - Fixed version with unified configuration
Consumes market bars and generates trading signals
"""

import os
import asyncio
import logging
import sys
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from collections import defaultdict, deque
from pathlib import Path

# Add lib to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from lib.models import Bar, Signal, SignalSide
from lib.bus import get_bus, connect_bus
from lib.settings import get_settings

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class TechnicalIndicators:
    """Technical analysis indicators"""
    
    @staticmethod
    def sma(prices: List[float], period: int) -> Optional[float]:
        """Simple Moving Average"""
        if len(prices) < period:
            return None
        return sum(prices[-period:]) / period
    
    @staticmethod
    def rsi(prices: List[float], period: int = 14) -> Optional[float]:
        """Relative Strength Index"""
        if len(prices) < period + 1:
            return None
        
        deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
        gains = [d if d > 0 else 0 for d in deltas[-period:]]
        losses = [-d if d < 0 else 0 for d in deltas[-period:]]
        
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        
        if avg_loss == 0:
            return 100
        
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))
    
    @staticmethod
    def macd(prices: List[float], fast: int = 12, slow: int = 26, signal: int = 9) -> tuple:
        """MACD indicator"""
        if len(prices) < slow:
            return None, None, None
        
        # Calculate EMAs
        def ema(data, period):
            if len(data) < period:
                return [None] * len(data)
            
            alpha = 2 / (period + 1)
            ema_values = [None] * (period - 1)
            ema_values.append(sum(data[:period]) / period)
            
            for i in range(period, len(data)):
                ema_values.append(alpha * data[i] + (1 - alpha) * ema_values[-1])
            
            return ema_values
        
        ema_fast = ema(prices, fast)
        ema_slow = ema(prices, slow)
        
        if ema_fast[-1] is None or ema_slow[-1] is None:
            return None, None, None
        
        macd_line = ema_fast[-1] - ema_slow[-1]
        
        # For simplicity, return basic MACD without signal line calculation
        return macd_line, 0, macd_line

class Random50Strategy:
    """Random 50/50 strategy for infrastructure testing"""
    
    def __init__(self):
        self.name = "random_50_50"
        np.random.seed(42)  # For reproducible results
    
    def analyze(self, symbol: str, bars: List[Bar]) -> Optional[Signal]:
        """Generate random BUY/SELL signals"""
        if len(bars) < 10:  # Need some bars before signaling
            return None
        
        latest_bar = bars[-1]
        
        # Random decision
        if np.random.random() > 0.95:  # 5% chance of signal per bar
            side = SignalSide.BUY if np.random.random() > 0.5 else SignalSide.SELL
            confidence = np.random.uniform(0.4, 0.8)
            
            return Signal(
                symbol=symbol,
                timestamp=datetime.utcnow(),
                side=side,
                confidence=confidence,
                price=latest_bar.close,
                source=self.name,
                metadata={
                    "strategy_type": "random",
                    "bar_count": len(bars),
                    "latest_price": latest_bar.close
                }
            )
        
        return None

class SmartTechnicalStrategy:
    """Technical analysis strategy using multiple indicators"""
    
    def __init__(self):
        self.name = "smart_technical"
        self.min_bars = 50  # Minimum bars needed for analysis
    
    def analyze(self, symbol: str, bars: List[Bar]) -> Optional[Signal]:
        """Analyze bars and generate technical signals"""
        if len(bars) < self.min_bars:
            return None
        
        # Get closing prices
        closes = [bar.close for bar in bars]
        latest_bar = bars[-1]
        
        # Calculate indicators
        sma_20 = TechnicalIndicators.sma(closes, 20)
        sma_50 = TechnicalIndicators.sma(closes, 50)
        rsi = TechnicalIndicators.rsi(closes, 14)
        macd, _, _ = TechnicalIndicators.macd(closes)
        
        if None in [sma_20, sma_50, rsi]:
            return None
        
        current_price = latest_bar.close
        
        # Scoring system
        buy_signals = 0
        sell_signals = 0
        
        # Trend analysis
        if current_price > sma_20 > sma_50:
            buy_signals += 1
        elif current_price < sma_20 < sma_50:
            sell_signals += 1
        
        # RSI analysis
        if rsi < 30:  # Oversold
            buy_signals += 1
        elif rsi > 70:  # Overbought
            sell_signals += 1
        
        # MACD analysis
        if macd and macd > 0:
            buy_signals += 1
        elif macd and macd < 0:
            sell_signals += 1
        
        # Volatility check (avoid signals during high volatility)
        recent_prices = closes[-20:]
        volatility = np.std(recent_prices) / np.mean(recent_prices)
        
        if volatility > 0.05:  # High volatility, reduce confidence
            confidence_multiplier = 0.5
        else:
            confidence_multiplier = 1.0
        
        # Generate signal
        if buy_signals >= 2:
            confidence = min(0.9, (buy_signals / 3) * 0.8 * confidence_multiplier)
            
            return Signal(
                symbol=symbol,
                timestamp=datetime.utcnow(),
                side=SignalSide.BUY,
                confidence=confidence,
                price=current_price,
                source=self.name,
                metadata={
                    "buy_signals": buy_signals,
                    "sell_signals": sell_signals,
                    "sma_20": sma_20,
                    "sma_50": sma_50,
                    "rsi": rsi,
                    "macd": macd,
                    "volatility": volatility,
                    "bar_count": len(bars)
                }
            )
        
        elif sell_signals >= 2:
            confidence = min(0.9, (sell_signals / 3) * 0.8 * confidence_multiplier)
            
            return Signal(
                symbol=symbol,
                timestamp=datetime.utcnow(),
                side=SignalSide.SELL,
                confidence=confidence,
                price=current_price,
                source=self.name,
                metadata={
                    "buy_signals": buy_signals,
                    "sell_signals": sell_signals,
                    "sma_20": sma_20,
                    "sma_50": sma_50,
                    "rsi": rsi,
                    "macd": macd,
                    "volatility": volatility,
                    "bar_count": len(bars)
                }
            )
        
        return None

class StrategyEngine:
    """Main strategy engine that manages multiple strategies"""
    
    def __init__(self):
        self.settings = get_settings()
        self.bus = get_bus()
        self.running = False
        
        # Initialize strategies
        self.strategies = [
            Random50Strategy(),
            SmartTechnicalStrategy()
        ]
        
        # Bar storage for each symbol
        self.bar_history: Dict[str, deque] = defaultdict(lambda: deque(maxlen=200))
        
        # Track signals to avoid spam
        self.last_signal_time: Dict[str, datetime] = {}
        self.signal_cooldown = timedelta(minutes=5)
        
        logger.info(f"Initialized strategy engine with {len(self.strategies)} strategies")
        logger.info(f"Tracking symbols: {self.settings.symbols_list}")
    
    def should_generate_signal(self, symbol: str, strategy_name: str) -> bool:
        """Check if enough time has passed since last signal"""
        key = f"{symbol}_{strategy_name}"
        last_time = self.last_signal_time.get(key)
        
        if last_time is None:
            return True
        
        return datetime.utcnow() - last_time > self.signal_cooldown
    
    def process_bar(self, bar: Bar):
        """Process incoming bar and generate signals"""
        try:
            # Store bar
            self.bar_history[bar.symbol].append(bar)
            bars = list(self.bar_history[bar.symbol])
            
            logger.debug(f"Processing bar for {bar.symbol}: ${bar.close:.2f} (total bars: {len(bars)})")
            
            # Run strategies
            for strategy in self.strategies:
                try:
                    if not self.should_generate_signal(bar.symbol, strategy.name):
                        continue
                    
                    signal = strategy.analyze(bar.symbol, bars)
                    
                    if signal and signal.confidence > 0.5:  # Minimum confidence threshold
                        # Update last signal time
                        key = f"{bar.symbol}_{strategy.name}"
                        self.last_signal_time[key] = datetime.utcnow()
                        
                        # Publish signal
                        self.bus.publish_signal(signal)
                        
                        logger.info(
                            f"Generated signal: {signal.side} {signal.symbol} "
                            f"(confidence: {signal.confidence:.2%}) from {strategy.name}"
                        )
                        
                        # Publish strategy event
                        self.bus.publish_system_event(
                            event_type="signal_generated",
                            source="strategies",
                            data={
                                "symbol": signal.symbol,
                                "side": signal.side,
                                "confidence": signal.confidence,
                                "strategy": strategy.name,
                                "metadata": signal.metadata
                            }
                        )
                
                except Exception as e:
                    logger.error(f"Error in strategy {strategy.name} for {bar.symbol}: {e}")
        
        except Exception as e:
            logger.error(f"Error processing bar for {bar.symbol}: {e}")
    
    async def consume_bars(self):
        """Consume bars from message bus"""
        logger.info("Starting to consume market bars...")
        
        bars_processed = 0
        signals_generated = 0
        
        async for bar in self.bus.subscribe_bars():
            if not self.running:
                break
            
            try:
                self.process_bar(bar)
                bars_processed += 1
                
                # Log progress periodically
                if bars_processed % 100 == 0:
                    logger.info(f"Processed {bars_processed} bars, generated {signals_generated} signals")
                
            except Exception as e:
                logger.error(f"Error consuming bar: {e}")
    
    async def start(self):
        """Start the strategy engine"""
        logger.info("Starting Strategy Engine...")
        
        # Connect to message bus
        if not connect_bus():
            logger.error("Failed to connect to message bus")
            return False
        
        # Publish service start event
        self.bus.publish_system_event(
            event_type="service_start",
            source="strategies",
            data={
                "strategies": [s.name for s in self.strategies],
                "symbols": self.settings.symbols_list,
                "paper_trading": self.settings.is_paper_trading
            }
        )
        
        self.running = True
        
        try:
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
                data={"reason": "graceful_shutdown"}
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