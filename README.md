# Algorithmic Trading Platform

A comprehensive, microservices-based algorithmic trading platform built with Python, featuring real-time market data ingestion, intelligent trading strategies, robust risk management, and enterprise-grade monitoring and observability.

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
```

The platform implements a distributed architecture where each component communicates through a Redis-based message bus using Redis Streams for reliable message delivery. The system processes real-time market data, generates trading signals using multiple strategies, validates them through comprehensive risk management, and executes orders through the Alpaca broker API.

## 🚀 Core Features

### Market Data Pipeline
- **Real-time Data Ingestion**: Connects to Alpaca Markets API for live and historical market data
- **IEX Feed Support**: Uses Alpaca's IEX data feed, compatible with free paper trading accounts
- **Historical Data Processing**: Downloads and processes historical price data for backtesting and analysis
- **Redis Streams Integration**: Publishes market data through reliable message streaming

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

### Monitoring & Observability
- **Prometheus Metrics**: Comprehensive system and business metrics collection
- **Grafana Dashboards**: Real-time visualization and alerting capabilities
- **Health Monitoring**: Automated health checks for all services with detailed status reporting
- **Service Logging**: Individual log files for each service component
- **Real-time Dashboard**: WebSocket-based live trading dashboard

### Service Management
- **Control System**: Comprehensive service management through `scripts/control.py`
- **Health Checking**: Strict health validation with exponential backoff
- **Process Management**: PID tracking and graceful shutdown for all services
- **Port Management**: Intelligent port cleanup to prevent conflicts
- **Dependency Validation**: Automatic checking of service dependencies

## 📋 Prerequisites

- Python 3.9+
- Redis Server
- Alpaca Markets Account (Paper Trading)
- `lsof` utility (recommended)
- 8GB RAM minimum
- Linux/macOS/WSL2

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

# Edit with your Alpaca credentials
nano .env
```

Required environment variables:
```bash
# Alpaca API Configuration
APCA_API_BASE_URL=https://paper-api.alpaca.markets
APCA_API_KEY_ID=your_key_here
APCA_API_SECRET_KEY=your_secret_here

# Data Feed Configuration
ALPACA_DATA_FEED=iex  # Use 'iex' for free accounts, 'sip' for premium

# Trading Configuration
SYMBOLS=AAPL,MSFT,GOOGL,TSLA,NVDA
HISTORICAL_DAYS=7
RISK_PCT=0.02
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
| **Prometheus** | http://127.0.0.1:9090 | None | System metrics and monitoring |
| **Grafana** | http://127.0.0.1:3000 | admin / trading123 | Advanced dashboard and alerting |

## 🏗️ System Components

### Data Ingestor (`apps/data_ingestor/`)
Handles market data acquisition from Alpaca Markets:
- Downloads historical price data for configured symbols
- Provides live market data updates every minute
- Supports both IEX (free) and SIP (premium) data feeds
- Publishes all market data to Redis message bus
- Includes comprehensive error handling and retry logic

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

### Message Bus (`lib/bus.py`)
Redis-based message communication:
- Redis Streams for reliable message delivery
- Automatic fallback to Pub/Sub for compatibility
- Message replay capability for debugging
- Consumer group management
- Comprehensive error handling and reconnection logic

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
```

### Data Feed Configuration
The system supports two data feed types:

**IEX Feed (Free)**:
- Included with all Alpaca accounts
- Suitable for paper trading and development
- 15-minute delayed data for free accounts

**SIP Feed (Premium)**:
- Requires paid subscription
- Real-time market data
- Professional-grade data quality

Set via environment variable:
```bash
export ALPACA_DATA_FEED=iex  # or 'sip'
```

## 📈 Monitoring

### Service Health Monitoring
The control system performs comprehensive health checking:
- HTTP health checks for all web services
- Redis connectivity and performance monitoring
- Process status and resource usage tracking
- Automatic service restart on failure detection

### Prometheus Metrics
Key metrics collected include:
```promql
# System health
trading_system_health{component="api"}

# Data pipeline
trading_stream_length{stream_name="trading:bars"}

# Business metrics
trading_signals_generated_total
trading_orders_submitted_total
trading_portfolio_value_usd

# Performance metrics
trading_redis_latency_ms
http_request_duration_seconds
```

