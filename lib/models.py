#!/usr/bin/env python3
"""
lib/models.py
Trading Platform Models - Enhanced Pydantic contracts with robust validations
Defines all data structures for the trading system with ChatGPT's recommended improvements
"""

from pydantic import BaseModel, Field, field_validator
from datetime import datetime, timezone
from typing import Optional, Literal, Any, Generic, TypeVar
from enum import Enum
from uuid import UUID, uuid4
from decimal import Decimal
import re

# --- Helper functions for default values ---
def get_utc_now():
    return datetime.now(timezone.utc)

def generate_uuid():
    return uuid4()

# --- Enumerations ---
class SchemaVersion(str, Enum):
    """Schema version for backward compatibility"""
    V1 = "v1"

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
    STOP_LIMIT = "stop_limit"

class OrderStatus(str, Enum):
    """Order status"""
    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"

# --- Event Envelope for strong typing ---
T = TypeVar('T')

class Event(BaseModel, Generic[T]):
    """Generic event envelope with metadata"""
    event_id: UUID = Field(default_factory=generate_uuid)
    timestamp: datetime = Field(default_factory=get_utc_now)
    source: str
    event_type: str
    schema_version: SchemaVersion = SchemaVersion.V1
    payload: T

# --- Core Market Data Models ---

class Bar(BaseModel):
    """OHLCV bar data with enhanced validations"""
    schema_version: SchemaVersion = SchemaVersion.V1
    symbol: str = Field(min_length=1, max_length=10)
    timestamp: datetime
    open: Decimal = Field(gt=0, decimal_places=4)
    high: Decimal = Field(gt=0, decimal_places=4)
    low: Decimal = Field(gt=0, decimal_places=4)
    close: Decimal = Field(gt=0, decimal_places=4)
    volume: int = Field(ge=0)
    timeframe: TimeFrame = TimeFrame.MINUTE
    
    @field_validator('symbol')
    @classmethod
    def validate_symbol(cls, v: str) -> str:
        """Validate symbol format (alphanumeric, no special chars except .)"""
        if not re.match(r'^[A-Z0-9.]+$', v.upper()):
            raise ValueError('Symbol must contain only letters, numbers, and dots')
        return v.upper()
    
    @field_validator('high', 'low', 'close')
    @classmethod
    def validate_ohlc_relationship(cls, v, info):
        """Validate OHLC relationships (high >= low, etc.)"""
        if info.data.get('low') and v < info.data['low']:
            raise ValueError('High/Close cannot be less than Low')
        return v
    
    @field_validator('low')
    @classmethod
    def validate_low_against_high(cls, v, info):
        """Validate low is not greater than high"""
        if info.data.get('high') and v > info.data['high']:
            raise ValueError('Low cannot be greater than High')
        return v

class Signal(BaseModel):
    """Trading signal with enhanced validations and idempotency"""
    schema_version: SchemaVersion = SchemaVersion.V1
    signal_id: UUID = Field(default_factory=generate_uuid)
    symbol: str = Field(min_length=1, max_length=10)
    timestamp: datetime = Field(default_factory=get_utc_now)
    side: SignalSide
    confidence: Decimal = Field(ge=0, le=1, decimal_places=3, default=Decimal('0.5'))
    quantity: Optional[Decimal] = Field(gt=0, decimal_places=6, default=None)
    price: Optional[Decimal] = Field(gt=0, decimal_places=4, default=None)
    expire_seconds: int = Field(gt=0, default=300)  # 5 minutes default
    source: str = Field(min_length=1, max_length=50)
    metadata: dict[str, Any] = Field(default_factory=dict)
    
    @field_validator('symbol')
    @classmethod
    def validate_symbol(cls, v: str) -> str:
        """Validate symbol format"""
        if not re.match(r'^[A-Z0-9.]+$', v.upper()):
            raise ValueError('Symbol must contain only letters, numbers, and dots')
        return v.upper()
    
    @field_validator('source')
    @classmethod
    def validate_source(cls, v: str) -> str:
        """Validate source format (alphanumeric and underscores)"""
        if not re.match(r'^[a-zA-Z0-9_]+$', v):
            raise ValueError('Source must contain only letters, numbers, and underscores')
        return v.lower()

