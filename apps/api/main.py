#!/usr/bin/env python3
"""
apps/api/main.py
Enhanced FastAPI Service - System monitoring, control, and Prometheus metrics
Provides REST API for querying system state and sending commands
NEW: Prometheus metrics integration for observability
"""

import os
import sys
import asyncio
import uuid
import subprocess
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any
import logging
import json
from pathlib import Path
from enum import Enum

# Add lib to path
sys.path.append(os.path.join(os.path.dirname(__file__), '../..'))

from fastapi import FastAPI, HTTPException, BackgroundTasks, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from lib.models import Signal, SignalSide, PortfolioState, SystemHealth
from lib.bus import get_bus, connect_bus
from dotenv import load_dotenv

# NEW: Prometheus metrics imports
from prometheus_client import Counter, Histogram, Gauge, Info, generate_latest, CONTENT_TYPE_LATEST
from prometheus_fastapi_instrumentator import Instrumentator, metrics

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# NEW: Global metrics dict (will be initialized in startup)
METRICS = {}
SYSTEM_INFO = None

def initialize_metrics():
    """Initialize Prometheus metrics (called once during startup)"""
    global METRICS, SYSTEM_INFO
    
    # Clear any existing metrics to avoid duplicates
    from prometheus_client import CollectorRegistry, REGISTRY
    
    # Use default registry but check for duplicates
    try:
        METRICS = {
            # System health metrics
            'system_health': Gauge('trading_system_health', 'System component health (1=healthy, 0=unhealthy)', ['component']),
            'redis_latency': Gauge('trading_redis_latency_ms', 'Redis connection latency in milliseconds'),
            'redis_connected_clients': Gauge('trading_redis_clients', 'Redis connected clients'),
            
            # Message bus metrics
            'bus_messages_published': Counter('trading_bus_messages_published_total', 'Messages published to bus', ['type']),
            'bus_messages_consumed': Counter('trading_bus_messages_consumed_total', 'Messages consumed from bus', ['type']),
            'stream_length': Gauge('trading_stream_length', 'Redis stream length', ['stream_name']),
            
            # Trading specific metrics  
            'trading_signals': Counter('trading_signals_generated_total', 'Trading signals generated', ['symbol', 'side', 'source']),
            'trading_orders': Counter('trading_orders_submitted_total', 'Orders submitted', ['symbol', 'side', 'status']),
            'portfolio_value': Gauge('trading_portfolio_value_usd', 'Portfolio total value in USD'),
            'position_count': Gauge('trading_open_positions', 'Number of open positions'),
            
            # Service metrics
            'service_uptime': Gauge('trading_service_uptime_seconds', 'Service uptime', ['service']),
            'custom_errors': Counter('trading_custom_errors_total', 'Custom application errors', ['service', 'error_type']),
        }
        
        # System info
        SYSTEM_INFO = Info('trading_system_info', 'Trading system information')
        
        logger.info("Prometheus metrics initialized successfully")
        return True
        
    except ValueError as e:
        if "Duplicated timeseries" in str(e):
            logger.warning(f"Metrics already exist, skipping initialization: {e}")
            return False
        else:
            raise e

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

# Backtest API Models
class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

class BacktestRequest(BaseModel):
    """Request model for creating a new backtest"""
    symbols: List[str]
    start_date: str  # ISO format
    end_date: Optional[str] = None
    timeframe: str = "1Min"
    feed: str = "iex"
    seed: Optional[int] = None
    speed_multiplier: float = 10.0  # Default to fast backtesting
    strategies: List[str] = ["random_50_50", "smart_technical"]
    initial_cash: float = 100000.0
    risk_params: Optional[Dict[str, Any]] = None

    class Config:
        schema_extra = {
            "example": {
                "symbols": ["AAPL", "GOOGL"],
                "start_date": "2022-01-01",
                "end_date": "2022-01-31",
                "timeframe": "1Min",
                "feed": "iex",
                "seed": 42,
                "speed_multiplier": 10.0,
                "strategies": ["random_50_50"],
                "initial_cash": 50000.0
            }
        }

