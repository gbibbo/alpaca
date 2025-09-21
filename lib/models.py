#!/usr/bin/env python3
"""
lib/models.py
Trading Platform Models - Pydantic contracts
Defines all data structures for the trading system
"""

from pydantic import BaseModel, Field
from datetime import datetime, timezone
from typing import Optional, Literal, Any
from enum import Enum
from uuid import UUID, uuid4

# --- Funciones de ayuda para valores por defecto ---
def get_utc_now():
    return datetime.now(timezone.utc)

def generate_uuid():
    return uuid4()

# --- Enumeraciones ---
class TimeFrame(str, Enum):
    """Supported timeframes"""
    MINUTE = "1m"
    FIVE_MINUTE = "5m"
    HOUR = "1h"
    DAY = "1d"

class SignalSide(str, Enum):
    """Signal directions"""
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"

class OrderType(str, Enum):
    """Order types"""
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"

class OrderStatus(str, Enum):
    """Order status"""
    PENDING = "pending"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"

# --- Modelos de Datos Principales ---

# Core Market Data
class Bar(BaseModel):
    """OHLCV bar data"""
    symbol: str
    timestamp: datetime
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: int = Field(ge=0)
    timeframe: TimeFrame = TimeFrame.MINUTE

# Trading Signals
class Signal(BaseModel):
    """Trading signal from strategy"""
    # <<-- CAMBIO: IDs ahora son UUID para consistencia -->>
    signal_id: UUID = Field(default_factory=generate_uuid)
    symbol: str
    timestamp: datetime = Field(default_factory=get_utc_now)
    side: SignalSide
    confidence: float = Field(ge=0, le=1, default=0.5)
    quantity: Optional[float] = Field(gt=0, default=None)
    price: Optional[float] = Field(gt=0, default=None)
    expire_seconds: int = Field(gt=0, default=300)
    source: str
    metadata: dict = Field(default_factory=dict)

# Risk Management
class OrderIntent(BaseModel):
    """Order intention after risk management"""
    # <<-- CAMBIO: IDs ahora son UUID para consistencia -->>
    intent_id: UUID = Field(default_factory=generate_uuid)
    symbol: str
    timestamp: datetime = Field(default_factory=get_utc_now)
    side: SignalSide
    quantity: float = Field(gt=0)
    order_type: OrderType = OrderType.MARKET
    price: Optional[float] = Field(gt=0, default=None)
    stop_loss: Optional[float] = Field(gt=0, default=None)
    take_profit: Optional[float] = Field(gt=0, default=None)
    client_order_id: str
    signal_source: str
    risk_adjusted: bool = False

# Execution Results
class OrderFill(BaseModel):
    """Executed order result"""
    # <<-- CAMBIO: IDs ahora son UUID para consistencia -->>
    fill_id: UUID = Field(default_factory=generate_uuid)
    symbol: str
    timestamp: datetime = Field(default_factory=get_utc_now)
    side: SignalSide
    quantity: float = Field(gt=0)
    fill_price: float = Field(gt=0)
    fill_quantity: float = Field(gt=0)
    broker_order_id: UUID
    client_order_id: str
    status: OrderStatus
    commission: float = Field(ge=0, default=0.0)
    total_value: float = Field(gt=0)

# Portfolio & Risk
class Position(BaseModel):
    """Current position"""
    symbol: str
    quantity: float
    avg_cost: float = Field(gt=0)
    market_value: float
    unrealized_pnl: float
    realized_pnl: float = 0.0
    last_updated: datetime = Field(default_factory=get_utc_now)

class PortfolioState(BaseModel):
    """Current portfolio state"""
    # <<-- CAMBIO: gt=0 a ge=0 para manejar portfolio vacío -->>
    total_value: float = Field(ge=0)
    cash: float = Field(ge=0)
    buying_power: float = Field(ge=0)
    positions: list[Position] = Field(default_factory=list)
    total_pnl: float = 0.0
    last_updated: datetime = Field(default_factory=get_utc_now)

# Risk Metrics
class RiskMetrics(BaseModel):
    """Risk assessment metrics"""
    symbol: str
    timestamp: datetime = Field(default_factory=get_utc_now)
    volatility: float = Field(ge=0)
    beta: Optional[float] = None
    sharpe_ratio: Optional[float] = None
    max_drawdown: float = Field(ge=0, le=1)
    var_95: Optional[float] = None
    position_size_pct: float = Field(ge=0, le=1)
    
# Strategy Performance
class StrategyMetrics(BaseModel):
    """Strategy performance metrics"""
    strategy_name: str
    timestamp: datetime = Field(default_factory=get_utc_now)
    total_trades: int = Field(ge=0)
    winning_trades: int = Field(ge=0)
    losing_trades: int = Field(ge=0)
    total_pnl: float = 0.0
    sharpe_ratio: Optional[float] = None
    max_drawdown: float = Field(ge=0, le=1)
    win_rate: float = Field(ge=0, le=1)
    avg_win: float = 0.0
    avg_loss: float = 0.0
    
# System Health
class SystemHealth(BaseModel):
    """System component health"""
    component: str
    status: Literal["healthy", "warning", "error"]
    timestamp: datetime = Field(default_factory=get_utc_now)
    message: str
    latency_ms: Optional[float] = Field(ge=0, default=None)
    uptime_seconds: Optional[int] = Field(ge=0, default=None)
    
# Configuration Models
class StrategyConfig(BaseModel):
    """Strategy configuration"""
    name: str
    enabled: bool = True
    symbols: list[str]
    timeframe: TimeFrame = TimeFrame.MINUTE
    risk_per_trade: float = Field(gt=0, le=0.1, default=0.02)
    max_positions: int = Field(gt=0, default=5)
    parameters: dict = Field(default_factory=dict)

class RiskConfig(BaseModel):
    """Risk management configuration"""
    max_daily_loss: float = Field(gt=0, default=0.05)
    max_portfolio_risk: float = Field(gt=0, le=0.5, default=0.2)
    max_position_size: float = Field(gt=0, le=0.5, default=0.1)
    stop_loss_pct: float = Field(gt=0, le=0.2, default=0.02)
    take_profit_pct: float = Field(gt=0, le=1.0, default=0.06)

class SystemConfig(BaseModel):
    """System-wide configuration"""
    redis_url: str = "redis://localhost:6379"
    database_url: str = "postgresql://user:pass@localhost/trading"
    alpaca_base_url: str = "https://paper-api.alpaca.markets"
    log_level: str = "INFO"
    enable_live_trading: bool = False
    strategies: list[StrategyConfig] = Field(default_factory=list)
    risk: RiskConfig = Field(default_factory=RiskConfig)

# Message Bus Events
class MessageEvent(BaseModel):
    """Base class for all message bus events"""
    event_type: str
    timestamp: datetime = Field(default_factory=get_utc_now)
    source: str
    # <<-- CAMBIO: Tipado explícito para el diccionario -->>
    data: dict[str, Any]