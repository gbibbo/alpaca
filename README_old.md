# Algorithmic Trading Platform

A production-ready, microservices-based algorithmic trading platform built with Python. Features real-time market data processing, intelligent trading strategies, comprehensive risk management, backtesting capabilities, and enterprise-grade monitoring.

> **Note**: For development history and implementation milestones, see [DEVELOPMENT_HISTORY.md](DEVELOPMENT_HISTORY.md)

## 🎯 Key Features

### 📊 Market Data & Trading
- **Real-time Data**: Live market data from Alpaca Markets (IEX and SIP feeds)
- **Historical Simulation**: Replay historical data through the complete trading pipeline
- **Multiple Strategies**: Random and technical analysis-based trading strategies
- **Risk Management**: Multi-layer validation with position limits, confidence thresholds, and circuit breakers
- **Order Execution**: Direct Alpaca integration with comprehensive order lifecycle management
- **Market Hours**: NYSE/NASDAQ calendar with holiday and early-close detection

### 🔄 Reliability & Performance
- **Message Bus**: Redis Streams with consumer groups for guaranteed delivery
- **Idempotency**: Deterministic order IDs prevent duplicates during retries
- **State Machine**: 13-state order FSM with automatic timeouts and cancellations
- **Persistence**: SQLite/CSV/Parquet export with SHA256 verification
- **Auto-recovery**: Automatic message reclaim and pending message handling

### 📈 Monitoring & Analysis
- **Prometheus Metrics**: Comprehensive business and system metrics
- **Grafana Dashboards**: Real-time visualization and alerting
- **WebSocket Dashboard**: Live trading dashboard with real-time updates
- **Backtesting API**: REST endpoints for job-based backtest management
- **Performance Reports**: Detailed analytics with Sharpe ratio, drawdown, win rates

---

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
│  Port: 8001     │    │                 │    │                 │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         ▲                       ▲                       ▲
         │                       │                       │
         └───────────────────────┼───────────────────────┘
                                 │
                    ┌─────────────────┐    ┌─────────────────┐
                    │   Strategies    │    │    Executor     │
                    │   (Signals)     │    │ (Order Mgmt)    │
                    └─────────────────┘    └─────────────────┘
                                 │
                    ┌─────────────────┐    ┌─────────────────┐
                    │   Simulator     │    │   Backtester    │
                    │ (Historical)    │    │ (Rapid Testing) │
                    └─────────────────┘    └─────────────────┘
```

The platform uses a distributed, event-driven architecture where components communicate through Redis Streams for reliable message delivery with automatic fallback to Pub/Sub.

---

## 📋 Prerequisites

**Required:**
- Python 3.9+
- Redis Server 6.0+ (or automatic FakeRedis fallback)

**Recommended:**
- 8GB RAM minimum
- Linux/macOS/WSL2/Windows
- Alpaca Markets Paper Trading Account (optional for CSV-based testing)

**Optional Tools:**
- `lsof` for port management
- `curl` for API testing

### Redis Compatibility

| Version | Streams Support | Auto Recovery | Status |
|---------|----------------|---------------|--------|
| 6.2+ | ✅ Full | ✅ Yes | ✅ Recommended |
| 6.0-6.1 | ⚠️ Limited | ⚠️ Manual | ⚠️ Compatible |
| < 6.0 | ❌ None | ❌ No | ❌ Use Pub/Sub |

---

## 🚀 Quick Start

### 1. Installation

```bash
# Clone repository
git clone <repository-url>
cd algorithmic-trading-platform

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/macOS
# or
venv\Scripts\activate     # Windows

# Install dependencies
pip install -r requirements.txt
```

### 2. Configuration

```bash
# Copy environment template
cp .env.template .env

# Edit configuration (optional for CSV testing)
nano .env
```

**Key Environment Variables:**
```bash
# Alpaca API (optional - for live data)
APCA_API_BASE_URL=https://paper-api.alpaca.markets
APCA_API_KEY_ID=your_key_here
APCA_API_SECRET_KEY=your_secret_here
ALPACA_DATA_FEED=iex  # 'iex' for free, 'sip' for premium

# Trading Configuration
SYMBOLS=AAPL,MSFT,GOOGL,TSLA,NVDA
HISTORICAL_DAYS=7
RISK_PCT=0.02