class BacktestJob(BaseModel):
    """Backtest job model"""
    job_id: str
    status: JobStatus
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    config: BacktestRequest
    results: Optional[Dict[str, Any]] = None
    progress: float = 0.0  # 0.0 to 100.0

    class Config:
        use_enum_values = True

# Global state
bus = None
signal_history = []
system_events = []
startup_time = datetime.utcnow()

# Backtest Job Manager
class JobManager:
    """Manages backtest job lifecycle"""

    def __init__(self):
        self.jobs: Dict[str, BacktestJob] = {}
        self.running_processes: Dict[str, asyncio.subprocess.Process] = {}
        self.max_concurrent_jobs = int(os.getenv("MAX_CONCURRENT_JOBS", "2"))

        # Results directory
        self.results_dir = Path("data/backtest_results")
        self.results_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"JobManager initialized with max {self.max_concurrent_jobs} concurrent jobs")

    def create_job(self, config: BacktestRequest) -> str:
        """Create a new backtest job"""
        job_id = str(uuid.uuid4())

        job = BacktestJob(
            job_id=job_id,
            status=JobStatus.QUEUED,
            created_at=datetime.utcnow(),
            config=config
        )

        self.jobs[job_id] = job

        if METRICS.get('trading_orders'):  # Using existing counter for jobs
            METRICS['trading_orders'].labels(symbol="BACKTEST", side="JOB", status="created").inc()

        logger.info(f"Created backtest job {job_id} for symbols {config.symbols}")
        return job_id

    def get_job(self, job_id: str) -> Optional[BacktestJob]:
        """Get job by ID"""
        return self.jobs.get(job_id)

    def list_jobs(self, status: Optional[JobStatus] = None, limit: int = 50) -> List[BacktestJob]:
        """List jobs with optional filtering"""
        jobs = list(self.jobs.values())

        if status:
            jobs = [j for j in jobs if j.status == status]

        # Sort by creation time (newest first)
        jobs.sort(key=lambda j: j.created_at, reverse=True)

        return jobs[:limit]

    async def start_job(self, job_id: str) -> bool:
        """Start a backtest job"""
        job = self.jobs.get(job_id)
        if not job:
            return False

        if job.status != JobStatus.QUEUED:
            logger.warning(f"Job {job_id} is not queued (status: {job.status})")
            return False

        # Check concurrent job limit
        running_count = len([j for j in self.jobs.values() if j.status == JobStatus.RUNNING])
        if running_count >= self.max_concurrent_jobs:
            logger.warning(f"Cannot start job {job_id}: {running_count} jobs already running")
            return False

        try:
            # Update job status
            job.status = JobStatus.RUNNING
            job.started_at = datetime.utcnow()

            # Build simulator command
            cmd = self._build_simulator_command(job_id, job.config)

            # Start subprocess
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=Path(__file__).parent.parent.parent
            )

            self.running_processes[job_id] = process

            # Monitor process in background
            asyncio.create_task(self._monitor_job(job_id, process))

            logger.info(f"Started backtest job {job_id} with PID {process.pid}")
            return True

        except Exception as e:
            job.status = JobStatus.FAILED
            job.error_message = str(e)
            job.completed_at = datetime.utcnow()

            logger.error(f"Failed to start job {job_id}: {e}")
            return False

    def _build_simulator_command(self, job_id: str, config: BacktestRequest) -> List[str]:
        """Build simulator command line"""
        cmd = [
            "python", "apps/simulator/main.py",
            "--symbols", ",".join(config.symbols),
            "--start", config.start_date,
            "--timeframe", config.timeframe,
            "--feed", config.feed,
            "--speed", str(config.speed_multiplier),
            "--output", str(self.results_dir / f"{job_id}_results.json"),
            "--no-delays"  # Fast backtesting
        ]

        if config.end_date:
            cmd.extend(["--end", config.end_date])

        if config.seed is not None:
            cmd.extend(["--seed", str(config.seed)])

        return cmd

    async def _monitor_job(self, job_id: str, process: asyncio.subprocess.Process):
        """Monitor job execution"""
        job = self.jobs[job_id]

        try:
            # Wait for process to complete
            stdout, stderr = await process.communicate()

            # Update job based on exit code
            if process.returncode == 0:
                job.status = JobStatus.COMPLETED
                job.completed_at = datetime.utcnow()
                job.progress = 100.0

                # Load results if available
                results_file = self.results_dir / f"{job_id}_results.json"
                if results_file.exists():
                    try:
                        with open(results_file) as f:
                            job.results = json.load(f)
                    except Exception as e:
                        logger.warning(f"Failed to load results for job {job_id}: {e}")

                logger.info(f"Job {job_id} completed successfully")

            else:
                job.status = JobStatus.FAILED
                job.completed_at = datetime.utcnow()
                job.error_message = stderr.decode() if stderr else "Unknown error"
                job.progress = 100.0

                logger.error(f"Job {job_id} failed with code {process.returncode}")

        except Exception as e:
            job.status = JobStatus.FAILED
            job.completed_at = datetime.utcnow()
            job.error_message = str(e)
            job.progress = 100.0

            logger.error(f"Error monitoring job {job_id}: {e}")

        finally:
            # Cleanup
            if job_id in self.running_processes:
                del self.running_processes[job_id]

    async def cancel_job(self, job_id: str) -> bool:
        """Cancel a running job"""
        job = self.jobs.get(job_id)
        if not job:
            return False

        if job.status != JobStatus.RUNNING:
            return False

        process = self.running_processes.get(job_id)
        if process:
            try:
                process.terminate()
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()

            job.status = JobStatus.CANCELLED
            job.completed_at = datetime.utcnow()
            job.progress = 100.0

            logger.info(f"Cancelled job {job_id}")
            return True

        return False

