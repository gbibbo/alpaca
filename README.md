# Algorithmic Trading Platform

A comprehensive, microservices-based algorithmic trading platform built with Python, featuring real-time market data ingestion, historical data simulation, intelligent trading strategies, robust risk management, rapid backtesting capabilities, and enterprise-grade monitoring and observability.

## 🆕 Recent Improvements

- **✨ Redis Streams Backend**: Primary message delivery system with consumer groups and automatic recovery
- **📈 Prometheus Integration**: Comprehensive metrics collection on dedicated port 8013
- **🔄 Intelligent Fallbacks**: Seamless fallback chain (Streams → Pub/Sub → FakeRedis)
- **🕰️ Redis 6.0 Support**: Compatible with Redis 6.0+ with feature detection
- **📊 Enhanced Backtesting**: Realistic modeling with slippage, costs, and visual results
- **🚑 Smoke Testing**: Quick 3-terminal setup verification with GOOGL example
- **🔍 Business Metrics**: Signal approval rates, risk violations, and portfolio tracking

## 🏗️ Architecture

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Grafana       │    │   Prometheus    │    │    Redis        │
│   (Dashboard)   │◄───┤   (Metrics)     │◄───┤   (Message Bus) │
│   Port: 3000    │    │   Port: 9090    │    │   Port: 6379    │
└─────────────────┘    └─────────────────┘    └─────────────────┘
                                                       ▲
                                                       │
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│     API         │    │  Risk Manager   │    │   Data Ingestor │
│  (Monitoring)   │◄───┤  (Validation)   │◄───┤   (Alpaca IEX)  │
│  Port: 8000     │    │                 │    │                 │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         ▲                       ▲                       ▲
         │                       │                       │
         └───────────────────────┼───────────────────────┘
                                 │
                    ┌─────────────────┐    ┌─────────────────┐
                    │   Strategies    │    │    Executor     │
                    │   (Signals)     │    │ (Order Management)│
                    │                 │    │                 │
                    └─────────────────┘    └─────────────────┘
                                 │
                    ┌─────────────────┐    ┌─────────────────┐
                    │   Simulator     │    │   Backtester    │
                    │ (Historical)    │    │ (Rapid Testing) │
                    │                 │    │                 │
                    └─────────────────┘    └─────────────────┘
