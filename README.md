# Algorithmic Trading Platform

A modern, microservices-based algorithmic trading platform with comprehensive monitoring, real-time dashboards, and enterprise-grade observability.

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
│  (Monitoring)   │◄───┤  (Validation)   │◄───┤   (Alpaca Data) │
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

## 🚀 Features

### Core Trading Infrastructure
- **Microservices Architecture**: Loosely coupled services with Redis Streams
- **Real-time Data Ingestion**: Live and historical market data from Alpaca
- **Multiple Trading Strategies**: Random and technical analysis strategies
- **Advanced Risk Management**: Multi-layer validation and circuit breakers
- **Order Execution**: Alpaca integration with retry logic and error handling

### Monitoring & Observability
- **Prometheus Metrics**: Comprehensive system and business metrics
- **Grafana Dashboards**: Real-time visualization and alerting
- **Health Monitoring**: Service health checks and automatic recovery
- **Performance Tracking**: Latency, throughput, and error rate monitoring
- **Real-time Dashboard**: WebSocket-based live trading dashboard

### Enhanced Features
- **Persistent Deduplication**: Redis-based idempotency across restarts
- **Timezone-aware Operations**: US/Eastern market time handling
- **Rate Limiting**: Intelligent API rate management with backoff
- **Circuit Breakers**: Automatic fault isolation and recovery
- **Comprehensive Logging**: Structured logging with performance metrics

## 📋 Prerequisites

- Python 3.11+
- Redis Server (or uses embedded fakeredis)
- Alpaca Markets Account (Paper Trading)
- 8GB RAM minimum
- Linux/macOS/WSL2

## 🛠️ Installation

### 1. Clone and Setup
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

# Trading Configuration
SYMBOLS=AAPL,MSFT,GOOGL,TSLA,NVDA
HISTORICAL_DAYS=7
RISK_PCT=0.02
```

### 4. Infrastructure Setup

#### Option A: Automated Setup (Recommended)
```bash
# Run setup script
chmod +x scripts/setup.sh
./scripts/setup.sh
```

#### Option B: Manual Setup
```bash
# Install Prometheus
wget https://github.com/prometheus/prometheus/releases/download/v2.45.0/prometheus-2.45.0.linux-amd64.tar.gz
tar -xzf prometheus-2.45.0.linux-amd64.tar.gz
cp prometheus.yml prometheus-2.45.0.linux-amd64/

# Install Grafana
cd ~/
wget https://dl.grafana.com/oss/release/grafana-10.2.4.linux-amd64.tar.gz
tar -zxf grafana-10.2.4.linux-amd64.tar.gz
cd -
```

## 🚀 Quick Start

### 1. Start Infrastructure
```bash
# Start Redis, API, Prometheus, and Grafana
python scripts/control.py start
```

### 2. Start Prometheus (Manual)
```bash
# In separate terminal - keep running
cd prometheus-2.45.0.linux-amd64
./prometheus --config.file=prometheus.yml --web.listen-address=:9090
```

### 3. Start Trading Services
```bash
# In separate terminal
python scripts/launcher.py --services data_ingestor strategies risk_manager executor
```

### 4. Verify System
```bash
# Check all services
python scripts/control.py status
```

## 📊 Access Interfaces

| Service | URL | Credentials |
|---------|-----|-------------|
| **Grafana Dashboard** | http://localhost:3000 | admin / trading123 |
| **Prometheus Metrics** | http://localhost:9090 | None |
| **Trading API** | http://localhost:8000 | None |
| **Live Dashboard** | http://localhost:8000/dashboard | None |
| **API Documentation** | http://localhost:8000/docs | None |

## 🔧 Usage

### Manual Trading Signals
```bash
# Create manual signal
curl -X POST http://localhost:8000/signals/manual \
  -H "Content-Type: application/json" \
  -d '{"symbol":"AAPL","side":"BUY","confidence":0.8,"price":150.0}'
```

### System Monitoring
```bash
# View system status
python scripts/control.py status

# View metrics
curl http://localhost:8000/metrics

# View signal history
curl http://localhost:8000/signals/history
```

### Service Management
```bash
# Control individual services
python scripts/control.py start-redis
python scripts/control.py start-api
python scripts/control.py start-prometheus
python scripts/control.py start-grafana

# Stop all services
python scripts/control.py stop
```

## 📈 Monitoring Setup

### 1. Configure Grafana
1. Open http://localhost:3000
2. Login: admin / trading123
3. Go to Configuration → Data Sources
4. Add Prometheus: http://localhost:9090
5. Create dashboards with these key metrics:

### 2. Key Metrics to Monitor
```promql
# System Health
trading_system_health

# Data Pipeline
trading_stream_length

# Performance
trading_redis_latency_ms
http_request_duration_seconds