class OrderIntent(BaseModel):
    """Order intention with enhanced validations"""
    schema_version: SchemaVersion = SchemaVersion.V1
    intent_id: UUID = Field(default_factory=generate_uuid)
    symbol: str = Field(min_length=1, max_length=10)
    timestamp: datetime = Field(default_factory=get_utc_now)
    side: SignalSide
    quantity: Decimal = Field(gt=0, decimal_places=6)
    order_type: OrderType = OrderType.MARKET
    price: Optional[Decimal] = Field(gt=0, decimal_places=4, default=None)
    stop_loss: Optional[Decimal] = Field(gt=0, decimal_places=4, default=None)
    take_profit: Optional[Decimal] = Field(gt=0, decimal_places=4, default=None)
    client_order_id: str = Field(min_length=1, max_length=50)
    signal_source: str = Field(min_length=1, max_length=50)
    risk_adjusted: bool = False
    max_slippage_bps: Optional[int] = Field(ge=0, le=1000, default=None)  # Basis points
    valid_until: Optional[datetime] = None
    
    @field_validator('symbol')
    @classmethod
    def validate_symbol(cls, v: str) -> str:
        return v.upper()
    
    @field_validator('client_order_id')
    @classmethod
    def validate_client_order_id(cls, v: str) -> str:
        """Validate client order ID format"""
        if not re.match(r'^[a-zA-Z0-9_-]+$', v):
            raise ValueError('Client order ID must contain only letters, numbers, underscores, and hyphens')
        return v
    
    @field_validator('price')
    @classmethod
    def validate_limit_price(cls, v, info):
        """Validate that limit orders have a price"""
        if info.data.get('order_type') == OrderType.LIMIT and v is None:
            raise ValueError('Limit orders must have a price')
        return v

class OrderFill(BaseModel):
    """Order execution result with enhanced tracking"""
    schema_version: SchemaVersion = SchemaVersion.V1
    fill_id: UUID = Field(default_factory=generate_uuid)
    symbol: str = Field(min_length=1, max_length=10)
    timestamp: datetime = Field(default_factory=get_utc_now)
    side: SignalSide
    quantity: Decimal = Field(gt=0, decimal_places=6)
    fill_price: Decimal = Field(gt=0, decimal_places=4)
    fill_quantity: Decimal = Field(gt=0, decimal_places=6)
    broker_order_id: str = Field(min_length=1)  # Can be UUID or string from broker
    client_order_id: str = Field(min_length=1, max_length=50)
    status: OrderStatus
    commission: Decimal = Field(ge=0, decimal_places=4, default=Decimal('0.0'))
    total_value: Decimal = Field(gt=0, decimal_places=4)
    slippage_bps: Optional[int] = Field(ge=0, default=None)  # Actual slippage in basis points
    
    @field_validator('symbol')
    @classmethod
    def validate_symbol(cls, v: str) -> str:
        return v.upper()
    
    @field_validator('fill_quantity')
    @classmethod
    def validate_fill_quantity(cls, v, info):
        """Validate fill quantity doesn't exceed order quantity"""
        if info.data.get('quantity') and v > info.data['quantity']:
            raise ValueError('Fill quantity cannot exceed order quantity')
        return v
    
    @field_validator('total_value')
    @classmethod
    def validate_total_value(cls, v, info):
        """Validate total value calculation"""
        fill_price = info.data.get('fill_price')
        fill_quantity = info.data.get('fill_quantity')
        commission = info.data.get('commission', Decimal('0'))
        
        if fill_price and fill_quantity:
            expected_value = fill_price * fill_quantity + commission
            if abs(v - expected_value) > Decimal('0.01'):  # Allow 1 cent tolerance
                raise ValueError(f'Total value {v} does not match calculation {expected_value}')
        return v