```

The platform implements a distributed architecture where each component communicates through a Redis-based message bus using Redis Streams for reliable message delivery with automatic fallback to Pub/Sub for compatibility. The system processes real-time market data, simulates historical trading scenarios, generates trading signals using multiple strategies, validates them through comprehensive risk management, and executes orders through the Alpaca broker API.

## 🚀 Core Features

### Market Data Pipeline
- **Real-time Data Ingestion**: Connects to Alpaca Markets API for live and historical market data
- **Historical Data Simulation**: Replays historical market data through the message bus at configurable speeds
- **IEX Feed Support**: Uses Alpaca's IEX data feed, compatible with free paper trading accounts
- **CSV Data Support**: Loads and processes historical data from CSV files for offline testing
- **Redis Streams Integration**: Primary message backend using Redis Streams with consumer groups for guaranteed delivery
- **Automatic Fallbacks**: Intelligent fallback chain: Redis Streams → Redis Pub/Sub → FakeRedis
- **Redis 6.0+ Support**: Compatible with Redis 6.0+ with automatic feature detection

### Trading Strategy Engine
- **Multiple Strategy Support**: Implements both random and technical analysis-based trading strategies
- **Signal Generation**: Produces buy/sell signals with confidence scores and metadata
- **Technical Indicators**: Includes SMA, RSI, MACD, and other technical analysis indicators
- **Configurable Parameters**: Strategies can be tuned through configuration files

### Risk Management System
- **Multi-layer Validation**: Comprehensive signal validation including market hours, position limits, and confidence thresholds
- **Rate Limiting**: Intelligent rate limiting using monotonic time to prevent over-trading
- **Circuit Breakers**: Automatic fault isolation and recovery mechanisms
- **Position Sizing**: Dynamic position sizing based on risk parameters and portfolio value
- **Emergency Controls**: Manual emergency stop functionality

### Order Execution
- **Alpaca Integration**: Direct integration with Alpaca Markets for order execution
- **Retry Logic**: Exponential backoff retry logic for API calls with intelligent error handling
- **Partial Fill Management**: Comprehensive tracking and management of partial order fills
- **Order Validation**: Pre-execution validation including position checks and risk limits

### Backtesting & Simulation
- **Historical Simulator**: End-to-end backtesting by replaying historical data through the complete trading pipeline
- **Rapid Backtester**: Off-bus quick strategy validation with synthetic or real market data
- **Performance Metrics**: Comprehensive backtesting metrics including Sharpe ratio, maximum drawdown, and win rates
- **Data Flexibility**: Supports both Alpaca API data and CSV file inputs for testing
- **Configurable Speed**: Variable replay speeds for efficient historical simulation
- **Visual Results**: Automatic chart generation with performance plots saved to `out/` directory
- **Realistic Trading**: Includes notional values, slippage modeling, and transaction costs

### Monitoring & Observability
- **Prometheus Metrics**: Comprehensive system and business metrics collection on port 8013
- **Grafana Dashboards**: Real-time visualization and alerting capabilities
- **Health Monitoring**: Automated health checks for all services with detailed status reporting
- **Service Logging**: Individual log files for each service component
- **Real-time Dashboard**: WebSocket-based live trading dashboard
- **Message Bus Metrics**: Stream length, consumer lag, pending messages, and throughput monitoring
- **Business Metrics**: Signal generation, order execution, portfolio value, and P&L tracking

### Service Management
- **Control System**: Comprehensive service management through `scripts/control.py`
- **Health Checking**: Strict health validation with exponential backoff
- **Process Management**: PID tracking and graceful shutdown for all services
- **Port Management**: Intelligent port cleanup to prevent conflicts
- **Dependency Validation**: Automatic checking of service dependencies

## 📋 Prerequisites

- Python 3.9+
- Redis Server 6.0+ (optional - automatic FakeRedis fallback available)
- Alpaca Markets Account (Paper Trading) - optional for CSV-based testing
- `lsof` utility (recommended)
- 8GB RAM minimum
- Linux/macOS/WSL2/Windows

### Redis Compatibility
- **Redis 6.2+**: Full feature support including `XAUTOCLAIM` for automatic message recovery
- **Redis 6.0-6.1**: Compatible with manual consumer group management (automatic recovery disabled)
- **No Redis**: Automatic FakeRedis fallback for development

## 🛠️ Installation

### 1. Clone Repository
```bash
git clone <repository-url>
cd algorithmic-trading-platform
```

### 2. Environment Setup
```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/macOS
# or
venv\Scripts\activate     # Windows

# Install dependencies
pip install -r requirements.txt
```

### 3. Configuration
```bash
# Copy environment template
cp .env.template .env

# Edit with your Alpaca credentials (optional for CSV testing)
nano .env
```

Required environment variables (for live data):
```bash
# Alpaca API Configuration (optional)
APCA_API_BASE_URL=https://paper-api.alpaca.markets
APCA_API_KEY_ID=your_key_here
APCA_API_SECRET_KEY=your_secret_here

# Data Feed Configuration
ALPACA_DATA_FEED=iex  # Use 'iex' for free accounts, 'sip' for premium

# Trading Configuration
SYMBOLS=AAPL,MSFT,GOOGL,TSLA,NVDA
HISTORICAL_DAYS=7
RISK_PCT=0.02

# Message Bus Configuration
BUS_BACKEND=streams        # 'streams' (preferred) or 'pubsub'
REDIS_URL=redis://127.0.0.1:6379
REDIS_DB=0
BUS_GROUP=trader          # Consumer group name for streams
USE_FAKE_REDIS=0          # Set to 1 to force FakeRedis

# Metrics Configuration
RISK_METRICS_PORT=8013    # Prometheus metrics port
```

### 4. Infrastructure Setup
```bash
# Automated setup (installs Prometheus and Grafana)
chmod +x scripts/setup.sh
./scripts/setup.sh