# Global job manager
job_manager = JobManager()

# NEW: WebSocket connection manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def send_personal_message(self, message: str, websocket: WebSocket):
        await websocket.send_text(message)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except:
                # Remove dead connections
                self.active_connections.remove(connection)

manager = ConnectionManager()

# NEW: Initialize Prometheus instrumentation
instrumentator = Instrumentator()

async def startup():
    """Initialize API service with metrics"""
    global bus
    
    logger.info("Starting Enhanced Trading Platform API with Prometheus metrics...")
    
    # Initialize metrics first
    initialize_metrics()
    
    # Set system info
    if SYSTEM_INFO:
        SYSTEM_INFO.info({
            'version': '1.1.0',
            'mode': 'paper_trading',
            'features': 'redis_streams,prometheus_metrics,real_time_monitoring'
        })
    
    # Connect to message bus
    if not connect_bus():
        logger.error("Failed to connect to Redis")
        if METRICS.get('system_health'):
            METRICS['system_health'].labels(component='redis').set(0)
        raise Exception("Cannot start API without Redis connection")
    
    bus = get_bus()
    if METRICS.get('system_health'):
        METRICS['system_health'].labels(component='redis').set(1)
    
    # Start background tasks
    asyncio.create_task(monitor_system_events())
    asyncio.create_task(monitor_signals())
    asyncio.create_task(update_system_metrics())
    
    logger.info("Enhanced API service started successfully with metrics endpoint at /metrics")

async def shutdown():
    """Cleanup on shutdown"""
    if bus:
        bus.disconnect()
    logger.info("API service stopped")

# FastAPI app with lifespan
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await startup()
    yield
    # Shutdown
    await shutdown()

app = FastAPI(
    title="Enhanced Algorithmic Trading Platform API",
    description="REST API for monitoring, controlling, and observing the trading system",
    version="1.1.0",
    lifespan=lifespan
)