# Message Bus (Redis Streams preferred)
BUS_BACKEND=streams
REDIS_URL=redis://127.0.0.1:6379
USE_FAKE_REDIS=0  # Set to 1 for development

# Metrics Ports
RISK_METRICS_PORT=8011
EXECUTOR_METRICS_PORT=8012
API_METRICS_PORT=8016
```

### 3. Infrastructure Setup

```bash
# Automated setup (installs Prometheus and Grafana)
chmod +x scripts/setup.sh
./scripts/setup.sh

# Or manual setup
python scripts/setup_infrastructure.py
```

### 4. Verify Installation

**Terminal A - Risk Manager:**
```bash
export BUS_BACKEND=streams REDIS_URL=redis://127.0.0.1:6379
python apps/risk_manager/main.py
```

**Terminal B - Executor:**
```bash
export BUS_BACKEND=streams REDIS_URL=redis://127.0.0.1:6379
python apps/executor/main.py
```

**Terminal C - Test Signal:**
```bash
export BUS_BACKEND=streams REDIS_URL=redis://127.0.0.1:6379
python -c "
from lib.bus import connect_bus, get_bus
from lib.models import Signal, SignalSide
from decimal import Decimal
connect_bus(); bus = get_bus()
sig = Signal(symbol='GOOGL', side=SignalSide.BUY, confidence=Decimal('0.9'), price=Decimal('151.00'), source='smart_technical')
bus.publish_signal(sig)
print('✅ Signal published')
"
```

**Expected Output:**
- Risk Manager: `✅ Signal approved and order created: GOOGL BUY confidence=0.9`
- Executor: `Received order intent: GOOGL BUY qty=65 notional=9965.00`

---

## 💡 Usage Guide

### Rapid Strategy Backtesting

Test strategies quickly without full system setup:

```bash
# Quick backtest with Alpaca data
python scripts/sim_random.py \
  --symbol GOOGL \
  --start 2023-01-01 \
  --end 2024-01-01 \
  --initial-cash 100000 \
  --position-notional 10000 \
  --signal-prob 0.05 \
  --seed 42 \
  --plot out/GOOGL.png

# Backtest with CSV data
python scripts/sim_random.py \
  --symbol AAPL \
  --start 2023-01-01 \
  --end 2023-12-31 \
  --csv data/csv/AAPL.csv \
  --position-size 0.1 \
  --output backtest_results.json
```

**Features:**
- 📊 Generates performance charts
- 💰 Includes slippage and transaction costs
- 📈 Calculates Sharpe ratio, max drawdown, total return
- 🎲 Reproducible with `--seed` parameter

### Historical Data Simulation

Replay historical data through the complete pipeline:

```bash
# Simulate with Alpaca data
python apps/simulator/main.py \
  --symbols AAPL,GOOGL,TSLA \
  --start 2024-01-01 \
  --end 2024-01-31 \
  --timeframe 1Day \
  --speed 5.0 \
  --seed 42 \
  --persist \
  --output simulation_results.json

# Simulate with CSV data
python apps/simulator/main.py \
  --symbols AAPL,GOOGL \
  --start 2024-01-01 \
  --end 2024-12-31 \
  --csv data/csv \
  --persist
```

**Persistence Features:**
- 💾 SQLite database with complete history
- 📤 Export to CSV and Parquet formats
- 🔒 SHA256 verification for reproducibility
- 📁 Organized output in `out/run_<timestamp>_<uuid>/`

### Service Management

```bash
# Start complete platform
python scripts/control.py start

# Start infrastructure only
python scripts/control.py start-infra

# Start trading services
python scripts/control.py start-trading

# Check system status
python scripts/control.py status

# Stop all services
python scripts/control.py stop
```

### Individual Services

```bash
# Infrastructure
python scripts/control.py start-redis
python scripts/control.py start-api
python scripts/control.py start-prometheus
python scripts/control.py start-grafana

# Trading components
python scripts/control.py start-trading
```

### Backtest API

```bash
# Create backtest job
curl -X POST http://127.0.0.1:8001/backtest/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "symbols": ["AAPL", "GOOGL"],
    "start_date": "2023-01-01",
    "end_date": "2023-12-31",
    "timeframe": "1Day",
    "seed": 42,
    "strategies": ["random_50_50"]
  }'