# Manual setup
python scripts/setup_infrastructure.py
```

## 🚀 Quick Start

### Smoke Test (Recommended First Step)

Verify your system setup with this simple 3-terminal test:

**Terminal A - Risk Manager:**
```bash
export BUS_BACKEND=streams REDIS_URL=redis://127.0.0.1:6379 REDIS_DB=0 BUS_GROUP=trader RISK_METRICS_PORT=8013
python apps/risk_manager/main.py
```

**Terminal B - Executor:**
```bash
export BUS_BACKEND=streams REDIS_URL=redis://127.0.0.1:6379 REDIS_DB=0 BUS_GROUP=trader
python apps/executor/main.py
```

**Terminal C - Publish Test Signal:**
```bash
export BUS_BACKEND=streams REDIS_URL=redis://127.0.0.1:6379 REDIS_DB=0
python -c "
from lib.bus import connect_bus, get_bus
from lib.models import Signal, SignalSide
from decimal import Decimal
connect_bus(); bus = get_bus()
sig = Signal(symbol='GOOGL', side=SignalSide.BUY, confidence=Decimal('0.9'), price=Decimal('151.00'), source='smart_technical')
bus.publish_signal(sig)
print('Signal published')
"
```

**Expected Output:**
- Risk Manager: `✅ Signal approved and order created: GOOGL BUY confidence=0.9`
- Executor: `Received order intent: GOOGL BUY qty=65 notional=9965.00`
- Prometheus metrics: http://127.0.0.1:8013/metrics
- Stream status: `Backend: streams`, `RedisStreamsBus health: OK`

### Redis 6.0 Troubleshooting

If you encounter issues with consumer groups on Redis 6.0:
```bash
# Reset consumer group (if needed)
redis-cli XGROUP DESTROY orders.intent order_processors
redis-cli XGROUP CREATE orders.intent order_processors "$" MKSTREAM
```

### Rapid Strategy Testing

Test trading strategies quickly without full system setup:

```bash
# Quick backtest with real Alpaca data (requires credentials)
python scripts/sim_random.py \
  --symbol GOOGL \
  --start 2022-01-01 \
  --end 2024-01-01 \
  --initial-cash 100000 \
  --position-notional 10000 \
  --signal-prob 0.02 \
  --seed 42

# Backtest with realistic slippage and costs
python scripts/sim_random.py \
  --symbol GOOGL \
  --start 2023-01-01 \
  --end 2024-01-01 \
  --initial-cash 100000 \
  --position-notional 10000 \
  --signal-prob 0.05 \
  --slippage-bps 3 \
  --plot out/GOOGL.png
```

**Features:**
- **Visual Results**: Generates performance charts in `out/GOOGL.png`
- **Realistic Modeling**: Includes slippage, transaction costs, and notional values
- **Performance Metrics**: Sharpe ratio, max drawdown, total return, win rate
- **Data Sources**: Supports Alpaca API or CSV files
- **Risk Management**: Position sizing and stop-loss simulation

### Historical Data Simulation

Replay historical data through the complete trading pipeline:

```bash
# Simulate with Alpaca data
python apps/simulator/main.py \
  --symbols AAPL,GOOGL,TSLA,MSFT \
  --start 2024-01-01 \
  --end 2024-01-31 \
  --timeframe 1Day \
  --speed 5.0 \
  --no-delays \
  --output simulation_results.json

# Simulate with CSV data
python apps/simulator/main.py \
  --symbols AAPL,GOOGL \
  --start 2024-01-01 \
  --end 2024-12-31 \
  --csv data/csv
```

### Service Management

The platform uses a comprehensive control system for managing all services:

```bash
# Start complete platform
python scripts/control.py start

# Start infrastructure only (Redis, API, Prometheus, Grafana)
python scripts/control.py start-infra

# Start trading services only
python scripts/control.py start-trading

# Stop all services
python scripts/control.py stop

# Check system status
python scripts/control.py status
```

### Individual Service Control
```bash
# Infrastructure services
python scripts/control.py start-redis
python scripts/control.py start-api
python scripts/control.py start-prometheus
python scripts/control.py start-grafana

# Trading services
python scripts/control.py start-trading
```

### System Verification
```bash
# Check all services status
python scripts/control.py status

# Test API health
curl -s http://127.0.0.1:8000/health