# Add Prometheus instrumentation
instrumentator.instrument(app).expose(app, endpoint="/metrics")

# NEW: Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# NEW: Background task to update system metrics
async def update_system_metrics():
    """Update Prometheus metrics periodically"""
    while True:
        try:
            if bus:
                # Update Redis metrics
                health = bus.health_check()
                if health.get('status') == 'healthy':
                    if METRICS.get('system_health'):
                        METRICS['system_health'].labels(component='redis').set(1)
                    if METRICS.get('redis_latency'):
                        METRICS['redis_latency'].set(health.get('latency_ms', 0))
                    if METRICS.get('redis_connected_clients'):
                        METRICS['redis_connected_clients'].set(health.get('connected_clients', 0))
                else:
                    if METRICS.get('system_health'):
                        METRICS['system_health'].labels(component='redis').set(0)
                
                # Update message bus metrics
                stats = bus.get_stats()
                if 'messages_published' in stats:
                    # Note: These are cumulative counters
                    pass
                
                # Update stream lengths if using streams
                if stats.get('mode') == 'streams' and 'streams' in stats and METRICS.get('stream_length'):
                    for stream_type, stream_info in stats['streams'].items():
                        METRICS['stream_length'].labels(stream_name=stream_info['name']).set(stream_info['length'])
            
            # Update service uptime
            uptime = (datetime.utcnow() - startup_time).total_seconds()
            if METRICS.get('service_uptime'):
                METRICS['service_uptime'].labels(service='api').set(uptime)
            
            await asyncio.sleep(30)  # Update every 30 seconds
            
        except Exception as e:
            logger.error(f"Error updating metrics: {e}")
            if METRICS.get('custom_errors'):
                METRICS['custom_errors'].labels(service='api', error_type='metrics_update').inc()
            await asyncio.sleep(30)