# Quick backtest
curl -X POST "http://127.0.0.1:8001/backtest/quick?symbols=AAPL&days=30&seed=42"

# List all jobs
curl http://127.0.0.1:8001/backtest/jobs

# Get job status
curl http://127.0.0.1:8001/backtest/jobs/{job_id}

# Download results
curl http://127.0.0.1:8001/backtest/jobs/{job_id}/download -o results.json
```

### Dynamic Strategy Configuration

Control strategy behavior in real-time:

```python
from lib.bus import connect_bus, get_bus

connect_bus()
bus = get_bus()

# Set reproducible seed for all strategies
bus.publish_system_event(
    event_type="strategy_config",
    source="backtester",
    data={
        "config_type": "reproducible_mode",
        "random_seed": 42
    }
)
```

**What happens:**
1. Event published to Redis `system` stream
2. Strategy Engine consumes via consumer group
3. Strategy updates its RNG with new seed
4. All subsequent signals become deterministic

---

## 🌐 Access Points

| Service | URL | Credentials | Description |
|---------|-----|-------------|-------------|
| **Trading API** | http://127.0.0.1:8001 | None | REST API and monitoring |
| **API Docs** | http://127.0.0.1:8001/docs | None | Interactive Swagger UI |
| **Live Dashboard** | http://127.0.0.1:8001/dashboard | None | Real-time trading dashboard |
| **Backtest Jobs** | http://127.0.0.1:8001/backtest/jobs | None | Job management |
| **Prometheus** | http://127.0.0.1:9090 | None | Metrics collection |
| **Grafana** | http://127.0.0.1:3000 | admin / trading123 | Dashboards & alerts |

### Metrics Endpoints

| Component | Port | URL |
|-----------|------|-----|
| Risk Manager | 8011 | http://127.0.0.1:8011/metrics |
| Executor | 8012 | http://127.0.0.1:8012/metrics |
| Strategies | 8013 | http://127.0.0.1:8013/metrics |
| Simulator | 8014 | http://127.0.0.1:8014/metrics |
| PnL Aggregator | 8015 | http://127.0.0.1:8015/metrics |
| API | 8016 | http://127.0.0.1:8016/metrics |

---

## 📊 Key Metrics

### Business Metrics
```promql
# Trading Activity
signals_received_total{source="smart_technical"}
signals_approved_total{symbol="GOOGL"}
signals_rejected_total{reason="market_hours"}
order_intents_published_total{symbol="GOOGL"}

# Risk Management
risk_checks_total{check="market_hours"}
risk_violations_total{type="position_limit"}
portfolio_value_usd{account="paper"}
position_size{symbol="GOOGL"}

# Order Execution
duplicate_order_blocked_by_client_id_total{symbol}
broker_429_retries_total{operation, success}
```

### System Metrics
```promql
# Message Bus Health
redis_streams_length{stream="signals"}
redis_streams_pending{group="signal_processors"}
redis_streams_lag{group="signal_processors"}

# Performance
redis_operation_duration_seconds{operation="xadd"}
message_processing_duration_seconds{type="signal"}
system_uptime_seconds
```

---

## 🎛️ System Components

### Data Ingestor
- Downloads market data from Alpaca Markets
- Supports IEX (free) and SIP (premium) feeds
- Publishes bars to message bus
- Handles rate limiting and retries

### Historical Simulator
- Replays historical data for backtesting
- Loads from Alpaca API or CSV files
- Configurable playback speed
- Persistence with SQLite/CSV/Parquet export

### Strategy Engine
- **Random Strategy**: Testing and baseline generation
- **Technical Strategy**: SMA, RSI, MACD indicators
- Confidence scoring and metadata
- Real-time configuration via system events

### Risk Manager
- Market hours validation (NYSE/NASDAQ calendar)
- Position sizing and limits
- Rate limiting and circuit breakers
- Emergency stop functionality
- Persistent deduplication

### Executor
- Alpaca Markets integration
- Order lifecycle management (13-state FSM)
- Idempotent order submission
- Automatic timeout handling
- Partial fill tracking

### API Service
- REST endpoints for monitoring and control
- WebSocket dashboard for real-time updates
- Backtest job management
- Prometheus metrics exposure
- Health checks and system status

### Message Bus
- **Redis Streams** (primary): Consumer groups, auto-recovery
- **Redis Pub/Sub** (fallback): Compatible with Redis < 6.0
- **FakeRedis** (development): In-memory testing
- Automatic reconnection and health monitoring

---

## 🧪 Testing & Validation

### Unit Tests
```bash
# Run all tests
python -m pytest -v