# View service logs
tail -f logs/api.log
tail -f logs/data_ingestor.log
```

## 📊 Access Points

| Service | URL | Credentials | Description |
|---------|-----|-------------|-------------|
| **Trading API** | http://127.0.0.1:8000 | None | REST API and system monitoring |
| **API Documentation** | http://127.0.0.1:8000/docs | None | Interactive API documentation |
| **Live Dashboard** | http://127.0.0.1:8000/dashboard | None | Real-time trading dashboard |
| **Prometheus (Global)** | http://127.0.0.1:9090 | None | System-wide metrics collection |
| **Risk Manager Metrics** | http://127.0.0.1:8013/metrics | None | Trading-specific Prometheus metrics |
| **Grafana** | http://127.0.0.1:3000 | admin / trading123 | Advanced dashboard and alerting |

## 🏗️ System Components

### Data Ingestor (`apps/data_ingestor/`)
Handles market data acquisition from Alpaca Markets:
- Downloads historical price data for configured symbols
- Provides live market data updates every minute
- Supports both IEX (free) and SIP (premium) data feeds
- Publishes all market data to Redis message bus
- Includes comprehensive error handling and retry logic

### Historical Simulator (`apps/simulator/`)
Replays historical market data for end-to-end backtesting:
- Loads data from Alpaca API or CSV files
- Publishes historical bars through the message bus at configurable speeds
- Supports multiple symbols with parallel simulation
- Provides simulation statistics and progress tracking
- Enables complete pipeline testing with historical data

### Strategy Engine (`apps/strategies/`)
Implements trading signal generation:
- **Random Strategy**: Simple random buy/sell signal generation for testing
- **Technical Strategy**: Advanced technical analysis using SMA, RSI, and MACD indicators
- Signal confidence scoring and metadata tracking
- Configurable parameters for each strategy
- Rate limiting to prevent signal spam

### Risk Manager (`apps/risk_manager/`)
Comprehensive risk management and signal validation:
- Market hours validation using US/Eastern timezone
- Position size calculations based on portfolio risk
- Rate limiting using monotonic time-based windows
- Circuit breaker patterns for fault isolation
- Emergency stop functionality
- Persistent deduplication across service restarts

### Executor (`apps/executor/`)
Order execution and management:
- Direct integration with Alpaca Markets API
- Exponential backoff retry logic for API reliability
- Comprehensive partial fill tracking and management
- Position validation before order submission
- Order status monitoring and fill reporting

### API Service (`apps/api/`)
REST API and monitoring interface:
- Health check endpoints with detailed status reporting
- Manual signal creation and management
- Portfolio and position monitoring
- Historical signal and trade data
- Real-time WebSocket dashboard
- Prometheus metrics exposure

### Rapid Backtester (`scripts/sim_random.py`)
Quick strategy validation tool:
- Off-bus backtesting for rapid strategy development
- Supports Alpaca API data download or CSV file input
- Synthetic data generation for testing when no data available
- Performance metrics calculation including Sharpe ratio and drawdown
- Optional visualization with matplotlib
- Configurable risk parameters and position sizing

### Message Bus (`lib/bus.py`)
Redis-based message communication with intelligent fallbacks:
- **Redis Streams** (primary): Reliable message delivery with consumer groups and automatic recovery
- **Redis Pub/Sub** (fallback): Compatible with older Redis versions
- **FakeRedis** (development): In-memory Redis simulation for testing
- **Smart Recovery**: Automatic handling of pending messages (Redis 6.2+) or manual reset for Redis 6.0
- **Consumer Groups**: Distributed message processing with load balancing
- **Message Persistence**: Durable message storage with replay capability
- **Health Monitoring**: Built-in connection health checks and automatic reconnection

## 🔧 Configuration

### Trading Configuration (`configs/base.yaml`)
```yaml
# Market symbols to trade
symbols:
  - "AAPL"
  - "MSFT" 
  - "GOOGL"
  - "TSLA"
  - "NVDA"

# Risk management parameters
risk:
  max_daily_loss: 0.05          # 5% maximum daily loss
  max_portfolio_risk: 0.20      # 20% maximum portfolio risk
  max_position_size: 0.10       # 10% maximum position size
  stop_loss_pct: 0.02           # 2% stop loss
  take_profit_pct: 0.06         # 6% take profit

# Strategy configurations
strategies:
  - name: "random_50_50"
    enabled: true
    risk_per_trade: 0.02
  - name: "smart_technical"
    enabled: true
    risk_per_trade: 0.05

# Simulation parameters
simulation:
  default_speed: 1.0            # Real-time speed multiplier
  max_speed: 100.0              # Maximum simulation speed
  default_timeframe: "1Day"     # Default data timeframe