### Grafana Dashboards
Pre-configured dashboards for:
- System overview and health status
- Trading activity and performance
- Data pipeline monitoring
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
├── prometheus.log        # Metrics collection
└── grafana.log           # Dashboard system
```

## 🧪 Testing

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
```

### System Validation
```bash
# Complete system test
python scripts/control.py stop
python scripts/control.py start
python scripts/control.py status

# Verify data pipeline
tail -f logs/data_ingestor.log  # Historical data download
tail -f logs/strategies.log     # Signal generation
tail -f logs/risk_manager.log   # Signal validation
```

## 🔍 Troubleshooting

### Service Startup Issues
```bash
# Check service dependencies
python scripts/launcher.py --check-deps

# View service logs
tail -f logs/api.log
tail -f logs/prometheus.log

# Restart services individually
python scripts/control.py stop
python scripts/control.py start-redis
python scripts/control.py start-api
```

### Data Feed Issues
For Alpaca subscription errors, ensure correct feed configuration:
```bash
# Use IEX feed for free accounts (default)
export ALPACA_DATA_FEED=iex

# Use SIP feed for premium accounts
export ALPACA_DATA_FEED=sip
```

### Port Conflicts
The control system automatically handles port cleanup:
```bash
🧹 Cleaning port 8000...
🔫 Killing process 12345 (uvicorn) listening on port 8000
✅ Killed 1 processes using port 8000
```

### Memory Issues
Prometheus configuration includes memory optimization:
```bash
--storage.tsdb.retention.time=3d  # Limited data retention
--web.listen-address=127.0.0.1:9090  # Local binding only
```

## 📚 API Reference

### REST Endpoints
- `GET /health` - System health check
- `GET /status` - Comprehensive system status
- `GET /metrics` - Prometheus metrics
- `POST /signals/manual` - Create manual trading signal
- `GET /signals/history` - Signal history with filtering
- `GET /portfolio` - Current portfolio state
- `GET /positions/{symbol}` - Position details

### WebSocket Endpoints
- `WS /ws/dashboard` - Real-time dashboard updates

## 🔒 Security

- **Local Binding**: All services bind to 127.0.0.1 by default
- **Paper Trading**: System configured for paper trading only
- **Credential Management**: Environment variable-based credential storage
- **Process Isolation**: Individual service processes with controlled communication

## 🏗️ Development

### Project Structure
```
algorithmic-trading-platform/
├── apps/                   # Microservices
│   ├── api/               # REST API and monitoring
│   ├── data_ingestor/     # Market data ingestion
│   ├── strategies/        # Trading strategies
│   ├── risk_manager/      # Risk management
│   └── executor/          # Order execution
├── lib/                   # Shared libraries
│   ├── models.py          # Pydantic data models
│   ├── bus.py            # Redis message bus
│   ├── settings.py       # Configuration management
│   ├── time_utils.py     # Time utilities
│   └── deduplication.py  # Idempotency service
├── scripts/              # Management scripts
│   ├── control.py        # Service management
│   ├── launcher.py       # Service launcher
│   └── setup.sh         # System setup
├── logs/                 # Service log files
├── pids/                 # Process ID files
├── configs/              # Configuration files
└── requirements.txt      # Python dependencies
```

### Adding New Strategies
1. Create strategy class in `apps/strategies/`
2. Implement `analyze()` method returning `Signal` objects
3. Add strategy to configuration
4. Test with controlled market data

### Extending the API
1. Add new endpoints to `apps/api/main.py`
2. Update API documentation
3. Add corresponding tests
4. Update Prometheus metrics if needed

## 🤝 Contributing

1. Fork the repository
2. Create feature branch
3. Implement changes with comprehensive testing
4. Ensure all health checks pass
5. Update documentation
6. Submit pull request

## 📞 Support

For issues and questions:
- Check service logs in `logs/` directory
- Use `python scripts/control.py status` for system health
- Monitor Prometheus metrics at http://127.0.0.1:9090
- Review configuration in `configs/base.yaml`

The platform provides a complete foundation for algorithmic trading with enterprise-grade reliability, monitoring, and risk management capabilities.