# Run specific test files
python -m pytest tests/test_epic4_idempotency.py -v
python -m pytest tests/test_edge_cases.py -v
python -m pytest tests/test_load_performance.py -v

# Run with coverage
python -m pytest --cov=lib --cov=apps -v
```

### Integration Tests
```bash
# Low-level Streams testing
export REDIS_URL="redis://localhost:6379/0"
python scripts/qa/streams_low_level_check.py

# End-to-end pipeline
bash scripts/qa/e2e_streams_pipeline.sh

# Metrics validation
bash scripts/qa/metrics_smoke_strict.sh
```

### API Testing
```bash
# Test backtest API
python -m pytest tests/test_backtest_api.py -v

# Manual API testing
curl http://127.0.0.1:8001/health
curl http://127.0.0.1:8001/status
```

### Manual Testing
```bash
# Test strategy with simulation
python apps/simulator/main.py \
  --symbols AAPL,GOOGL \
  --start 2024-01-01 \
  --end 2024-01-02 \
  --csv data/csv \
  --no-delays

# Monitor in another terminal
tail -f logs/strategies.log
tail -f logs/risk_manager.log
tail -f logs/executor.log
```

---

## 🔧 Configuration

### Trading Configuration (`configs/base.yaml`)
```yaml
symbols:
  - "AAPL"
  - "MSFT"
  - "GOOGL"
  - "TSLA"
  - "NVDA"

risk:
  max_daily_loss: 0.05          # 5%
  max_portfolio_risk: 0.20      # 20%
  max_position_size: 0.10       # 10%
  stop_loss_pct: 0.02           # 2%
  take_profit_pct: 0.06         # 6%

strategies:
  - name: "random_50_50"
    enabled: true
    risk_per_trade: 0.02
  - name: "smart_technical"
    enabled: true
    risk_per_trade: 0.05

simulation:
  default_speed: 1.0
  max_speed: 100.0
  default_timeframe: "1Day"
```

### CSV Data Format
```csv
timestamp,open,high,low,close,volume
2024-01-01,150.00,152.00,149.00,151.00,1000000
2024-01-02,151.00,153.00,150.50,152.50,1200000
```

Place CSV files in `data/csv/SYMBOL.csv` format.

---

## 🔍 Troubleshooting

### Redis Connection Issues
```bash
# Check Redis status
redis-cli ping  # Should return PONG
redis-cli info server

# Test with FakeRedis fallback
export USE_FAKE_REDIS=1
python scripts/control.py start-api

# Force Pub/Sub mode
export BUS_BACKEND=pubsub
```

### Service Startup Issues
```bash
# Check service logs
tail -f logs/api.log
tail -f logs/risk_manager.log

# Verify dependencies
python scripts/launcher.py --check-deps

# Clean up ports
python scripts/control.py stop
lsof -i :8001,6379,9090,3000
```

### Alpaca API Issues
```bash
# Test credentials
python -c "
from lib.settings import get_settings
s = get_settings()
print(f'Has credentials: {s.has_alpaca_credentials}')
print(f'Data feed: {s.alpaca_data_feed}')
"

# Use CSV fallback for testing
mkdir -p data/csv
# Place CSV files here
```

### Redis 6.0 Compatibility
```bash
# Upgrade Redis (recommended)
docker run -p 6379:6379 redis:7-alpine

# Or reset consumer groups
redis-cli XGROUP DESTROY signals signal_processors
redis-cli XGROUP CREATE signals signal_processors "$" MKSTREAM

# Or use Pub/Sub
export BUS_BACKEND=pubsub
```

### Port Conflicts
```bash
# Automatic cleanup
python scripts/control.py stop