```

### Data Feed Configuration
The system supports multiple data sources and feed types:

**Alpaca Integration**:
- Supports both IEX (free) and SIP (premium) feeds
- Automatic TimeFrame mapping for different intervals
- Handles 1Min, 5Min, 1Hour, and 1Day timeframes
- Robust error handling and retry logic

**CSV Data Format**:
```csv
timestamp,open,high,low,close,volume
2024-01-01,150.00,152.00,149.00,151.00,1000000
2024-01-02,151.00,153.00,150.50,152.50,1200000
```

**Message Bus Configuration**:
```bash
# Use Redis Streams (preferred)
export BUS_BACKEND=streams
export USE_FAKE_REDIS=0

# Use Pub/Sub fallback
export BUS_BACKEND=pubsub

# Force FakeRedis (development)
export USE_FAKE_REDIS=1
```

## 📈 Monitoring

### Service Health Monitoring
The control system performs comprehensive health checking:
- HTTP health checks for all web services with exponential backoff
- Redis connectivity and performance monitoring
- Process status and resource usage tracking
- Automatic service restart on failure detection
- Port conflict detection and cleanup

### Message Bus Monitoring
Track message flow and system health:
```bash
# Monitor Redis Streams (if using Redis Streams)
redis-cli XINFO GROUPS signals
redis-cli XLEN bars
redis-cli XINFO CONSUMERS signals signal_processors

# Check message bus health
curl -s http://127.0.0.1:8000/health | jq '.message_bus'
```

### Prometheus Metrics

The Risk Manager exposes comprehensive metrics on port 8013:

**Access Metrics:**
```bash
curl http://127.0.0.1:8013/metrics
```

**Key Metrics Collected:**
```promql
# Stream Health and Performance
redis_streams_length{stream="signals"}              # Pending messages
redis_streams_consumers{group="signal_processors"}   # Active consumers
redis_streams_pending{group="signal_processors"}     # Unacked messages
redis_streams_lag{group="signal_processors"}         # Consumer lag

# Business Metrics
signals_received_total{source="smart_technical"}     # Signals by source
signals_approved_total{symbol="GOOGL"}               # Approved signals
signals_rejected_total{reason="market_hours"}        # Rejection reasons
order_intents_published_total{symbol="GOOGL"}        # Orders sent

# Risk Metrics
risk_checks_total{check="market_hours"}              # Risk check counts
risk_violations_total{type="position_limit"}         # Risk violations
portfolio_value_usd{account="paper"}                 # Portfolio value
position_size{symbol="GOOGL"}                        # Position sizes

# System Performance
redis_operation_duration_seconds{operation="xadd"}   # Redis latency
message_processing_duration_seconds{type="signal"}   # Processing time
system_uptime_seconds                                 # Service uptime
```

**Grafana Integration:**
- Pre-configured dashboards available
- Real-time alerts on system health
- Business metrics visualization
- Performance monitoring

### Grafana Dashboards
Pre-configured dashboards for:
- System overview and health status
- Trading activity and performance
- Data pipeline monitoring with message flow
- Backtesting results and strategy performance
- Resource usage and performance metrics

### Logging System
Each service maintains individual log files in the `logs/` directory:
```
logs/
├── api.log                 # API service logs
├── data_ingestor.log      # Market data ingestion
├── strategies.log         # Trading strategy engine  
├── risk_manager.log       # Risk management system
├── executor.log          # Order execution
├── simulator.log         # Historical simulation
├── prometheus.log        # Metrics collection
└── grafana.log           # Dashboard system
```

## 🧪 Testing & Validation

### Quick Strategy Testing
```bash
# Test with synthetic data
python scripts/sim_random.py \
  --symbol GOOGL \
  --start 2022-01-01 \
  --end 2024-01-01 \
  --initial-cash 100000 \
  --position-notional 10000 \
  --signal-prob 0.05 \
  --seed 42

# Test with CSV data (alternative to --position-notional)
python scripts/sim_random.py \
  --symbol GOOGL \
  --start 2023-01-01 \
  --end 2023-12-31 \
  --csv data/csv/GOOGL.csv \
  --position-size 0.1 \
  --output backtest_results.json