# --- Portfolio & Risk Models ---

class Position(BaseModel):
    """Current position with enhanced tracking"""
    schema_version: SchemaVersion = SchemaVersion.V1
    symbol: str = Field(min_length=1, max_length=10)
    quantity: Decimal = Field(decimal_places=6)  # Can be negative for short positions
    avg_cost: Decimal = Field(gt=0, decimal_places=4)
    market_value: Decimal = Field(decimal_places=4)
    unrealized_pnl: Decimal = Field(decimal_places=4)
    realized_pnl: Decimal = Field(decimal_places=4, default=Decimal('0.0'))
    last_updated: datetime = Field(default_factory=get_utc_now)
    
    @field_validator('symbol')
    @classmethod
    def validate_symbol(cls, v: str) -> str:
        return v.upper()

class PortfolioState(BaseModel):
    """Current portfolio state with enhanced validations"""
    schema_version: SchemaVersion = SchemaVersion.V1
    total_value: Decimal = Field(ge=0, decimal_places=2)
    cash: Decimal = Field(ge=0, decimal_places=2)
    buying_power: Decimal = Field(ge=0, decimal_places=2)
    positions: list[Position] = Field(default_factory=list)
    total_pnl: Decimal = Field(decimal_places=2, default=Decimal('0.0'))
    last_updated: datetime = Field(default_factory=get_utc_now)
    
    @field_validator('buying_power')
    @classmethod
    def validate_buying_power(cls, v, info):
        """Validate buying power is reasonable relative to cash"""
        cash = info.data.get('cash')
        if cash and v < cash:
            raise ValueError('Buying power cannot be less than cash')
        return v

# --- Risk & Configuration Models ---

class RiskMetrics(BaseModel):
    """Risk assessment metrics with enhanced validations"""
    schema_version: SchemaVersion = SchemaVersion.V1
    symbol: str = Field(min_length=1, max_length=10)
    timestamp: datetime = Field(default_factory=get_utc_now)
    volatility: Decimal = Field(ge=0, le=10, decimal_places=4)  # Max 1000% volatility
    beta: Optional[Decimal] = Field(ge=-5, le=5, decimal_places=3, default=None)
    sharpe_ratio: Optional[Decimal] = Field(ge=-10, le=10, decimal_places=3, default=None)
    max_drawdown: Decimal = Field(ge=0, le=1, decimal_places=4)
    var_95: Optional[Decimal] = Field(decimal_places=4, default=None)
    position_size_pct: Decimal = Field(ge=0, le=1, decimal_places=4)
    
    @field_validator('symbol')
    @classmethod
    def validate_symbol(cls, v: str) -> str:
        return v.upper()