# NEW: Dashboard and WebSocket endpoints
@app.get("/", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """Serve the main trading system UI"""
    return FileResponse("static/index.html")

@app.websocket("/ws/dashboard")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time dashboard updates"""
    await manager.connect(websocket)
    try:
        # Send initial data
        await send_initial_dashboard_data(websocket)
        
        # Keep connection alive and send periodic updates
        while True:
            # Send system status every 5 seconds
            await asyncio.sleep(5)
            await send_dashboard_update(websocket)
            
    except WebSocketDisconnect:
        manager.disconnect(websocket)

async def send_initial_dashboard_data(websocket: WebSocket):
    """Send initial data to newly connected dashboard"""
    try:
        # System status
        status_data = await get_system_status_data()
        await websocket.send_text(json.dumps({
            "type": "system_status",
            "data": status_data
        }))
        
        # Portfolio data
        portfolio_data = await get_portfolio_data()
        await websocket.send_text(json.dumps({
            "type": "portfolio_update", 
            "data": portfolio_data
        }))
        
        # Recent signals
        for signal in signal_history[-10:]:  # Last 10 signals
            await websocket.send_text(json.dumps({
                "type": "new_signal",
                "data": signal
            }))
            
    except Exception as e:
        logger.error(f"Error sending initial dashboard data: {e}")

async def send_dashboard_update(websocket: WebSocket):
    """Send periodic updates to dashboard"""
    try:
        # System status update
        status_data = await get_system_status_data()
        await websocket.send_text(json.dumps({
            "type": "system_status",
            "data": status_data
        }))
        
        # Metrics update
        metrics_data = {
            "orders_today": 15,  # Simulated
            "success_rate": 0.8,  # Simulated
            "messages_published": bus.messages_published if bus else 0,
            "messages_consumed": bus.messages_consumed if bus else 0
        }
        await websocket.send_text(json.dumps({
            "type": "metrics_update",
            "data": metrics_data
        }))
        
    except Exception as e:
        logger.error(f"Error sending dashboard update: {e}")

async def get_system_status_data():
    """Get system status data for dashboard"""
    try:
        redis_health = bus.health_check() if bus else {"status": "disconnected"}
        bus_stats = bus.get_stats() if bus else {"error": "not_connected"}
        
        return {
            "services": {
                "data_ingestor": "running",
                "strategies": "running", 
                "risk_manager": "running",
                "executor": "running",
                "api": "running"
            },
            "redis_status": redis_health,
            "total_symbols": len([ch for ch in bus_stats.get("channels", {}) if ch.startswith("bars.")]),
            "active_strategies": ["random_50_50", "smart_technical"]
        }
    except Exception as e:
        logger.error(f"Error getting system status: {e}")
        return {"error": str(e)}

async def get_portfolio_data():
    """Get portfolio data for dashboard"""
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
        "total_pnl": 275.0
    }

# Health and Status Endpoints
@app.get("/health")
async def health_check():
    """Basic health check with metrics"""
    health_status = {"status": "healthy", "timestamp": datetime.utcnow()}
    
    # Update health metric
    if METRICS.get('system_health'):
        METRICS['system_health'].labels(component='api').set(1)
    
    return health_status

@app.get("/status", response_model=SystemStatusResponse)
async def get_system_status():
    """Get comprehensive system status with metrics"""
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
        
        # Update service health metrics
        if METRICS.get('system_health'):
            for service, status in services.items():
                METRICS['system_health'].labels(component=service).set(1 if status == "running" else 0)
        
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
        if METRICS.get('custom_errors'):
            METRICS['custom_errors'].labels(service='api', error_type='status_check').inc()
        raise HTTPException(status_code=500, detail=str(e))

# Signal Management
@app.post("/signals/manual")
async def create_manual_signal(signal_request: ManualSignalRequest):
    """Create manual trading signal with metrics"""
    try:
        if not bus:
            if METRICS.get('custom_errors'):
                METRICS['custom_errors'].labels(service='api', error_type='no_bus').inc()
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
        
        # Update metrics
        if METRICS.get('trading_signals'):
            METRICS['trading_signals'].labels(
                symbol=signal.symbol,
                side=signal.side.value,
                source=signal.source
            ).inc()
        
        logger.info(f"Manual signal created: {signal.side} {signal.symbol}")
        
        return {
            "status": "success",
            "signal_id": f"{signal.symbol}_{signal.timestamp.isoformat()}",
            "message": f"Signal published for {signal.symbol}"
        }
        
    except Exception as e:
        logger.error(f"Error creating manual signal: {e}")
        if METRICS.get('custom_errors'):
            METRICS['custom_errors'].labels(service='api', error_type='signal_creation').inc()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/signals/history")
async def get_signal_history(
    symbol: Optional[str] = None,
    hours: int = 24,
    limit: int = 100
):
    """Get signal history with metrics"""
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
        if METRICS.get('custom_errors'):
            METRICS['custom_errors'].labels(service='api', error_type='signal_history').inc()
        raise HTTPException(status_code=500, detail=str(e))

# Portfolio and Positions
@app.get("/portfolio")
async def get_portfolio():
    """Get current portfolio state with metrics"""
    try:
        # In real implementation, this would query the executor or database
        # For now, return simulated data and update metrics
        
        portfolio_data = {
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
        
        # Update portfolio metrics
        if METRICS.get('portfolio_value'):
            METRICS['portfolio_value'].set(portfolio_data["total_value"])
        if METRICS.get('position_count'):
            METRICS['position_count'].set(len(portfolio_data["positions"]))
        
        return portfolio_data
        
    except Exception as e:
        logger.error(f"Error getting portfolio: {e}")
        if METRICS.get('custom_errors'):
            METRICS['custom_errors'].labels(service='api', error_type='portfolio').inc()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/positions/{symbol}")
async def get_position(symbol: str):
    """Get position for specific symbol with metrics"""
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
        if METRICS.get('custom_errors'):
            METRICS['custom_errors'].labels(service='api', error_type='position_lookup').inc()
        raise HTTPException(status_code=500, detail=str(e))

# System Control
@app.post("/system/restart/{service}")
async def restart_service(service: str):
    """Restart a specific service with metrics"""
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
        METRICS['custom_errors'].labels(service='api', error_type='service_restart').inc()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/system/emergency_stop")
async def emergency_stop():
    """Emergency stop all trading with metrics"""
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
        METRICS['custom_errors'].labels(service='api', error_type='emergency_stop').inc()
        raise HTTPException(status_code=500, detail=str(e))

# Monitoring and Logs
@app.get("/events")
async def get_system_events(hours: int = 1, limit: int = 50):
    """Get recent system events with metrics"""
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
        METRICS['custom_errors'].labels(service='api', error_type='events').inc()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/metrics-summary")
async def get_metrics_summary():
    """Get system performance metrics summary"""
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
                "uptime_hours": 24.5,
                "redis_streams_active": bus.supports_streams if bus else False
            },
            "timestamp": datetime.utcnow()
        }
        
    except Exception as e:
        logger.error(f"Error getting metrics: {e}")
        METRICS['custom_errors'].labels(service='api', error_type='metrics_summary').inc()
        raise HTTPException(status_code=500, detail=str(e))

# Background Tasks
async def monitor_system_events():
    """Monitor system events in background with metrics"""
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
        if METRICS.get('custom_errors'):
            METRICS['custom_errors'].labels(service='api', error_type='event_monitoring').inc()

async def monitor_signals():
    """Monitor trading signals in background with metrics and WebSocket updates"""
    try:
        if not bus:
            return
        
        async for signal in bus.subscribe_signals():
            # Store signal in memory with JSON-safe conversion
            signal_data = {
                "timestamp": signal.timestamp.isoformat(),
                "symbol": signal.symbol,
                "side": signal.side.value,  # Convert enum to string
                "confidence": float(signal.confidence),  # Convert Decimal to float
                "price": float(signal.price) if signal.price else None,  # Convert Decimal to float
                "source": signal.source,
                "metadata": signal.metadata
            }
            
            signal_history.append(signal_data)
            
            # Keep only last 1000 signals
            if len(signal_history) > 1000:
                signal_history.pop(0)
            
            # Update signal metrics
            if METRICS.get('trading_signals'):
                METRICS['trading_signals'].labels(
                    symbol=signal.symbol,
                    side=signal.side.value,
                    source=signal.source
                ).inc()
            
            # NEW: Send real-time update to dashboard (with JSON-safe data)
            try:
                await manager.broadcast(json.dumps({
                    "type": "new_signal",
                    "data": signal_data
                }))
            except Exception as broadcast_error:
                logger.error(f"Error broadcasting signal: {broadcast_error}")
            
            logger.debug(f"Recorded signal: {signal.side} {signal.symbol} from {signal.source}")
            
    except Exception as e:
        logger.error(f"Error monitoring signals: {e}")
        if METRICS.get('custom_errors'):
            METRICS['custom_errors'].labels(service='api', error_type='signal_monitoring').inc()

# Backtest API Endpoints
@app.post("/backtest/jobs", response_model=Dict[str, str])
async def create_backtest_job(
    request: BacktestRequest,
    background_tasks: BackgroundTasks,
    auto_start: bool = False
):
    """Create a new backtest job"""
    try:
        job_id = job_manager.create_job(request)

        if auto_start:
            # Try to start immediately
            background_tasks.add_task(job_manager.start_job, job_id)

        return {"job_id": job_id, "status": "created"}

    except Exception as e:
        logger.error(f"Failed to create backtest: {e}")
        if METRICS.get('custom_errors'):
            METRICS['custom_errors'].labels(service='api', error_type='backtest_creation').inc()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/backtest/jobs", response_model=List[BacktestJob])
async def list_backtest_jobs(
    status: Optional[JobStatus] = None,
    limit: int = 50
):
    """List backtest jobs"""
    try:
        jobs = job_manager.list_jobs(status=status, limit=limit)
        return jobs
    except Exception as e:
        logger.error(f"Failed to list backtest jobs: {e}")
        if METRICS.get('custom_errors'):
            METRICS['custom_errors'].labels(service='api', error_type='backtest_list').inc()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/backtest/jobs/{job_id}", response_model=BacktestJob)
async def get_backtest_job(job_id: str):
    """Get backtest job status and details"""
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job

@app.post("/backtest/jobs/{job_id}/start")
async def start_backtest_job(job_id: str):
    """Start a queued backtest job"""
    if not job_manager.get_job(job_id):
        raise HTTPException(status_code=404, detail="Job not found")

    success = await job_manager.start_job(job_id)
    if not success:
        raise HTTPException(status_code=400, detail="Cannot start job")

    return {"status": "started"}

@app.post("/backtest/jobs/{job_id}/cancel")
async def cancel_backtest_job(job_id: str):
    """Cancel a running backtest job"""
    if not job_manager.get_job(job_id):
        raise HTTPException(status_code=404, detail="Job not found")

    success = await job_manager.cancel_job(job_id)
    if not success:
        raise HTTPException(status_code=400, detail="Cannot cancel job")

    return {"status": "cancelled"}

@app.get("/backtest/jobs/{job_id}/results")
async def get_backtest_results(job_id: str):
    """Get backtest job results"""
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status != JobStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Job not completed")

    if not job.results:
        raise HTTPException(status_code=404, detail="Results not available")

    return job.results

@app.get("/backtest/jobs/{job_id}/download")
async def download_backtest_results(job_id: str):
    """Download backtest results as JSON file"""
    from fastapi.responses import FileResponse

    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    results_file = job_manager.results_dir / f"{job_id}_results.json"
    if not results_file.exists():
        raise HTTPException(status_code=404, detail="Results file not found")

    return FileResponse(
        path=str(results_file),
        filename=f"backtest_{job_id}_results.json",
        media_type="application/json"
    )

@app.get("/backtest/stats")
async def get_backtest_stats():
    """Get backtest system statistics"""
    jobs = job_manager.jobs.values()

    stats = {
        "total_jobs": len(jobs),
        "status_counts": {},
        "running_jobs": len([j for j in jobs if j.status == JobStatus.RUNNING]),
        "max_concurrent": job_manager.max_concurrent_jobs,
        "results_directory": str(job_manager.results_dir)
    }

    # Count by status
    for status in JobStatus:
        stats["status_counts"][status.value] = len([j for j in jobs if j.status == status])

    return stats

@app.post("/backtest/quick")
async def quick_backtest(
    symbols: str = "AAPL,GOOGL",
    days: int = 30,
    seed: Optional[int] = None
):
    """Create and start a quick backtest for testing"""
    try:
        from datetime import date

        end_date = date.today()
        start_date = end_date - timedelta(days=days)

        config = BacktestRequest(
            symbols=symbols.split(","),
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            timeframe="1Day",
            seed=seed,
            speed_multiplier=1.0,  # Fast execution
            strategies=["random_50_50"]
        )

        job_id = job_manager.create_job(config)

        # Start immediately
        success = await job_manager.start_job(job_id)

        return {
            "job_id": job_id,
            "status": "started" if success else "failed_to_start",
            "config": {
                "symbols": config.symbols,
                "date_range": f"{start_date} to {end_date}",
                "seed": seed
            }
        }

    except Exception as e:
        logger.error(f"Failed to create quick backtest: {e}")
        if METRICS.get('custom_errors'):
            METRICS['custom_errors'].labels(service='api', error_type='quick_backtest').inc()
        raise HTTPException(status_code=500, detail=str(e))

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