# Manual cleanup
lsof -tiTCP:8001,8011,8012 -sTCP:LISTEN | xargs kill -9
```

---

## 📚 API Reference

### REST Endpoints

**System:**
- `GET /health` - Health check
- `GET /status` - System status
- `GET /metrics` - Prometheus metrics

**Trading:**
- `POST /signals/manual` - Create signal
- `GET /signals/history` - Signal history
- `GET /portfolio` - Portfolio state
- `GET /positions/{symbol}` - Position details

**Backtesting:**
- `POST /backtest/jobs` - Create job
- `GET /backtest/jobs` - List jobs
- `GET /backtest/jobs/{id}` - Job status
- `POST /backtest/jobs/{id}/start` - Start job
- `POST /backtest/jobs/{id}/cancel` - Cancel job
- `GET /backtest/jobs/{id}/results` - Results
- `GET /backtest/jobs/{id}/download` - Download
- `POST /backtest/quick` - Quick test
- `GET /backtest/stats` - Statistics

### WebSocket
- `WS /ws/dashboard` - Real-time updates

### Message Bus Streams
- `bars` - Market data
- `signals` - Trading signals
- `orders.intent` - Order requests
- `orders.fill` - Executions
- `system` - System events

---

## 🏗️ Development

### Project Structure
```
algorithmic-trading-platform/
├── apps/                   # Microservices
│   ├── api/               # REST API
│   ├── data_ingestor/     # Market data
│   ├── strategies/        # Trading strategies
│   ├── risk_manager/      # Risk management
│   ├── executor/          # Order execution
│   └── simulator/         # Historical sim
├── lib/                   # Shared libraries
│   ├── models.py          # Data models
│   ├── bus.py            # Message bus
│   ├── settings.py       # Configuration
│   └── time_utils.py     # Time utilities
├── scripts/              # Management scripts
│   ├── control.py        # Service control
│   ├── sim_random.py     # Backtester
│   └── setup.sh         # Setup
├── data/csv/             # CSV data files
├── logs/                 # Service logs
├── out/                  # Results
└── configs/              # Configuration
```

### Adding New Strategies
1. Create class in `apps/strategies/`
2. Implement `analyze()` returning `Signal` objects
3. Add to configuration
4. Test with simulator
5. Validate with backtester

### Extending the API
1. Add endpoints to `apps/api/main.py`
2. Update API documentation
3. Add tests
4. Update metrics

---

## 🤝 Contributing

1. Fork the repository
2. Create feature branch
3. Implement with tests
4. Test with simulator and backtester
5. Ensure health checks pass
6. Update documentation
7. Submit pull request

---

## 📞 Support

**Troubleshooting Steps:**
1. Check logs in `logs/` directory
2. Verify system status: `python scripts/control.py status`
3. Monitor metrics: http://127.0.0.1:9090
4. Review configuration: `configs/base.yaml`
5. Test components individually
6. Check Redis: `redis-cli ping`

**Common Issues:**
- Port conflicts → `python scripts/control.py stop`
- Redis connection → Set `USE_FAKE_REDIS=1`
- Alpaca API → Use CSV data fallback
- Service startup → Check logs and dependencies

---

## 📄 License

See LICENSE file for details.

---

## 🎯 Getting Started Checklist

- [ ] Install Python 3.9+ and Redis 6.0+
- [ ] Clone repository and install dependencies
- [ ] Configure environment variables
- [ ] Run infrastructure setup
- [ ] Verify installation with smoke test
- [ ] Run a quick backtest
- [ ] Simulate historical data
- [ ] Access monitoring dashboards
- [ ] Review API documentation
- [ ] Explore Grafana dashboards

**Next Steps:**
- Review [DEVELOPMENT_HISTORY.md](DEVELOPMENT_HISTORY.md) for implementation details
- Check `configs/base.yaml` for trading parameters
- Explore example backtests in `scripts/sim_random.py`
- Monitor system health in Grafana

---

**Ready to trade?** Start with a quick backtest to verify your setup:

```bash
python scripts/sim_random.py --symbol AAPL --start 2023-01-01 --end 2023-12-31 --seed 42 --plot out/test.png
```

🚀 Happy trading!