# Business Metrics
trading_signals_generated_total
trading_orders_submitted_total
trading_portfolio_value_usd
```

### 3. Recommended Dashboards
- **System Overview**: Health, uptime, error rates
- **Trading Activity**: Signals, orders, fills, P&L
- **Performance**: Latency, throughput, resource usage
- **Data Pipeline**: Stream lengths, processing rates

## 🔍 Troubleshooting

### Common Issues

#### Services Won't Start
```bash
# Check dependencies
python scripts/launcher.py --check-deps

# Check port conflicts
lsof -i :3000,6379,8000,9090

# Check logs
tail -f logs/*.log
```

#### No Trading Signals
- **Market Hours**: Check if market is open (9:30-16:00 ET, Mon-Fri)
- **Strategy Configuration**: Strategies are intentionally restrictive
- **Manual Testing**: Use manual signals to test pipeline

#### Prometheus Connection Issues
```bash
# Verify Prometheus is running
curl http://localhost:9090/-/healthy

# Check configuration
cat prometheus-2.45.0.linux-amd64/prometheus.yml

# Restart Prometheus manually
cd prometheus-2.45.0.linux-amd64
./prometheus --config.file=prometheus.yml
```

#### Performance Issues
- **Metrics Slow**: API metrics endpoint can be slow (6-8s normal)
- **Memory Usage**: Monitor Redis memory usage
- **Rate Limiting**: Check Alpaca API rate limits

### Configuration Tuning

#### Risk Management
```python
# In base.yaml
risk:
  max_daily_loss: 0.05
  max_position_size: 0.10
  stop_loss_pct: 0.02
```

#### Strategy Parameters
```python
# Random strategy probability
strategies:
  - name: "random_50_50"
    parameters:
      probability_threshold: 0.5  # Lower = more signals
```

## 🧪 Testing

### Unit Tests
```bash
# Run test suite
pytest tests/

# Run specific tests
pytest tests/test_strategies.py
pytest tests/test_risk_manager.py
```

### Integration Testing
```bash
# Test full pipeline with manual signals
./scripts/test_pipeline.sh

# Test individual services
python scripts/launcher.py --services data_ingestor --check-deps
```

## 🏗️ Development

### Project Structure
```
algorithmic-trading-platform/
├── apps/                   # Microservices
│   ├── api/               # REST API & monitoring
│   ├── data_ingestor/     # Market data ingestion
│   ├── strategies/        # Trading strategies
│   ├── risk_manager/      # Risk management
│   └── executor/          # Order execution
├── lib/                   # Shared libraries
│   ├── models.py          # Pydantic data models
│   ├── bus.py            # Redis message bus
│   ├── settings.py       # Configuration
│   ├── time_utils.py     # Time/timezone utilities
│   └── deduplication.py  # Idempotency service
├── scripts/              # Management scripts
│   ├── control.py        # Infrastructure control
│   ├── launcher.py       # Service launcher
│   └── setup.sh         # Automated setup
├── configs/              # Configuration files
└── requirements.txt      # Python dependencies
```

### Adding New Strategies
1. Create strategy class in `apps/strategies/`
2. Implement `analyze()` method returning `Signal`
3. Add to strategy list in `apps/strategies/main.py`
4. Configure parameters in `configs/base.yaml`

### Adding New Metrics
1. Define metrics in `apps/api/main.py`
2. Update metrics in background tasks
3. Create Grafana dashboard panels
4. Add alerts if needed

## 📚 API Reference

### REST Endpoints
- `GET /health` - System health check
- `GET /status` - Comprehensive system status
- `GET /metrics` - Prometheus metrics
- `POST /signals/manual` - Create manual signal
- `GET /signals/history` - Signal history
- `GET /portfolio` - Portfolio state
- `GET /positions/{symbol}` - Position details

### WebSocket Endpoints
- `WS /ws/dashboard` - Real-time dashboard updates

## 🔒 Security Notes

- **Paper Trading Only**: System is configured for paper trading
- **Local Access**: Services bind to localhost by default
- **No Authentication**: Development setup - add auth for production
- **API Keys**: Store Alpaca credentials securely in .env

## 🚀 Production Deployment

For production deployment:
1. Enable authentication and TLS
2. Use external Redis cluster
3. Set up proper log aggregation
4. Configure backup strategies
5. Implement proper secret management
6. Set up monitoring alerts
7. Use container orchestration (Docker/Kubernetes)

## 📄 License

[Add your license information here]

## 🤝 Contributing

1. Fork the repository
2. Create feature branch
3. Add tests for new functionality
4. Ensure all tests pass
5. Submit pull request

## 📞 Support

For issues and questions:
- Check troubleshooting section
- Review logs in `logs/` directory
- Monitor Grafana dashboards
- Check GitHub issues