#!/usr/bin/env python3
"""
apps/api/main.py
FastAPI Service - System monitoring and control
Provides REST API for querying system state and sending commands
"""

import os
import sys
import asyncio
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import logging

# Add lib to path
sys.path.append(os.path.join(os.path.dirname(__file__), '../..'))

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from lib.models import Signal, SignalSide, PortfolioState, SystemHealth
from lib.bus import get_bus, connect_bus
from dotenv import load_dotenv

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# API Models
class ManualSignalRequest(BaseModel):
    symbol: str
    side: SignalSide
    confidence: float = 0.8
    price: Optional[float] = None
    source: str = "manual_api"

class SystemStatusResponse(BaseModel):
    timestamp: datetime
    services: Dict[str, str]
    redis_status: Dict
    total_symbols: int
    active_strategies: List[str]

class SignalHistoryResponse(BaseModel):
    signals: List[Dict]
    total_count: int
    time_range: str

# FastAPI app
app = FastAPI(
    title="Algorithmic Trading Platform API",
    description="REST API for monitoring and controlling the trading system",
    version="1.0.0"
)

# Global state
bus = None
signal_history = []
system_events = []

@app.on_event("startup")
async def startup_event():
    """Initialize API service"""
    global bus
    
    logger.info("Starting Trading Platform API...")
    
    # Connect to message bus
    if not connect_bus():
        logger.error("Failed to connect to Redis")
        raise Exception("Cannot start API without Redis connection")
    
    bus = get_bus()
    
    # Start background tasks
    asyncio.create_task(monitor_system_events())
    asyncio.create_task(monitor_signals())
    
    logger.info("API service started successfully")

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    if bus:
        bus.disconnect()
    logger.info("API service stopped")

# Health and Status Endpoints
@app.get("/health")
async def health_check():
    """Basic health check"""
    return {"status": "healthy", "timestamp": datetime.utcnow()}

