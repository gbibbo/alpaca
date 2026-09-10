#!/usr/bin/env python3
"""
lib/settings.py
Unified Settings Configuration
Single source of truth for all environment variables and configuration
Enhanced with timezone support
"""

from pydantic_settings import BaseSettings
from typing import List, Optional, Literal
from pydantic import Field
import math
import pytz

class Settings(BaseSettings):
    """Application settings from environment variables"""
    
    # Alpaca API Configuration
    apca_api_key_id: Optional[str] = None
    apca_api_secret_key: Optional[str] = None
    apca_api_base_url: str = "https://paper-api.alpaca.markets"
    apca_api_data_url: str = "https://data.alpaca.markets"
    
    trading_mode: Literal["backtest", "paper", "live"] = "backtest"

    # Trading Configuration
    symbols: str = "AAPL,MSFT,GOOGL,TSLA,NVDA"  # Comma-separated string
    symbol: str = "AAPL"  # Single symbol for backward compatibility
    timeframe_minutes: int = 1
    historical_days: int = 7
    
    # Risk Management
    max_daily_loss: float = 0.05  # 5%
    max_portfolio_risk: float = 0.20  # 20%
    max_position_size: float = 0.10  # 10%
    stop_loss_pct: Optional[float] = 0.02  # 2%
    take_profit_pct: Optional[float] = 0.06  # 6%
    risk_pct: float = 0.02  # Risk per trade
    
    # Enhanced Rate Limiting (using monotonic time)
    max_orders_per_minute: int = 10
    max_signals_per_5min: int = 50
    max_api_calls_per_minute: int = 200  # Alpaca limit
    
    # Redis/Message Bus Configuration
    redis_url: str = "redis://localhost:6379/0"
    use_fake_redis: bool = False
    bus_backend: str = "pubsub"  # "streams" (recommended, durable) or "pubsub" (fire-and-forget)
    allow_fake_redis_fallback: bool = False  # If True, silently use an in-process fakeredis when Redis is down
    
    # API Configuration
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    
    # System Configuration
    log_level: str = "INFO"
    enable_live_trading: bool = False
    paper_trading: bool = True
    
    # Timezone Configuration (new)
    market_timezone: str = "US/Eastern"
    system_timezone: str = "UTC"
    
    # Service Configuration
    data_ingestor_enabled: bool = True
    strategies_enabled: bool = True
    risk_manager_enabled: bool = True
    executor_enabled: bool = True
    api_enabled: bool = True
    
    model_config = {
        "env_file": ".env",
        "env_nested_delimiter": "__",
        "case_sensitive": False,
        "extra": "ignore"
    }
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Validate timezone
        try:
            pytz.timezone(self.market_timezone)
            pytz.timezone(self.system_timezone)
        except pytz.exceptions.UnknownTimeZoneError as e:
            raise ValueError(f"Invalid timezone configuration: {e}")

        # Validate risk parameters fail-fast at construction, so a misconfigured .env cannot
        # reach the live risk manager / executor with nonsensical (negative, >100%, NaN) limits.
        # (lower_exclusive, upper_inclusive)
        bounds = {
            "max_daily_loss": (0.0, 1.0),
            "max_portfolio_risk": (0.0, 1.0),
            "max_position_size": (0.0, 1.0),
            "stop_loss_pct": (0.0, 1.0),
            "take_profit_pct": (0.0, 10.0),
            "risk_pct": (0.0, 1.0),
        }
        for field, (low, high) in bounds.items():
            value = getattr(self, field)
            if value is None and field in ("stop_loss_pct", "take_profit_pct"):
                continue  # explicitly disabled
            if not math.isfinite(value) or not (low < value <= high):
                raise ValueError(
                    f"Invalid risk setting {field}={value}; must be finite and in ({low}, {high}]")
        if self.max_orders_per_minute < 1:
            raise ValueError(f"max_orders_per_minute must be >= 1, got {self.max_orders_per_minute}")
    
    @property
    def symbols_list(self) -> List[str]:
        """Get symbols as a list"""
        if isinstance(self.symbols, str):
            return [s.strip() for s in self.symbols.split(",") if s.strip()]
        return [self.symbol] if self.symbol else ["AAPL"]
    
    @property
    def is_paper_trading(self) -> bool:
        """Check if we're in paper trading mode"""
        return "paper" in self.apca_api_base_url.lower() or self.paper_trading
    
    @property
    def has_alpaca_credentials(self) -> bool:
        """Check if Alpaca credentials are configured"""
        return bool(self.apca_api_key_id and self.apca_api_secret_key)
    
    @property
    def market_tz(self) -> pytz.BaseTzInfo:
        """Get market timezone object"""
        return pytz.timezone(self.market_timezone)
    
    @property
    def system_tz(self) -> pytz.BaseTzInfo:
        """Get system timezone object"""  
        return pytz.timezone(self.system_timezone)

# -------------------------------------------------------------------
# --> ESTA ES LA LÍNEA CRÍTICA QUE PROBABLEMENTE FALTABA <--
# Global settings instance
settings = Settings()
# -------------------------------------------------------------------

# Convenience function for getting settings
def get_settings() -> Settings:
    """Get application settings"""
    return settings