class StrategyConfig(BaseModel):
    """Strategy configuration with validations"""
    schema_version: SchemaVersion = SchemaVersion.V1
    name: str = Field(min_length=1, max_length=50)
    enabled: bool = True
    symbols: list[str] = Field(min_length=1)
    timeframe: TimeFrame = TimeFrame.MINUTE
    risk_per_trade: Decimal = Field(gt=0, le=Decimal('0.1'), decimal_places=4, default=Decimal('0.02'))
    max_positions: int = Field(gt=0, le=100, default=5)
    parameters: dict[str, Any] = Field(default_factory=dict)
    
    @field_validator('name')
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate strategy name format"""
        if not re.match(r'^[a-zA-Z0-9_-]+$', v):
            raise ValueError('Strategy name must contain only letters, numbers, underscores, and hyphens')
        return v.lower()
    
    @field_validator('symbols')
    @classmethod
    def validate_symbols(cls, v: list[str]) -> list[str]:
        """Validate all symbols in list"""
        validated_symbols = []
        for symbol in v:
            if not re.match(r'^[A-Z0-9.]+$', symbol.upper()):
                raise ValueError(f'Invalid symbol format: {symbol}')
            validated_symbols.append(symbol.upper())
        return validated_symbols

class RiskConfig(BaseModel):
    """Risk management configuration with enhanced validations"""
    schema_version: SchemaVersion = SchemaVersion.V1
    max_daily_loss: Decimal = Field(gt=0, le=Decimal('0.5'), decimal_places=4, default=Decimal('0.05'))
    max_portfolio_risk: Decimal = Field(gt=0, le=Decimal('1.0'), decimal_places=4, default=Decimal('0.2'))
    max_position_size: Decimal = Field(gt=0, le=Decimal('1.0'), decimal_places=4, default=Decimal('0.1'))
    stop_loss_pct: Decimal = Field(gt=0, le=Decimal('0.5'), decimal_places=4, default=Decimal('0.02'))
    take_profit_pct: Decimal = Field(gt=0, le=Decimal('2.0'), decimal_places=4, default=Decimal('0.06'))
    max_orders_per_minute: int = Field(gt=0, le=1000, default=10)
    cooldown_seconds: int = Field(gt=0, le=86400, default=300)  # Max 24 hours

# --- System Health & Events ---

class SystemHealth(BaseModel):
    """System component health with enhanced tracking"""
    schema_version: SchemaVersion = SchemaVersion.V1
    component: str = Field(min_length=1, max_length=50)
    status: Literal["healthy", "warning", "error", "unknown"]
    timestamp: datetime = Field(default_factory=get_utc_now)
    message: str = Field(max_length=500)
    latency_ms: Optional[Decimal] = Field(ge=0, le=60000, decimal_places=2, default=None)  # Max 60 seconds
    uptime_seconds: Optional[int] = Field(ge=0, default=None)
    metadata: dict[str, Any] = Field(default_factory=dict)

class MessageEvent(BaseModel):
    """Enhanced message bus event with metadata"""
    schema_version: SchemaVersion = SchemaVersion.V1
    event_id: UUID = Field(default_factory=generate_uuid)
    event_type: str = Field(min_length=1, max_length=100)
    timestamp: datetime = Field(default_factory=get_utc_now)
    source: str = Field(min_length=1, max_length=50)
    data: dict[str, Any]
    correlation_id: Optional[UUID] = None  # For tracing related events
    
    @field_validator('event_type')
    @classmethod
    def validate_event_type(cls, v: str) -> str:
        """Validate event type format"""
        if not re.match(r'^[a-z0-9_.-]+$', v):
            raise ValueError('Event type must contain only lowercase letters, numbers, underscores, dots, and hyphens')
        return v.lower()
    
    @field_validator('source')
    @classmethod
    def validate_source(cls, v: str) -> str:
        """Validate source format"""
        if not re.match(r'^[a-zA-Z0-9_-]+$', v):
            raise ValueError('Source must contain only letters, numbers, underscores, and hyphens')
        return v.lower()

# --- Configuration Models ---

class SystemConfig(BaseModel):
    """System-wide configuration with validations"""
    schema_version: SchemaVersion = SchemaVersion.V1
    redis_url: str = Field(default="redis://localhost:6379/0")
    database_url: str = Field(default="postgresql://user:pass@localhost/trading")
    alpaca_base_url: str = Field(default="https://paper-api.alpaca.markets")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    enable_live_trading: bool = False
    strategies: list[StrategyConfig] = Field(default_factory=list)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    
    @field_validator('redis_url', 'database_url', 'alpaca_base_url')
    @classmethod
    def validate_url(cls, v: str) -> str:
        """Basic URL validation"""
        if not (v.startswith('redis://') or v.startswith('postgresql://') or v.startswith('https://')):
            raise ValueError('Invalid URL format')
        return v