@app.get("/status", response_model=SystemStatusResponse)
async def get_system_status():
    """Get comprehensive system status"""
    try:
        # Check Redis health
        redis_health = bus.health_check() if bus else {"status": "disconnected"}
        
        # Get message bus stats
        bus_stats = bus.get_stats() if bus else {"error": "not_connected"}
        
        # Simulate service status (in real implementation, track via events)
        services = {
            "data_ingestor": "running",
            "strategies": "running", 
            "risk_manager": "running",
            "executor": "running",
            "api": "running"
        }
        
        # Count active channels/symbols
        total_symbols = len([ch for ch in bus_stats.get("channels", {}) if ch.startswith("bars.")])
        
        return SystemStatusResponse(
            timestamp=datetime.utcnow(),
            services=services,
            redis_status=redis_health,
            total_symbols=total_symbols,
            active_strategies=["random_50_50", "smart_technical"]
        )
        
    except Exception as e:
        logger.error(f"Error getting system status: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Signal Management
@app.post("/signals/manual")
async def create_manual_signal(signal_request: ManualSignalRequest):
    """Create manual trading signal"""
    try:
        if not bus:
            raise HTTPException(status_code=503, detail="Message bus not connected")
        
        # Create signal
        signal = Signal(
            symbol=signal_request.symbol,
            timestamp=datetime.utcnow(),
            side=signal_request.side,
            confidence=signal_request.confidence,
            price=signal_request.price,
            source=signal_request.source,
            metadata={"manual": True, "api_user": "admin"}
        )
        
        # Publish signal
        bus.publish_signal(signal)
        
        logger.info(f"Manual signal created: {signal.side} {signal.symbol}")
        
        return {
            "status": "success",
            "signal_id": f"{signal.symbol}_{signal.timestamp.isoformat()}",
            "message": f"Signal published for {signal.symbol}"
        }
        
    except Exception as e:
        logger.error(f"Error creating manual signal: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/signals/history")
async def get_signal_history(
    symbol: Optional[str] = None,
    hours: int = 24,
    limit: int = 100
):
    """Get signal history"""
    try:
        # Filter signals
        cutoff_time = datetime.utcnow() - timedelta(hours=hours)
        
        filtered_signals = [
            s for s in signal_history
            if s.get("timestamp", "") > cutoff_time.isoformat()
        ]
        
        if symbol:
            filtered_signals = [
                s for s in filtered_signals
                if s.get("symbol") == symbol
            ]
        
        # Apply limit
        filtered_signals = filtered_signals[-limit:]
        
        return SignalHistoryResponse(
            signals=filtered_signals,
            total_count=len(filtered_signals),
            time_range=f"last_{hours}_hours"
        )
        
    except Exception as e:
        logger.error(f"Error getting signal history: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Portfolio and Positions
@app.get("/portfolio")
async def get_portfolio():
    """Get current portfolio state"""
    try:
        # In real implementation, this would query the executor or database
        # For now, return simulated data
        
        return {
            "total_value": 100000.0,
            "cash": 85000.0,
            "buying_power": 170000.0,
            "positions": [
                {
                    "symbol": "AAPL",
                    "quantity": 50,
                    "avg_cost": 245.50,
                    "market_value": 12275.0,
                    "unrealized_pnl": 275.0
                }
            ],
            "total_pnl": 275.0,
            "last_updated": datetime.utcnow()
        }
        
    except Exception as e:
        logger.error(f"Error getting portfolio: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/positions/{symbol}")
async def get_position(symbol: str):
    """Get position for specific symbol"""
    try:
        # Simulate position lookup
        if symbol == "AAPL":
            return {
                "symbol": symbol,
                "quantity": 50,
                "avg_cost": 245.50,
                "market_value": 12275.0,
                "unrealized_pnl": 275.0,
                "last_updated": datetime.utcnow()
            }
        else:
            return {
                "symbol": symbol,
                "quantity": 0,
                "message": "No position found"
            }
            
    except Exception as e:
        logger.error(f"Error getting position for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# System Control
@app.post("/system/restart/{service}")
async def restart_service(service: str):
    """Restart a specific service"""
    try:
        if not bus:
            raise HTTPException(status_code=503, detail="Message bus not connected")
        
        valid_services = ["data_ingestor", "strategies", "risk_manager", "executor"]
        
        if service not in valid_services:
            raise HTTPException(status_code=400, detail=f"Invalid service: {service}")
        
        # Publish restart command
        bus.publish_system_event(
            event_type="restart_command",
            source="api",
            data={"service": service, "requested_by": "admin"}
        )
        
        return {
            "status": "success",
            "message": f"Restart command sent to {service}",
            "timestamp": datetime.utcnow()
        }
        
    except Exception as e:
        logger.error(f"Error restarting service {service}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/system/emergency_stop")
async def emergency_stop():
    """Emergency stop all trading"""
    try:
        if not bus:
            raise HTTPException(status_code=503, detail="Message bus not connected")
        
        # Publish emergency stop
        bus.publish_system_event(
            event_type="emergency_stop",
            source="api",
            data={"reason": "manual_emergency_stop", "timestamp": datetime.utcnow()}
        )
        
        logger.warning("Emergency stop activated via API")
        
        return {
            "status": "success",
            "message": "Emergency stop activated",
            "timestamp": datetime.utcnow()
        }
        
    except Exception as e:
        logger.error(f"Error during emergency stop: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Monitoring and Logs
@app.get("/events")
async def get_system_events(hours: int = 1, limit: int = 50):
    """Get recent system events"""
    try:
        cutoff_time = datetime.utcnow() - timedelta(hours=hours)
        
        recent_events = [
            event for event in system_events
            if event.get("timestamp", "") > cutoff_time.isoformat()
        ]
        
        return {
            "events": recent_events[-limit:],
            "total_count": len(recent_events),
            "time_range": f"last_{hours}_hours"
        }
        
    except Exception as e:
        logger.error(f"Error getting system events: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/metrics")
async def get_metrics():
    """Get system performance metrics"""
    try:
        # In real implementation, query from database or metrics store
        return {
            "trading_metrics": {
                "total_trades_today": 15,
                "successful_trades": 12,
                "failed_trades": 3,
                "total_pnl_today": 275.50,
                "win_rate": 0.8
            },
            "system_metrics": {
                "signals_processed": 150,
                "orders_executed": 15,
                "average_latency_ms": 45.2,
                "uptime_hours": 24.5
            },
            "timestamp": datetime.utcnow()
        }
        
    except Exception as e:
        logger.error(f"Error getting metrics: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Background Tasks
async def monitor_system_events():
    """Monitor system events in background"""
    try:
        if not bus:
            return
        
        async for event in bus.subscribe_system_events():
            # Store event in memory (in production, store in database)
            event_data = {
                "timestamp": event.timestamp.isoformat(),
                "event_type": event.event_type,
                "source": event.source,
                "data": event.data
            }
            
            system_events.append(event_data)
            
            # Keep only last 1000 events
            if len(system_events) > 1000:
                system_events.pop(0)
            
            logger.debug(f"Recorded system event: {event.event_type} from {event.source}")
            
    except Exception as e:
        logger.error(f"Error monitoring system events: {e}")

async def monitor_signals():
    """Monitor trading signals in background"""
    try:
        if not bus:
            return
        
        async for signal in bus.subscribe_signals():
            # Store signal in memory
            signal_data = {
                "timestamp": signal.timestamp.isoformat(),
                "symbol": signal.symbol,
                "side": signal.side,
                "confidence": signal.confidence,
                "price": signal.price,
                "source": signal.source,
                "metadata": signal.metadata
            }
            
            signal_history.append(signal_data)
            
            # Keep only last 1000 signals
            if len(signal_history) > 1000:
                signal_history.pop(0)
            
            logger.debug(f"Recorded signal: {signal.side} {signal.symbol} from {signal.source}")
            
    except Exception as e:
        logger.error(f"Error monitoring signals: {e}")

if __name__ == "__main__":
    import uvicorn
    
    port = int(os.getenv("API_PORT", "8000"))
    host = os.getenv("API_HOST", "0.0.0.0")
    
    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=True,
        log_level="info"
    )