# Algorithmic Trading Platform - Modular Architecture

**Complete event-driven trading system using microservices architecture**

Sistema completo de trading algorítmico implementando la arquitectura modular sugerida por ChatGPT, con separación de responsabilidades, bus de mensajes Redis, y microservicios independientes.

## Architecture Overview

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│  Data Ingestor  │    │   Strategies    │    │  Risk Manager   │
│                 │    │                 │    │                 │
│ Alpaca → Redis  │    │ Bars → Signals  │    │ Signals → Orders│
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       │
         └───────────────┐       │       ┌───────────────┘
                         ▼       ▼       ▼
              ┌─────────────────────────────────┐
              │         Redis Pub/Sub           │
              │       (Message Bus)             │
              └─────────────────────────────────┘
                         │               │
                         ▼               ▼
              ┌─────────────────┐    ┌─────────────────┐
              │    Executor     │    │   FastAPI       │
              │                 │    │                 │
              │ Orders → Alpaca │    │ Monitoring/API  │
              └─────────────────┘    └─────────────────┘
```

## Core Components

### 1. Data Ingestor (`apps/data_ingestor/`)
- Downloads historical and live market data from Alpaca
- Publishes `Bar` objects to Redis channels (`bars.{SYMBOL}`)
- Handles 1-minute granularity for consistent backtest/live alignment

### 2. Strategies (`apps/strategies/`)
- Consumes market bars from Redis
- Calculates technical indicators (SMA, RSI, MACD)
- Publishes `Signal` objects to Redis (`signals.{SYMBOL}`)
- Includes Random 50/50 (infrastructure test) and Smart Technical strategies

### 3. Risk Manager (`apps/risk_manager/`)
- Consumes trading signals
- Applies position sizing, risk limits, portfolio constraints
- Publishes `OrderIntent` objects to Redis (`orders.intent`)
- Implements cooldown periods and daily loss limits

### 4. Executor (`apps/executor/`)
- Consumes order intents
- Executes orders with Alpaca broker API
- Publishes `OrderFill` results to Redis (`orders.fill.{SYMBOL}`)
- Handles both paper and live trading modes

### 5. API (`apps/api/`)
- FastAPI service for monitoring and control
- REST endpoints for system status, portfolio, manual signals
- WebUI available at http://localhost:8000/docs
- Real-time system health monitoring

## Data Models (Pydantic)

### Core Trading Objects
- **Bar**: OHLCV market data with timestamp
- **Signal**: Trading signal with confidence and metadata  
- **OrderIntent**: Risk-validated order ready for execution
- **OrderFill**: Execution result from broker
- **PortfolioState**: Current positions and P&L

### Message Bus Events
All components communicate via Redis Pub/Sub using structured events, enabling loose coupling and easy testing.

## Quick Start

### Prerequisites
- Python 3.8+
- Alpaca Paper Trading account (free)
- Docker (for Redis) or local Redis installation

### 1. Automated Setup
```bash
# Clone and setup everything
git clone <repository>
cd alpaca
chmod +x scripts/setup.sh
./scripts/setup.sh
```

### 2. Configure Alpaca Credentials
```bash
# Edit .env with your Alpaca Paper Trading keys
nano .env
```

### 3. Start the Platform
```bash
# Start all microservices
python scripts/launcher.py

# Or start specific services
python scripts/launcher.py --services data_ingestor strategies
```

### 4. Monitor the System
- **API Dashboard**: http://localhost:8000/docs
- **Redis Monitor**: http://localhost:8081 (if using Docker)
- **System Status**: http://localhost:8000/status

## Configuration

### System Configuration (`configs/base.yaml`)
Complete YAML configuration covering:
- Market data symbols and timeframes
- Strategy parameters and risk limits
- Service settings and monitoring options
- Redis and database connection settings

### Environment Variables (`.env`)
```env
APCA_API_KEY_ID=your_paper_trading_key
APCA_API_SECRET_KEY=your_paper_trading_secret
APCA_API_BASE_URL=https://paper-api.alpaca.markets
SYMBOLS=AAPL,MSFT,GOOGL,TSLA,NVDA
```

## Usage Examples

### Manual Trading Signal
```bash
curl -X POST "http://localhost:8000/signals/manual" \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "AAPL",
    "side": "BUY", 
    "confidence": 0.8,
    "price": 245.50
  }'