```

### End-to-End Pipeline Testing
```bash
# Simulate historical data through complete pipeline
python apps/simulator/main.py \
  --symbols AAPL,GOOGL \
  --start 2024-01-01 \
  --end 2024-01-31 \
  --timeframe 1Day \
  --speed 10.0 \
  --no-delays

# Monitor pipeline in another terminal
tail -f logs/data_ingestor.log
tail -f logs/strategies.log
tail -f logs/risk_manager.log
```

### API Testing
```bash
# Health check
curl -s http://127.0.0.1:8000/health

# Manual signal creation
curl -X POST http://127.0.0.1:8000/signals/manual \
  -H "Content-Type: application/json" \
  -d '{"symbol":"AAPL","side":"BUY","confidence":0.8,"price":150.0}'

# View signal history
curl -s http://127.0.0.1:8000/signals/history

# Check message bus stats
curl -s http://127.0.0.1:8000/status | jq '.message_bus'
```

### System Validation
```bash
# Complete system test
python scripts/control.py stop
python scripts/control.py start
python scripts/control.py status

# Verify data pipeline with simulation
python apps/simulator/main.py --symbols AAPL --start 2024-01-01 --end 2024-01-02 --csv data/csv
```

## 🔍 Troubleshooting

### Service Startup Issues
```bash
# Check service dependencies
python scripts/launcher.py --check-deps

# View service logs
tail -f logs/api.log
tail -f logs/prometheus.log

# Check Redis connectivity
redis-cli ping  # Should return PONG

# Test with FakeRedis fallback
export USE_FAKE_REDIS=1
python scripts/control.py start-api
```

### Data Feed Issues
```bash
# Test Alpaca connectivity
python -c "
from lib.settings import get_settings
s = get_settings()
print(f'Has credentials: {s.has_alpaca_credentials}')
print(f'Data feed: {s.alpaca_data_feed}')
"

# Use CSV fallback for testing
mkdir -p data/csv
# Place CSV files with format: timestamp,open,high,low,close,volume
```

### Message Bus Issues
```bash
# Check Redis status and version
redis-cli info server
redis-cli --version

# Monitor message flow
redis-cli MONITOR

# Check Redis Streams (Redis 6.0+)
redis-cli XINFO GROUPS signals
redis-cli XINFO CONSUMERS signals signal_processors
redis-cli XLEN signals

# Force Pub/Sub fallback
export BUS_BACKEND=pubsub

# Use FakeRedis for development
export USE_FAKE_REDIS=1
```

### Redis 6.0 Compatibility Issues

**Problem**: `XAUTOCLAIM` command not available in Redis 6.0-6.1
```
ERROR: Redis command XAUTOCLAIM not supported
```

**Note**: La plataforma detecta automáticamente Redis < 6.2 y desactiva la recuperación automática de pendientes (seguirá funcionando). Se recomienda actualizar a 6.2+ para habilitarla.

**Solutions**:
1. **Upgrade Redis** (Recommended):
   ```bash
   # Ubuntu/Debian
   sudo apt update && sudo apt install redis-server

   # macOS
   brew upgrade redis

   # Docker
   docker run -p 6379:6379 redis:7-alpine
   ```

2. **Reset Consumer Group** (Redis 6.0 workaround):
   ```bash
   redis-cli XGROUP DESTROY signals signal_processors
   redis-cli XGROUP DESTROY orders.intent order_processors
   redis-cli XGROUP CREATE signals signal_processors "$" MKSTREAM
   redis-cli XGROUP CREATE orders.intent order_processors "$" MKSTREAM
   ```

3. **Use Pub/Sub Backend**:
   ```bash
   export BUS_BACKEND=pubsub
   ```

**Feature Comparison**:
| Redis Version | Streams Support | Auto Recovery | Recommended |
|---------------|----------------|---------------|-------------|
| 6.2+ | ✅ Full | ✅ Yes | ✅ |
| 6.0-6.1 | ⚠️ Limited | ❌ Manual | ⚠️ |
| < 6.0 | ❌ No | ❌ No | ❌ |

### TimeFrame Mapping Issues
Supported timeframes for Alpaca API:
- `1Min` - One minute bars
- `5Min` - Five minute bars  
- `1Hour` - One hour bars
- `1Day` - Daily bars

### Port Conflicts
The control system automatically handles port cleanup:
```bash
🧹 Cleaning port 8000...
🔫 Killing process 12345 (uvicorn) listening on port 8000
✅ Killed 1 processes using port 8000
```

### Memory Optimization
For resource-constrained environments:
```bash
# Limit Prometheus retention
--storage.tsdb.retention.time=1d