```

### Check Portfolio
```bash
curl "http://localhost:8000/portfolio"
```

### System Health
```bash
curl "http://localhost:8000/status"
```

## Development

### Project Structure
```
alpaca/
├── apps/                    # Microservices
│   ├── data_ingestor/      # Market data → Redis
│   ├── strategies/         # Bar analysis → Signals  
│   ├── risk_manager/       # Signal validation → Orders
│   ├── executor/           # Order execution
│   └── api/                # REST API & monitoring
├── lib/                    # Shared libraries
│   ├── models.py           # Pydantic data models
│   └── bus.py              # Redis message bus
├── configs/                # Configuration files
├── scripts/                # Utility scripts
├── old_reference/          # Original monolithic code
├── docker-compose.yml      # Infrastructure services
└── requirements.txt        # Python dependencies
```

### Running Individual Services
```bash
# Each service can run independently
python apps/data_ingestor/main.py
python apps/strategies/main.py  
python apps/risk_manager/main.py
python apps/executor/main.py
python apps/api/main.py
```

### Adding New Strategies
1. Create strategy class in `apps/strategies/main.py`
2. Implement `analyze(symbol, bars)` method
3. Return `Signal` objects with confidence scores
4. Strategy automatically integrated via message bus

## Performance Results

### Backtesting (1-minute data)
- **Smart Strategy**: -2.35% (134 trades executed)
- **Random 50/50**: -51.51% (validates infrastructure)
- **Sharpe Ratio**: -0.036 (timeframe: minutes)
- **Max Drawdown**: 2.39%

### Live Trading Validation
- Successfully executed real orders in Alpaca Paper Trading
- $35,000+ invested across multiple positions
- System demonstrates stable operation with live market data
- All microservices communicating correctly via Redis

## Infrastructure

### Docker Services (Optional)
```bash
# Start infrastructure
docker-compose up -d redis postgres grafana

# Redis GUI
open http://localhost:8081

# Grafana (future monitoring)
open http://localhost:3000
```

### Message Bus Channels
- `bars.{SYMBOL}` - Market data
- `signals.{SYMBOL}` - Trading signals  
- `orders.intent` - Risk-validated orders
- `orders.fill.{SYMBOL}` - Execution results
- `system.*` - Service events and health

## Scaling & Production

### Horizontal Scaling
Each microservice can be scaled independently:
```bash
# Multiple strategy instances
python apps/strategies/main.py &
python apps/strategies/main.py &

# Load balanced executors  
python apps/executor/main.py &
python apps/executor/main.py &
```

### Database Integration
Ready for TimescaleDB/QuestDB integration:
- Historical bar storage
- Signal and execution logging  
- Portfolio state persistence
- Performance analytics

### Cloud Deployment
Architecture supports container deployment:
- Kubernetes-ready microservices
- Redis Cluster for message bus scaling
- External broker API integration
- Monitoring and alerting hooks

## Comparison with Original Implementation

### Before (Monolithic)
- Single `finrl_basic_agent.py` (22KB)
- Mixed responsibilities (data + strategy + execution)
- Difficult to test individual components
- Limited scalability

### After (Modular)
- 5 independent microservices
- Clear separation of concerns  
- Event-driven architecture
- Redis message bus for decoupling
- Testable, scalable, maintainable

## Validation

This implementation successfully demonstrates:
- **Infrastructure Testing**: Random 50/50 strategy validates message flow
- **Real Trading**: Executed actual orders in Alpaca Paper Trading
- **Technical Analysis**: Smart strategy outperformed random baseline
- **Scalability**: Each service runs independently
- **Monitoring**: Complete observability via FastAPI

## Next Steps

1. **Machine Learning Integration**: Add ML-based strategies
2. **Database Layer**: Implement TimescaleDB for historical data
3. **Advanced Risk**: Portfolio optimization and correlation analysis  
4. **Live Trading**: Transition from paper to live execution
5. **Monitoring**: Grafana dashboards and alerting
6. **Backtesting Engine**: Dedicated backtesting service

## License

MIT License - Educational and research use

## Disclaimer

This software is for educational purposes. Trading involves financial risk. Always use paper trading before live implementation.

---

**Built following ChatGPT's microservices architecture recommendations**  
Demonstrates complete event-driven trading system with proper separation of concerns.