# Use smaller Redis memory
redis-server --maxmemory 100mb --maxmemory-policy allkeys-lru

# Reduce simulation speed
python apps/simulator/main.py --speed 0.1  # Very slow simulation
```

## 📚 API Reference

### REST Endpoints
- `GET /health` - System health check with message bus status
- `GET /status` - Comprehensive system status including message bus statistics
- `GET /metrics` - Prometheus metrics
- `POST /signals/manual` - Create manual trading signal
- `GET /signals/history` - Signal history with filtering
- `GET /portfolio` - Current portfolio state
- `GET /positions/{symbol}` - Position details

### WebSocket Endpoints
- `WS /ws/dashboard` - Real-time dashboard updates

### Message Bus Events
The system publishes various event types through Redis:
- `bars` - Market data bars
- `signals` - Trading signals
- `orders.intent` - Order intentions
- `orders.fill` - Order execution results
- `system` - System events and status updates

**Consumer Groups:**
- `signal_processors` - Processes trading signals (Risk Manager)
- `order_processors` - Processes order intents (Executor)
- `bar_processors` - Processes market data (Strategies)

## 🔒 Security

- **Local Binding**: All services bind to 127.0.0.1 by default
- **Paper Trading**: System configured for paper trading only
- **Credential Management**: Environment variable-based credential storage
- **Process Isolation**: Individual service processes with controlled communication
- **Safe Message Processing**: Messages acknowledged only after successful processing

## 🏗️ Development

### Project Structure
```
algorithmic-trading-platform/
├── apps/                   # Microservices
│   ├── api/               # REST API and monitoring
│   ├── data_ingestor/     # Market data ingestion
│   ├── strategies/        # Trading strategies
│   ├── risk_manager/      # Risk management
│   ├── executor/          # Order execution
│   └── simulator/         # Historical data simulation
├── lib/                   # Shared libraries
│   ├── models.py          # Pydantic data models
│   ├── bus.py            # Redis message bus with fallbacks
│   ├── settings.py       # Configuration management
│   ├── time_utils.py     # Time utilities
│   └── deduplication.py  # Idempotency service
├── scripts/              # Management and testing scripts
│   ├── control.py        # Service management
│   ├── launcher.py       # Service launcher
│   ├── sim_random.py     # Rapid backtesting tool
│   └── setup.sh         # System setup
├── data/                 # Data directory
│   └── csv/             # CSV data files
├── logs/                 # Service log files
├── pids/                 # Process ID files
├── configs/              # Configuration files
└── requirements.txt      # Python dependencies
```

### Adding New Strategies
1. Create strategy class in `apps/strategies/`
2. Implement `analyze()` method returning `Signal` objects
3. Add strategy to configuration
4. Test with simulator: `python apps/simulator/main.py --csv data/csv`
5. Validate with rapid backtester: `python scripts/sim_random.py`

### Extending the API
1. Add new endpoints to `apps/api/main.py`
2. Update API documentation
3. Add corresponding tests
4. Update Prometheus metrics if needed
5. Test with `curl` commands

### Creating Custom Data Sources
1. Implement data loader in simulator or backtester
2. Follow CSV format: `timestamp,open,high,low,close,volume`
3. Test with both tools to ensure compatibility
4. Add configuration options if needed

## 🤝 Contributing

1. Fork the repository
2. Create feature branch
3. Implement changes with comprehensive testing
4. Test with both simulator and backtester
5. Ensure all health checks pass
6. Update documentation
7. Submit pull request

## 📞 Support

For issues and questions:
- Check service logs in `logs/` directory
- Use `python scripts/control.py status` for system health
- Monitor Prometheus metrics at http://127.0.0.1:9090
- Review configuration in `configs/base.yaml`
- Test individual components with simulator and backtester
- Check Redis connectivity: `redis-cli ping`

The platform provides a complete foundation for algorithmic trading with enterprise-grade reliability, monitoring, and risk management capabilities, along with comprehensive testing and simulation tools for strategy development and validation